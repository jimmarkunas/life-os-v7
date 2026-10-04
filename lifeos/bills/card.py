"""Render the saved Bills snapshot into V7's one "Bills: This Week" callout on the Daily Report (D116).

Bill Tracker -> bills-paid -> bills-snapshot -> bills-card. This stage only reads the accepted private snapshot and writes the one callout it owns;
it never reads or writes the Bill Tracker, and it never advances a Due Date (the Paid processor owns that). The supporting window is calculated at
render time from the America/Chicago date, [today, today + 6]; no calendar date is stored anywhere. Counts-only logs, fixed error codes."""
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.platform import db, router
from lifeos.platform.notion_client import Client, NotionError, rich_text
from . import snapshot as bills_snapshot, state

TZ = ZoneInfo(bills_snapshot.LOCAL_TZ)
STALE_HOURS = 3
WINDOW_DAYS = 7                                  # [today, today + 6]
CARD_TITLE = router.BILLS_REGION
MODULE = router.OWNERS[CARD_TITLE]
HEADINGS = ("heading_2", "heading_3", "heading_4")
# D117: the card owns only the TEXT in the callout (the summary line). The linked Bills table inside it is Jim's interactive view (it is where he ticks
# Paid), so it is never removed or rewritten: only these plain text kinds are replaced; every other block stays exactly where it is.
TEXT_KINDS = ("paragraph", "bulleted_list_item", "numbered_list_item", "to_do")


class CardError(NotionError):
    """Fixed codes only; never expose bill names, amounts or IDs."""


def _plain(block):
    inner = block.get(block.get("type")) or {}
    return "".join((part.get("plain_text") or (part.get("text") or {}).get("content") or "")
                   for part in inner.get("rich_text") or [] if isinstance(part, dict))


def _children(client, block_id):
    results, cursor, seen = [], None, set()
    for _ in range(50):
        path = f"/blocks/{quote(block_id, safe='')}/children?page_size=100"
        if cursor:
            if cursor in seen:
                raise CardError("BILLS_NOTION_PAGINATION_INVALID")
            seen.add(cursor)
            path += f"&start_cursor={quote(cursor, safe='')}"
        page = client.call("GET", path)
        if (not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool)
                or any(not isinstance(block, dict) or not isinstance(block.get("id"), str) for block in page["results"])):
            raise CardError("BILLS_NOTION_PAGE_INCOMPLETE")
        results.extend(page["results"])
        if not page["has_more"]:
            return results
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise CardError("BILLS_NOTION_PAGE_INCOMPLETE")
    raise CardError("BILLS_NOTION_PAGINATION_INCOMPLETE")


def _same_id(left, right):
    return isinstance(left, str) and isinstance(right, str) and left.replace("-", "").lower() == right.replace("-", "").lower()


VOLATILE = {"last_edited_time", "last_edited_by", "request_id", "expiry_time"}      # metadata that changes without the content changing


def _stable(node):
    if isinstance(node, dict):
        out = {k: _stable(v) for k, v in node.items() if k not in VOLATILE}
        if out.get("type") == "file" or "expiry_time" in node:
            out.pop("url", None)
        return out
    if isinstance(node, list):
        return [_stable(v) for v in node]
    return node


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:16]


def _tree(client, block):
    node = deepcopy(block)
    if block.get("has_children"):
        node["region_children"] = [_tree(client, child) for child in _children(client, block["id"])]
    return node


def _region_digest(client, block_id):
    """One hash per field of a protected callout and one per child block, so a change can be located (names only)."""
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(meta, dict) or meta.get("type") != "callout" or not _same_id(meta.get("id"), block_id):
        raise CardError("BILLS_PROTECTED_REGION_UNAVAILABLE")
    tree = _stable(_tree(client, meta))
    kids = tree.pop("region_children", [])
    return {"fields": {key: _hash(value) for key, value in tree.items()}, "children": [[child.get("type"), _hash(child)] for child in kids]}


def _changed(before, after):
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"digest": "missing"}
    fields = sorted(k for k in set(before["fields"]) | set(after["fields"]) if before["fields"].get(k) != after["fields"].get(k))
    kids = [i for i in range(max(len(before["children"]), len(after["children"])))
            if (before["children"][i:i + 1] or [None]) != (after["children"][i:i + 1] or [None])]
    return {"fields": fields, "children": kids}


def _insert_after(client, block_id, after_id, blocks, after_write):
    """Insert the new text right under the heading, so the table below it keeps its place."""
    try:
        result = client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": blocks, "after": after_id})
    finally:
        after_write()
    if not isinstance(result, dict) or not isinstance(result.get("results"), list) or len(result["results"]) != len(blocks):
        raise CardError("BILLS_CARD_APPEND_MISMATCH")


def _owned(blocks):
    return bool(blocks) and blocks[0].get("type") in HEADINGS and _plain(blocks[0]).strip() == CARD_TITLE


def _now(now):
    now = now or datetime.now(TZ)
    return now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)


def _instant(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def _valid(saved):
    """A snapshot is usable only when it is whole. An empty or malformed one is never shown as 'no bills'."""
    if not isinstance(saved, dict) or saved.get("schema") != bills_snapshot.SCHEMA_V or not isinstance(saved.get("rows"), list) or not saved["rows"]:
        raise CardError("BILLS_CARD_SNAPSHOT_INVALID")
    for row in saved["rows"]:
        if (not isinstance(row, dict) or not isinstance(row.get("Name"), str) or not row["Name"].strip()
                or not isinstance(row.get("Paid"), bool) or any(field not in row for field in bills_snapshot.CRITICAL)):
            raise CardError("BILLS_CARD_SNAPSHOT_INVALID")
    try:
        _instant(saved["taken_at"])
    except (KeyError, TypeError, ValueError):
        raise CardError("BILLS_CARD_SNAPSHOT_INVALID") from None
    return saved


def _amount(row):
    """The known amount of a bill, or None. Missing is never zero."""
    for field in ("Costs per Cycle", "Last Observed Amount"):
        value = row.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _money(value):
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _short(value):
    day = state._day(value)
    return f"{day.strftime('%b')} {day.day}" if day else "date unset"


def _window(rows, today):
    """Active unpaid recurring bills whose Next Due falls in [today, today + 6], from the state classifier's buckets."""
    buckets = state.classify(rows, today)
    last = today + timedelta(days=WINDOW_DAYS - 1)
    due = [row for row in buckets["due_today"] + buckets["due_next_7_days"] if (day := state._day(row.get("Next Due"))) is not None and day <= last]
    overdue = sorted(buckets["stale_due"], key=lambda row: (state._day(row["Due Date"]), row["Name"]))
    late = {row["page_id"] for row in overdue if "page_id" in row}
    due = sorted((row for row in due if row.get("page_id") not in late), key=lambda row: (state._day(row["Next Due"]), row["Name"]))   # an overdue bill is listed once, as overdue
    return overdue, due


def _status(saved, stale):
    taken = _instant(saved["taken_at"])
    return f"STALE · last accepted {taken.strftime('%b %-d %-I:%M %p')} CT" if stale else f"Updated {taken.strftime('%-I:%M %p')} CT"


def _summary(saved, stale, overdue, due):
    known = [amount for amount in (_amount(row) for row in due) if amount is not None]
    parts = [_status(saved, stale), f"{len(due)} due in next {WINDOW_DAYS} days"]
    if due:
        parts.append(f"{_money(sum(known))} known")
        if len(known) < len(due):
            parts.append(f"{len(due) - len(known)} amount missing")
    if overdue:
        parts.append(f"{len(overdue)} overdue")
    return " · ".join(parts)


def _text(content, url=None):
    parts = rich_text(content)
    if url and parts:
        parts[0]["text"]["link"] = {"url": url}
    return parts


def _paragraph(content, url_label=None, url=None):
    parts = _text(content)
    if url:
        parts += _text(" · ") + _text(url_label, url)
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": parts}}


def _bullet(content):
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _text(content)}}


def _line(row, field):
    amount = _amount(row)
    return f"{row['Name']} · {'due' if field == 'Due Date' else 'next due'} {_short(row.get(field))} · {_money(amount) if amount is not None else 'amount missing'}"


def render(saved, stale=False, now=None, tracker_url=None):
    """-> (blocks, counts). Pure: the window comes from `now` in America/Chicago, the rows from the saved snapshot."""
    now = _now(now)
    saved = _valid(saved)
    overdue, due = _window(saved["rows"], now.date())
    blocks = [_paragraph(_summary(saved, stale, overdue, due), "Bill Tracker", tracker_url)]
    counts = {"rows": len(saved["rows"]), "active": sum(row.get("Status") == "Active" for row in saved["rows"]), "overdue": len(overdue),
              "due_7d": len(due), "amount_missing": sum(_amount(row) is None for row in overdue + due),
              "status": "stale" if stale else "fresh", "blocks_written": 0}
    return blocks, counts


def _protected(client, environ):
    """Digests of the V7 regions this stage must never change (Calendar, JIRA). Both ids must be configured."""
    ids = {router.JIRA_REGION: (environ.get("JIRA_CARD_BLOCK_ID") or "").strip(), router.CALENDAR_REGION: (environ.get("CALENDAR_CARD_BLOCK_ID") or "").strip()}
    if not all(ids.values()):
        raise CardError("BILLS_PROTECTED_REGION_UNAVAILABLE")
    try:
        return {region: _region_digest(client, block_id) for region, block_id in ids.items()}
    except CardError:
        raise
    except Exception:
        raise CardError("BILLS_PROTECTED_REGION_UNAVAILABLE") from None


def _require_intact(client, environ, before):
    after = _protected(client, environ)
    if not router.protected_intact(MODULE, before, after):
        print("bills: protected region changed", json.dumps({r: _changed(before.get(r), after.get(r)) for r in before if before.get(r) != after.get(r)}))
        raise CardError("BILLS_PROTECTED_REGION_CHANGED")


def _target(client, block_id):
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(meta, dict) or meta.get("type") != "callout" or not _same_id(meta.get("id"), block_id):
        raise CardError("BILLS_CARD_NOT_OWNED")
    blocks = _children(client, block_id)
    if not _owned(blocks):
        raise CardError("BILLS_CARD_NOT_OWNED")
    try:
        router.check_write(MODULE, [CARD_TITLE])
    except router.RouterError:
        raise CardError("BILLS_CARD_NOT_OWNED") from None
    return blocks


def _replaceable(block):
    return block.get("type") in TEXT_KINDS


def _tracker_link(existing):
    """Keep the Bill Tracker link the callout already carries (never invented here)."""
    for block in existing[1:]:
        for part in (block.get(block.get("type")) or {}).get("rich_text") or []:
            url = ((part.get("text") or {}).get("link") or {}).get("url") or part.get("href")
            if isinstance(part, dict) and (part.get("plain_text") or "").strip() == "Bill Tracker" and isinstance(url, str) and url.startswith("https://"):
                return url
    return None


def _client(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def run(limit, live, environ=os.environ, client=None, now=None, connect=None):
    block_id = (environ.get("BILLS_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        return {"rows": 0, "active": 0, "overdue": 0, "due_7d": 0, "amount_missing": 0, "blocks_written": 0, "status": "not_configured"}
    try:
        with (connect or db.connect)() as connection:
            saved = bills_snapshot.load(connection)
    except NotionError:
        raise
    except Exception:
        raise CardError("BILLS_CARD_STORE_FAILED") from None
    if saved is None:
        raise CardError("BILLS_CARD_SNAPSHOT_MISSING")
    now = _now(now)
    _valid(saved)
    stale = now - _instant(saved["taken_at"]) > timedelta(hours=STALE_HOURS)
    client = client or _client(environ)
    existing = _target(client, block_id)
    blocks, counts = render(saved, stale, now, _tracker_link(existing))
    if not live:
        return counts
    old_text = [block for block in existing[1:] if _replaceable(block)]
    kept = [block["id"] for block in existing[1:] if not _replaceable(block)]            # the interactive table and anything else stays untouched, in order
    before = _protected(client, environ)
    try:
        router.check_write(MODULE, [CARD_TITLE])
    except router.RouterError:
        raise CardError("BILLS_CARD_NOT_OWNED") from None
    _insert_after(client, block_id, existing[0]["id"], blocks, lambda: _require_intact(client, environ, before))   # new text first: a failed write leaves the old text
    removed = 0
    for old in old_text:
        try:
            router.check_write(MODULE, [CARD_TITLE])
        except router.RouterError:
            raise CardError("BILLS_CARD_NOT_OWNED") from None
        try:
            client.call("DELETE", f"/blocks/{quote(old['id'], safe='')}")
            removed += 1
        except NotionError as error:
            if str(error) != "NOTION_HTTP_404":
                raise
        finally:
            _require_intact(client, environ, before)
    after = _children(client, block_id)                   # authoritative read-back of the configured Bills region
    _require_intact(client, environ, before)
    if (not _owned(after) or after[0].get("id") != existing[0].get("id") or len(after) != 1 + len(blocks) + len(kept)
            or [_plain(b) for b in after[1:1 + len(blocks)]] != [_plain(b) for b in blocks] or [b.get("id") for b in after[1 + len(blocks):]] != kept):
        raise CardError("BILLS_CARD_VERIFY_FAILED")
    counts["blocks_written"], counts["removed"] = len(blocks), removed
    return counts
