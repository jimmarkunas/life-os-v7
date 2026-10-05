"""Attention (D127): Gmail label and Outlook category Jim controls -> the canonical Notion Attention data source. Counts and fixed codes only.

Flow: read the current and prior week completely -> read each mailbox source completely -> admit by policy -> plan -> write -> read back.
Fail closed: an unreadable Notion week, a schema mismatch or an incomplete listing writes nothing for what it touches; a failed source only skips that source
(its items are never deactivated) and the stage ends DEGRADED. Done is never written. Nothing is ever deleted."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo
import os

from lifeos.attention import policy, reconcile
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.notion_client import Client, NotionError, rich_text
from lifeos.platform.outlook import Outlook, OutlookError

SOURCE_ID = "5cab416c-b1df-43c2-abcc-b98df6656d41"          # the canonical Attention data source (the one the Daily Report view links)
GMAIL_LABEL, GMAIL_QUERY = "LifeOS/Attention", "label:LifeOS-Attention"
OUTLOOK_CATEGORIES = ("LifeOS Attention", "LIFE OS Attention", "Life OS Attention")          # the Outlook category (any of these spellings): read-only for V7, the message stays in the inbox
SCHEMA = {"Item": "title", "Category": "select", "Done": "checkbox", "Active": "checkbox", "Medium": "rich_text", "Source URL": "url", "Week Ending": "date", "Received": "date"}


class AttentionError(NotionError):
    """Fixed codes only; never mail or property values."""


def _today(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/Chicago")).date()


def _chicago_date(stamp):
    """The Chicago calendar date (YYYY-MM-DD) of an ISO timestamp, or "" when it is missing or unreadable."""
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return ""
    moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo("America/Chicago")).date().isoformat()


def _text(prop):
    items = (prop or {}).get(prop.get("type")) if isinstance(prop, dict) else None
    return "".join(i.get("plain_text") or (i.get("text") or {}).get("content", "") for i in items or [] if isinstance(i, dict))


def _row(page):
    props = page.get("properties") if isinstance(page, dict) else None
    try:
        week = ((props["Week Ending"].get("date") or {}).get("start") or "")[:10]
        return {"id": page["id"], "item": _text(props["Item"]), "category": ((props["Category"].get("select") or {}).get("name")),
                "done": bool(props["Done"]["checkbox"]), "active": bool(props["Active"]["checkbox"]), "medium": _text(props["Medium"]),
                "url": props["Source URL"].get("url") or "", "week": week, "received": ((props["Received"].get("date") or {}).get("start") or "")[:10],
                "linked": any(isinstance(i, dict) and (i.get("href") or (i.get("text") or {}).get("link")) for i in props["Item"].get("title") or [])}
    except (KeyError, TypeError, AttributeError):
        raise AttentionError("ATTENTION_ROW_INVALID") from None


def _schema(notion, source_id):
    try:
        props = notion.call("GET", f"/data_sources/{quote(source_id, safe='')}").get("properties")
    except NotionError as error:                                    # the fixed Notion code (NOTION_HTTP_404 means the integration cannot see the data source)
        raise AttentionError(f"ATTENTION_SCHEMA_READ_FAILED:{error}") from None
    if not isinstance(props, dict) or any((props.get(n) or {}).get("type") != t for n, t in SCHEMA.items()):
        raise AttentionError("ATTENTION_SCHEMA_MISMATCH")
    names = {o.get("name") for o in (props["Category"].get("select") or {}).get("options", []) if isinstance(o, dict)}
    if not {"Security", "Account", "Deadline", "Admin"} <= names:
        raise AttentionError("ATTENTION_SCHEMA_MISMATCH")


def read_week(notion, source_id, week):
    """Every row whose Week Ending is `week`. Complete or an error: a partial read could make a missing row look absent."""
    rows, cursor = [], None
    for _ in range(50):
        body = {"page_size": 100, "filter": {"property": "Week Ending", "date": {"equals": str(week)}}}
        if cursor:
            body["start_cursor"] = cursor
        try:
            page = notion.query_data_source(source_id, body)
        except NotionError:
            raise AttentionError("ATTENTION_READ_FAILED") from None
        rows += [_row(p) for p in page.get("results", [])]
        if not page.get("has_more"):
            return rows
        cursor = page.get("next_cursor")
        if not cursor:
            raise AttentionError("ATTENTION_READ_INCOMPLETE")
    raise AttentionError("ATTENTION_READ_INCOMPLETE")


def _linked_title(item, url):
    """The row title; when the mail has an address the whole title is a link to it, so the row opens the email instead of an empty page."""
    parts = rich_text(item)
    if url:
        for part in parts:
            part["text"]["link"] = {"url": url}
    return parts


def _open_block(url):
    """The one line in a row's page: a link to the email (the mail itself is never copied into Notion)."""
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Open the email", "link": {"url": url}}}]}}


def _props(c):
    props = {"Item": {"title": _linked_title(c["item"], c["url"])}, "Category": {"select": {"name": c["category"]}}, "Active": {"checkbox": True}, "Done": {"checkbox": False},
             "Medium": {"rich_text": rich_text(c["medium"])}, "Week Ending": {"date": {"start": c["week"]}}}
    if c.get("received"):
        props["Received"] = {"date": {"start": c["received"]}}
    if c["url"]:
        props["Source URL"] = {"url": c["url"]}
    return props


def _gmail_source(gmail, limit, live):
    """-> (candidates [{sender, subject, url, medium}], present medium tags, labeled ids). A missing label is created only when live."""
    try:
        label = gmail.label_id(GMAIL_LABEL, create=live)
    except GmailError as error:
        if "label not found" in str(error):
            return [], set(), None                                  # dry run before the label exists: nothing labelled, nothing to prove
        raise
    ids = gmail.list_ids_complete(GMAIL_QUERY, limit)
    out = []
    for message_id in ids:
        record = gmail.message_record(message_id)
        out.append({"sender": record["sender"], "subject": record["subject"], "url": f"https://mail.google.com/mail/u/0/#all/{message_id}", "medium": f"Gmail:{message_id}",
                    "received": _chicago_date(record.get("received_at"))})
    return out, {c["medium"] for c in out}, label


def _gmail_proof(gmail, label, medium, labeled):
    """present / absent / unknown for one stored Gmail source tag: absent only when the message still exists and no longer carries the label."""
    if medium in labeled:
        return "present"
    try:
        return "absent" if label and label not in gmail.message_labels(medium.split(":", 1)[1]) else "unknown"
    except GmailError:
        return "unknown"


def _outlook_source(client, account, limit, live):
    out = []
    for message in client.messages_in_category(OUTLOOK_CATEGORIES, limit):
        sender = ((message.get("from") or {}).get("emailAddress") or {}).get("address") or ""
        out.append({"sender": sender, "subject": message.get("subject") or "", "url": "https://outlook.office.com/mail/id/" + quote(message["id"], safe=""),
                    "medium": f"Outlook:{account}:{message['id']}", "received": _chicago_date(message.get("receivedDateTime"))})
    return out, {c["medium"] for c in out}, OUTLOOK_CATEGORIES


def _outlook_proof(client, category, medium, labeled):
    """present / absent / unknown: absent only when the message is still readable and no longer carries the category."""
    if medium in labeled:
        return "present"
    try:
        found = client.get(f"/me/messages/{quote(medium.split(':', 2)[2], safe='')}", {"$select": "id,categories"})
        categories = found.get("categories")
        wanted = {"".join(n.lower().split()) for n in ([category] if isinstance(category, str) else category)}
        return "absent" if isinstance(categories, list) and not any("".join(str(c).lower().split()) in wanted for c in categories) else "unknown"
    except OutlookError:
        return "unknown"


def run(limit, live, environ=os.environ, gmail=None, notion=None, outlook_clients=None, now=None):
    counts = {"gmail": 0, "outlook": 0, "outlook_accounts": 0, "admitted": 0, "owned_elsewhere": 0, "admitted_other": 0, "created": 0, "carried": 0, "deactivated": 0, "reactivated": 0,
              "reused": 0, "ambiguous": 0, "skipped_done": 0, "sources_failed": 0, "verified": False, "why": []}
    source_id = (environ.get("NOTION_ATTENTION_DATA_SOURCE_ID") or SOURCE_ID).strip().replace("collection://", "")
    if notion is None:
        notion = Client({**environ, "NOTION_ATTENTION_DATA_SOURCE_ID": source_id}, token_name="NOTION_JIRA_TOKEN", source_name="NOTION_ATTENTION_DATA_SOURCE_ID")
    today = _today(now)
    this_week = reconcile.week_ending(today)
    _schema(notion, source_id)
    rows = read_week(notion, source_id, this_week) + read_week(notion, source_id, this_week - timedelta(days=7))
    candidates, labeled, proofs, failed = [], set(), {}, 0
    checkers = []                                                    # [(medium prefix, callable(medium) -> proof)]
    try:
        gmail = gmail or Gmail.from_env()
        found, present, label = _gmail_source(gmail, limit, live)
        counts["gmail"], labeled = len(found), labeled | present
        candidates += found
        checkers.append(("Gmail:", lambda m, g=gmail, l=label, p=present: _gmail_proof(g, l, m, p)))
    except (GmailError, KeyError) as error:
        failed += 1
        counts["why"].append("gmail:" + str(error)[:90])
    outlook_list = list(outlook_clients if outlook_clients is not None else _outlook_clients(environ))
    counts["outlook_accounts"] = len(outlook_list)                      # 0 means no Outlook account is signed in to V7 (or no client id): nothing to read
    for account, client, connect_error in outlook_list:
        if connect_error:
            failed += 1
            continue
        try:
            found, present, folder = _outlook_source(client, account, limit, live)
            counts["outlook"] += len(found)
            labeled |= present
            candidates += found
            checkers.append((f"Outlook:{account}:", lambda m, c=client, f=folder, p=present: _outlook_proof(c, f, m, p)))
        except OutlookError as error:
            failed += 1
            counts["why"].append(f"outlook@{account}:" + str(error)[:90])
    counts["sources_failed"] = failed
    admitted = []
    for c in candidates:
        verdict, detail = policy.decide(c["sender"], c["subject"])
        if verdict == "ADMIT":
            admitted.append({"category": detail, "item": policy.item_text(c["subject"]), "url": c["url"], "medium": c["medium"], "received": c.get("received", "")})
        elif verdict == "OWNED":
            counts["owned_elsewhere"] += 1
        if verdict == "ADMIT" and detail == "Other":
            counts["admitted_other"] += 1
    counts["admitted"] = len(admitted)
    for row in rows:                                                 # proof of a stored source only from a source that was read completely this run
        medium = row["medium"]
        for prefix, check in checkers:
            if medium.startswith(prefix):
                proofs[medium] = check(medium)
    for c in admitted:
        proofs[c["medium"]] = "present"
    plan = reconcile.plan(admitted, rows, today, proofs)
    dates = {c["medium"]: c["received"] for c in admitted if c.get("received")}
    plan["backfill"] = [(r["id"], dates[r["medium"]]) for r in rows if not r["received"] and r["medium"] in dates]          # rows written before the column existed
    counts["backfilled"] = len(plan["backfill"])
    plan["upgrade"] = [r for r in rows if r["url"] and not r["linked"]]                                                              # rows written before the title was a link
    counts["linked"] = len(plan["upgrade"])
    for name in ("ambiguous", "reused", "skipped_done", "carried"):
        counts[name] = plan[name]
    if not live:
        counts.update(created=len(plan["create"]), deactivated=len(plan["deactivate"]), reactivated=len(plan["reactivate"]))
    else:
        done_before = {r["id"]: r["done"] for r in rows}
        for create in plan["create"]:
            body = {"parent": {"type": "data_source_id", "data_source_id": source_id}, "properties": _props(create)}
            if create["url"]:
                body["children"] = [_open_block(create["url"])]
            notion.call_once("POST", "/pages", body)
            counts["created"] += 1
        for row_id in plan["deactivate"]:
            notion.update_page_properties(row_id, {"Active": {"checkbox": False}})
            counts["deactivated"] += 1
        for row_id, received in plan["backfill"]:
            notion.update_page_properties(row_id, {"Received": {"date": {"start": received}}})
        for r in plan["upgrade"]:
            notion.update_page_properties(r["id"], {"Item": {"title": _linked_title(r["item"], r["url"])}})
            notion.call("PATCH", f"/blocks/{r['id']}/children", {"children": [_open_block(r["url"])]})
        for row_id in plan["reactivate"]:
            notion.update_page_properties(row_id, {"Active": {"checkbox": True}})
            counts["reactivated"] += 1
        after = read_week(notion, source_id, this_week)               # authoritative read-back from the canonical source
        seen = {}
        for r in after:
            seen.setdefault(reconcile.key(r["week"], r["category"], r["item"]), []).append(r)
        ok = all(len(seen.get(reconcile.key(c["week"], c["category"], c["item"]), [])) == 1 for c in plan["create"])
        ok = ok and all(not r["active"] for r in after if r["id"] in plan["deactivate"])
        ok = ok and all(r["done"] == done_before[r["id"]] for r in after if r["id"] in done_before)
        counts["verified"] = ok
        if not ok:
            raise AttentionError("ATTENTION_READBACK_MISMATCH")
    new_items = counts["created"] - counts["carried"]
    if live and new_items > 0:                                          # counts only: no sender, subject or link ever leaves the private database
        from lifeos.platform import alerts                              # noqa: PLC0415
        counts["notified"] = alerts.ntfy((environ.get("NTFY_TOPIC") or "").strip(), "LIFE OS Attention",
                                         f"{new_items} new item{'s' if new_items != 1 else ''} need{'' if new_items != 1 else 's'} your attention", alerts.PAGE)
    if failed:
        raise AttentionError(f"ATTENTION_DEGRADED:{failed}_source(s)_unread:" + ";".join(counts["why"]))
    return counts


def _outlook_clients(environ):
    """[(account label, client, error flag)] for every signed-in Outlook account; tokens live in the private database."""
    out = []
    client_id = (environ.get("OUTLOOK_CLIENT_ID") or "").strip()
    if not client_id:
        return out
    from lifeos.platform import outlook_tokens as store                # noqa: PLC0415
    from lifeos.platform import db                                    # noqa: PLC0415
    with db.connect() as connection:
        store.ensure_schema(connection)
        for label in store.accounts(connection):
            try:
                out.append((label, Outlook(client_id, store.load(connection, label), lambda new, label=label: _save(label, new)), False))
            except OutlookError:
                out.append((label, None, True))
    return out


def _save(label, token):
    from lifeos.platform import outlook_tokens as store                # noqa: PLC0415
    from lifeos.platform import db                                    # noqa: PLC0415
    with db.connect() as connection:
        store.save(connection, label, token)
