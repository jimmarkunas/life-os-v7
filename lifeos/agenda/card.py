"""Render the saved Agenda snapshot into V7's one Calendar callout."""
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.platform import db, router
from lifeos.platform.notion_client import Client, NotionError, rich_text
from . import snapshot as agenda_snapshot

LOCAL_TZ = "America/Chicago"
TZ = ZoneInfo(LOCAL_TZ)
STALE_HOURS = 3
CARD_TITLE = router.CALENDAR_REGION
MODULE = router.OWNERS[CARD_TITLE]
STATUS_PREFIXES = ("Updated ", "STALE · last accepted ")


class CardError(NotionError):
    """Fixed codes only; never expose calendar content, IDs, or URLs."""


def _plain(block):
    inner = block.get(block.get("type")) or {}
    return "".join((part.get("plain_text") or (part.get("text") or {}).get("content") or "")
                   for part in inner.get("rich_text") or [] if isinstance(part, dict))


def _owned(blocks):
    return bool(blocks) and blocks[0].get("type") == "heading_4" and _plain(blocks[0]).strip() == CARD_TITLE


def _children(client, block_id):
    results, cursor, seen = [], None, set()
    for _ in range(50):
        path = f"/blocks/{quote(block_id, safe='')}/children?page_size=100"
        if cursor:
            if cursor in seen:
                raise CardError("AGENDA_NOTION_PAGINATION_INVALID")
            seen.add(cursor)
            path += f"&start_cursor={quote(cursor, safe='')}"
        page = client.call("GET", path)
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise CardError("AGENDA_NOTION_PAGE_INCOMPLETE")
        if any(not isinstance(block, dict) or not isinstance(block.get("id"), str) for block in page["results"]):
            raise CardError("AGENDA_NOTION_PAGE_INCOMPLETE")
        results.extend(page["results"])
        if not page["has_more"]:
            return results
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise CardError("AGENDA_NOTION_PAGE_INCOMPLETE")
    raise CardError("AGENDA_NOTION_PAGINATION_INCOMPLETE")


def _tree(client, block):
    node = deepcopy(block)
    if block.get("has_children"):
        node["agenda_children"] = [_tree(client, child) for child in _children(client, block["id"])]
    return node


def _page_regions(client, page_id):
    regions, digests = [], {}
    owned_trees = {}
    for root in _children(client, page_id):
        if root.get("type") != "callout":
            continue
        tree = _tree(client, root)
        children = tree.get("agenda_children") or []
        heading = children[0] if children else {}
        name = _plain(heading).strip() if heading.get("type") == "heading_4" else ""
        if not name:
            continue
        regions.append((name, root["id"]))
        if name in router.OWNERS:
            owned_trees.setdefault(name, []).append(tree)
    for name, trees in owned_trees.items():
        encoded = json.dumps(trees, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digests[name] = hashlib.sha256(encoded.encode()).hexdigest()
    return regions, digests


def _target(client, block_id):
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(meta, dict) or meta.get("type") != "callout":
        raise CardError("AGENDA_CARD_NOT_OWNED")
    parent = meta.get("parent") or {}
    page_id = parent.get("page_id") if parent.get("type") == "page_id" else None
    if not isinstance(page_id, str) or not page_id:
        raise CardError("AGENDA_CARD_NOT_OWNED")
    regions, protected = _page_regions(client, page_id)
    try:
        resolved = router.resolve_target(regions, MODULE)
    except router.RouterError:
        raise CardError("AGENDA_CARD_NOT_OWNED") from None
    if resolved != block_id:
        raise CardError("AGENDA_CARD_ID_MISMATCH")
    blocks = _children(client, block_id)
    if not _owned(blocks):
        raise CardError("AGENDA_CARD_NOT_OWNED")
    try:
        router.check_write(MODULE, [CARD_TITLE])
    except router.RouterError:
        raise CardError("AGENDA_CARD_NOT_OWNED") from None
    return page_id, blocks, protected


def _text(content, url=None):
    parts = rich_text(content)
    if url and parts:
        parts[0]["text"]["link"] = {"url": url}
    return parts


def _paragraph(content):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _text(content)}}


def _heading(content):
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": _text(content)}}


def _day_events(snapshot, day):
    return [event for event in snapshot["events"] if day in event.get("days", [])]


def _instant(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def _today_marker(events, now):
    timed = [event for event in events if not event["all_day"]]
    active = next((event for event in timed if _instant(event["start"]) <= now < _instant(event["end"])), None)
    if active:
        return active["id"], "Now"
    upcoming = next((event for event in timed if _instant(event["start"]) > now), None)
    if upcoming:
        return upcoming["id"], "Next"
    all_day = next((event for event in events if event["all_day"]), None)
    return (all_day["id"], "Now") if all_day else (None, None)


def _event_block(event, marker=None):
    if event["all_day"]:
        prefix = "All day — "
    else:
        start, end = _instant(event["start"]), _instant(event["end"])
        prefix = f"{start.strftime('%-I:%M %p')}–{end.strftime('%-I:%M %p')} — "
    if marker:
        prefix = f"{marker} · {prefix}"
    parts = _text(prefix) + _text(event["title"], event.get("meeting_link"))
    if event.get("location"):
        parts += _text(f" · {event['location']}")
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": parts}}


def _status(snapshot, stale):
    try:
        taken = _instant(snapshot["taken_at"])
    except (KeyError, TypeError, ValueError):
        raise CardError("AGENDA_SNAPSHOT_INVALID") from None
    return (f"STALE · last accepted {taken.strftime('%Y-%m-%d %H:%M')} CT" if stale
            else f"Updated {taken.strftime('%H:%M')} CT")


def _snapshot_counts(snapshot, stale):
    if not isinstance(snapshot, dict) or snapshot.get("schema") != agenda_snapshot.SCHEMA_V or not isinstance(snapshot.get("events"), list):
        raise CardError("AGENDA_SNAPSHOT_INVALID")
    today, tomorrow = snapshot.get("today"), snapshot.get("tomorrow")
    if not isinstance(today, str) or not isinstance(tomorrow, str):
        raise CardError("AGENDA_SNAPSHOT_INVALID")
    events = snapshot["events"]
    for event in events:
        if (not isinstance(event, dict) or not isinstance(event.get("days"), list)
                or any(day not in (today, tomorrow) for day in event["days"])
                or not isinstance(event.get("id"), str) or not isinstance(event.get("title"), str)
                or not isinstance(event.get("all_day"), bool) or not isinstance(event.get("start"), str)
                or not isinstance(event.get("end"), str)
                or (event.get("location") is not None and not isinstance(event.get("location"), str))
                or (event.get("meeting_link") is not None and not isinstance(event.get("meeting_link"), str))):
            raise CardError("AGENDA_SNAPSHOT_INVALID")
        try:
            if event["all_day"]:
                datetime.fromisoformat(event["start"])
                datetime.fromisoformat(event["end"])
            else:
                if _instant(event["end"]) <= _instant(event["start"]):
                    raise ValueError
        except (TypeError, ValueError):
            raise CardError("AGENDA_SNAPSHOT_INVALID") from None
    return {"events": len(events), "all_day": sum(bool(event.get("all_day")) for event in events),
            "today": sum(today in event["days"] for event in events),
            "tomorrow": sum(tomorrow in event["days"] for event in events),
            "saved": 0, "blocks_written": 0, "status": "stale" if stale else "fresh"}


def render(snapshot, stale=False, now=None):
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    counts = _snapshot_counts(snapshot, stale)
    blocks = [_paragraph(_status(snapshot, stale))]
    today = _day_events(snapshot, snapshot["today"])
    marker_id, marker = _today_marker(today, now)
    for label, day, events in (("Today", snapshot["today"], today),
                               ("Tomorrow", snapshot["tomorrow"], _day_events(snapshot, snapshot["tomorrow"]))):
        blocks.append(_heading(label))
        if events:
            blocks.extend(_event_block(event, marker if label == "Today" and event["id"] == marker_id else None) for event in events)
        else:
            blocks.append(_paragraph("Nothing scheduled"))
    return blocks, counts


def _client(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(),
                   "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _append(client, block_id, blocks):
    for start in range(0, len(blocks), 100):
        chunk = blocks[start:start + 100]
        result = client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": chunk})
        if not isinstance(result, dict) or not isinstance(result.get("results"), list) or len(result["results"]) != len(chunk):
            raise CardError("AGENDA_CARD_APPEND_MISMATCH")


def run(limit, live, environ=os.environ, client=None, now=None, connect=None):
    block_id = (environ.get("CALENDAR_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        return {"events": 0, "all_day": 0, "today": 0, "tomorrow": 0, "saved": 0,
                "blocks_written": 0, "status": "not_configured"}
    try:
        with (connect or db.connect)() as connection:
            saved = agenda_snapshot.load(connection)
    except NotionError:
        raise
    except Exception:
        raise CardError("AGENDA_STORE_FAILED") from None
    if saved is None:
        raise CardError("AGENDA_SNAPSHOT_MISSING")
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    try:
        taken = _instant(saved["taken_at"])
    except (KeyError, TypeError, ValueError):
        raise CardError("AGENDA_SNAPSHOT_INVALID") from None
    stale = now - taken > timedelta(hours=STALE_HOURS)
    blocks, counts = render(saved, stale, now)
    client = client or _client(environ)
    page_id, existing, before = _target(client, block_id)
    status = _status(saved, stale)
    if stale:
        status_block = next((block for block in existing[1:] if block.get("type") == "paragraph"
                             and _plain(block).startswith(STATUS_PREFIXES)), None)
        if status_block is None:
            raise CardError("AGENDA_CARD_STALE_NO_BASELINE")
        if live:
            try:
                router.check_write(MODULE, [CARD_TITLE])
            except router.RouterError:
                raise CardError("AGENDA_CARD_NOT_OWNED") from None
            client.call("PATCH", f"/blocks/{quote(status_block['id'], safe='')}",
                        {"paragraph": {"rich_text": rich_text(status)}})
            after = _children(client, block_id)
            if not _owned(after) or _plain(next((b for b in after if b.get("id") == status_block["id"]), {})) != status:
                raise CardError("AGENDA_CARD_VERIFY_FAILED")
            if hashlib.sha256(json.dumps(existing[2:], sort_keys=True).encode()).hexdigest() != hashlib.sha256(json.dumps(after[2:], sort_keys=True).encode()).hexdigest():
                raise CardError("AGENDA_CARD_VERIFY_FAILED")
            _, after_protected = _page_regions(client, page_id)
            if not router.protected_intact(MODULE, before, after_protected):
                raise CardError("AGENDA_PROTECTED_REGION_CHANGED")
            counts["blocks_written"] = 1
        return counts
    if not live:
        return counts
    new_blocks = blocks
    try:
        router.check_write(MODULE, [CARD_TITLE])
    except router.RouterError:
        raise CardError("AGENDA_CARD_NOT_OWNED") from None
    _append(client, block_id, new_blocks)                 # add first; old content remains if this fails
    _, after_add = _page_regions(client, page_id)
    if not router.protected_intact(MODULE, before, after_add):
        raise CardError("AGENDA_PROTECTED_REGION_CHANGED")
    for old in existing[1:]:
        try:
            router.check_write(MODULE, [CARD_TITLE])
        except router.RouterError:
            raise CardError("AGENDA_CARD_NOT_OWNED") from None
        try:
            client.call("DELETE", f"/blocks/{quote(old['id'], safe='')}")
        except NotionError as error:
            if str(error) != "NOTION_HTTP_404":
                raise
    after = _children(client, block_id)                    # read back the configured Calendar region
    _, after_protected = _page_regions(client, page_id)
    if (not _owned(after) or len(after) != 1 + len(new_blocks) or _plain(after[1]) != status
            or not router.protected_intact(MODULE, before, after_protected)):
        raise CardError("AGENDA_CARD_VERIFY_FAILED")
    counts["blocks_written"] = len(new_blocks)
    return counts
