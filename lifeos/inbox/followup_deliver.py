"""Delivers Followup open loops to the Agent Inbox, one row per loop, through the `send_to_jim` path.

A loop is never a copy of a Jira ticket, Todoist task or event: its Link points at the evidence. The row key is the
loop's thread id (`followup-<id>`), so a rerun never adds a second row. Only ACTIVE loops get a row; Review Needed
is never sent. A row the user resolved (Done, Dismissed, Promoted) is final. A loop the store closed on strong
proof marks its still-open row Done; nothing else sets Status. Counts and fixed reason words only reach the log.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lifeos.inbox import agent_inbox
from lifeos.inbox.model import InboxError
from lifeos.platform.notion_client import NotionError

SENDER = "Followup"
KEY_PREFIX = "followup-"
LOCAL = ZoneInfo("America/Chicago")
# needs_action_today is Now (overdue) or Today, decided from the checkpoint
STATE_URGENCY = {"waiting_aging": "This week", "upcoming": "FYI"}
STATE_WORDS = {"needs_action_today": "Needs action", "waiting_aging": "Waiting", "upcoming": "Upcoming"}
JIM_DECIDED = frozenset({"Done", "Dismissed", "Promoted"})
OPEN = frozenset({"New", "Snoozed"})


def key_of(thread_id):
    return (KEY_PREFIX + str(thread_id))[:agent_inbox.KEY_MAX]


def _due_date(text):
    try:
        parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(LOCAL).date() if parsed.tzinfo else parsed.date()


def urgency_of(state, checkpoint, now):
    if state != "needs_action_today":
        return STATE_URGENCY.get(state, "FYI")
    due = _due_date(checkpoint) if checkpoint else None
    return "Now" if due and due < now.astimezone(LOCAL).date() else "Today"


def item_of(payload, thread, now):
    state = thread.get("state")
    details = f"{STATE_WORDS.get(state, 'Open loop')}. {str(thread.get('notes') or '')[:200]}".strip()
    return {"from": SENDER, "subject": str(thread.get("title") or "")[:agent_inbox.SUBJECT_MAX], "urgency": urgency_of(state, thread.get("next_checkpoint"), now),
            "workspace": str(payload.get("workspace") or "").title(), "due": thread.get("next_checkpoint") or "", "link": str(thread.get("url") or ""), "details": details, "key": key_of(thread.get("id"))}


def _plain(prop):
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        return "".join(piece.get("plain_text", "") for piece in value or ())
    if kind == "select":
        return (value or {}).get("name", "")
    if kind == "date":
        return (value or {}).get("start") or ""
    return value if value is not None else ""


def _existing(client, source, key):
    found = client.query_data_source(source, {"page_size": 2, "filter": {"and": [{"property": "Key", "rich_text": {"equals": key}}, {"property": "From", "rich_text": {"equals": SENDER}}]}}).get("results", [])
    return found[0] if found else None


def _moment(text):
    """A due value as a comparable (aware UTC datetime or date), never a string."""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return str(text)
    return parsed.astimezone(timezone.utc) if (parsed.tzinfo and "T" in str(text)) else parsed.date()


def _same(current, item):
    due_ok = _moment(current["Due"]) == _moment(item["due"]) if item["due_given"] else bool(current["Due"] or not item["due"])
    return (current["Subject"], current["Urgency"], current["Link"]) == (item["subject"], item["urgency"], item["link"]) and due_ok


def _current(page):
    p = page.get("properties", {})
    return {"Subject": _plain(p.get("Subject", {})), "Urgency": _plain(p.get("Urgency", {})), "Due": _plain(p.get("Due", {})), "Link": _plain(p.get("Link", {}))}


def _props(item, current_due=""):
    full = agent_inbox.properties(item)
    keep = {name: full[name] for name in ("Subject", "Urgency", "Details") if name in full}
    if item["due_given"] or not current_due:
        keep["Due"] = full.get("Due", {"date": None})
    keep["Link"] = full.get("Link", {"url": None})
    return keep


def deliver(payloads, now=None, environ=None, client=None):
    """Returns counts: created, updated, unchanged, kept (the user already decided the row), done (closed loops),
    skipped (the validator refused the item), failed. Never raises.
    """
    out = {"created": 0, "updated": 0, "unchanged": 0, "kept": 0, "done": 0, "skipped": 0, "failed": 0}
    now = now or datetime.now(timezone.utc)
    try:
        if client is None:
            from lifeos.inbox import send_to_jim                                          # noqa: PLC0415
            client = send_to_jim._client(environ if environ is not None else {})
        source = agent_inbox.locate(client)
    except (InboxError, NotionError) as error:
        print(f"followup-inbox failed {getattr(error, 'reason', str(error).lower()[:40])}")
        return {**out, "failed": 1}
    for payload in payloads:
        if payload.get("workspace") not in ("personal", "bytalos"):
            continue
        for thread in payload.get("threads") or ():
            try:
                item = agent_inbox.normalize(item_of(payload, thread, now), now)
                page = _existing(client, source, item["key"])
                if page is None:
                    out["created"] += agent_inbox.send(client, item, now=now, source=source).get("created", 0)
                    continue
                if _plain(page.get("properties", {}).get("Status", {})) not in OPEN:
                    out["kept"] += 1
                elif _same(_current(page), item):
                    out["unchanged"] += 1
                else:
                    client.call_once("PATCH", f"/pages/{page['id']}", {"properties": _props(item, _current(page)["Due"])})
                    agent_inbox.replace_body(client, page["id"], item["details"])
                    out["updated"] += 1
            except InboxError as error:
                out["skipped"] += 1
                print(f"followup-inbox skipped {error.reason}")
            except NotionError as error:
                out["failed"] += 1
                print(f"followup-inbox failed notion_{str(error).lower()[:30]}")
        for thread_id in payload.get("closed") or ():
            try:
                page = _existing(client, source, key_of(thread_id))
                if page and _plain(page.get("properties", {}).get("Status", {})) in OPEN:
                    client.call_once("PATCH", f"/pages/{page['id']}", {"properties": {"Status": {"select": {"name": "Done"}}}})
                    out["done"] += 1
            except NotionError as error:
                out["failed"] += 1
                print(f"followup-inbox failed notion_{str(error).lower()[:30]}")
    try:
        out["filled"] = agent_inbox.fill_missing_due(client, source, now).get("filled", 0)
    except NotionError as error:
        out["failed"] += 1
        print(f"followup-inbox failed notion_{str(error).lower()[:30]}")
    return out


__all__ = ["deliver", "item_of", "key_of", "urgency_of"]
