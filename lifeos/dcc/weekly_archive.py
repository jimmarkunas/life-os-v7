"""The Weekly Reports Archive: one static snapshot of the Daily Report per day.

From 6:00 PM CT the first run freezes the Daily Report as plain blocks into the week's page, one `Daily LIFE OS
Report — <date>` per date, read back block by block. Sunday freezes the Notes callout once; Monday resets the live
Notes only if the prior week's snapshot re-verifies exactly. Counts and fixed codes only."""
import os
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.platform import full_width
from lifeos.platform.notion_client import Client, NotionError, rich_text
from lifeos.platform.notion_text import block_text

TZ = ZoneInfo("America/Chicago")
DUE_HOUR = 18  # 6:00 PM CT
WEEK_TITLE, DAY_TITLE, NOTES_TITLE = "Weekly Reports — Week Ending {}", "Daily LIFE OS Report — {}", "Weekly Notes — Week Ending {}"
NOTES_NAMES = ("Notes", "Live Notes", "Protected Notes")
MARK = "Snapshot complete and read back"  # last block of a finished snapshot
BATCH = 90
STATIC = {"paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item", "to_do", "toggle", "callout", "quote", "code", "divider", "table", "table_row"}
FLATTEN = {"column_list", "column"}
LIVE = {"child_database", "link_to_page", "synced_block", "child_page", "embed", "bookmark", "image", "file", "pdf", "video", "audio", "unsupported"}


class ArchiveError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return ArchiveError("ARCHIVE_" + code)


def week_end(day):
    return day + timedelta(days=6 - day.weekday())


def label(day):
    return f"{day.strftime('%b')} {day.day}, {day.year}"


def _client(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _clean(raw):
    value = (raw or "").strip().replace("-", "").lower()
    return value if len(value) == 32 and all(c in "0123456789abcdef" for c in value) else ""


def _kids(client, block_id):
    """Every child block across all pages; an inconsistent page raises PAGE_INCOMPLETE."""
    results, cursor, seen = [], None, set()
    for _ in range(50):
        path = f"/blocks/{quote(block_id, safe='')}/children?page_size=100" + (f"&start_cursor={quote(cursor, safe='')}" if cursor else "")
        page = client.call("GET", path)
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise _fail("PAGE_INCOMPLETE")
        results.extend(page["results"])
        if not page["has_more"]:
            return results
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or cursor in seen:
            raise _fail("PAGE_INCOMPLETE")
        seen.add(cursor)
    raise _fail("PAGE_INCOMPLETE")


def _page_of(client, block_id):
    node = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    for _ in range(12):
        parent = (node or {}).get("parent") or {}
        if parent.get("type") == "page_id":
            return parent["page_id"]
        if parent.get("type") != "block_id":
            break
        node = client.call("GET", f"/blocks/{quote(parent['block_id'], safe='')}")
    raise _fail("PAGE_UNRESOLVED")


def _rich(items):
    out = []
    for part in items or []:
        if not isinstance(part, dict):
            continue
        content = part.get("plain_text") or (part.get("text") or {}).get("content") or ""
        link = (part.get("text") or {}).get("link") or ({"url": part["href"]} if part.get("href") else None)
        piece = {"type": "text", "text": {"content": content, **({"link": link} if link else {})}}
        if part.get("annotations"):
            piece["annotations"] = {k: v for k, v in part["annotations"].items() if k in ("bold", "italic", "strikethrough", "underline", "code", "color")}
        out.append(piece)
    return out


def freeze(client, block_id, skipped):
    """Static copy of a block's children; live views, embeds and images are appended to `skipped`."""
    out = []
    for block in _kids(client, block_id):
        kind = block.get("type")
        if kind in FLATTEN:
            out.extend(freeze(client, block["id"], skipped))
            continue
        if kind in LIVE or kind not in STATIC:
            skipped.append(kind)
            continue
        inner = {k: v for k, v in (block.get(kind) or {}).items() if v is not None and k not in ("color", "is_toggleable")}
        if "rich_text" in inner:
            inner["rich_text"] = _rich(inner["rich_text"])
        if "caption" in inner:
            inner["caption"] = _rich(inner["caption"])
        if kind == "callout" and (inner.get("icon") or {}).get("type") != "emoji":
            inner.pop("icon", None)  # only emoji icons are copied
        if kind == "table_row":
            inner["cells"] = [_rich(cell) for cell in inner.get("cells") or []]
        node = {"type": kind, kind: inner}
        if block.get("has_children"):
            node["children"] = freeze(client, block["id"], skipped)
        out.append(node)
    return out


def digest(nodes, depth=0):
    """[(depth, type, text)] of a frozen tree or one read back from Notion."""
    out = []
    for node in nodes:
        kind = node["type"]
        inner = node.get(kind) or {}
        text = "".join((p.get("plain_text") or (p.get("text") or {}).get("content") or "") for p in inner.get("rich_text") or [] if isinstance(p, dict))
        if kind == "table_row":
            text = " | ".join("".join((p.get("plain_text") or (p.get("text") or {}).get("content") or "") for p in cell) for cell in inner.get("cells") or [])
        out.append((depth, kind, text))
        out.extend(digest(node.get("children") or [], depth + 1))
    return out


def read_tree(client, block_id):
    nodes = []
    for block in _kids(client, block_id):
        node = {"type": block["type"], block["type"]: block.get(block["type"]) or {}}
        if block.get("has_children"):
            node["children"] = read_tree(client, block["id"])
        nodes.append(node)
    return nodes


def _write(client, parent_id, nodes):
    """Append `nodes` in batches, then their children; each write is sent once."""
    for start in range(0, len(nodes), BATCH):
        batch = nodes[start:start + BATCH]
        body = {"children": [_create(node) for node in batch]}
        made = client.call_once("PATCH", f"/blocks/{quote(parent_id, safe='')}/children", body).get("results") or []
        if len(made) != len(batch):
            raise _fail("WRITE_COUNT")
        for node, block in zip(batch, made):
            if node.get("children") and node["type"] != "table":
                _write(client, block["id"], node["children"])


def _create(node):
    """A table's rows must travel inside its create payload; other children are appended later."""
    out = {k: v for k, v in node.items() if k != "children"}
    if node["type"] == "table":
        out["table"] = {**out["table"], "children": [_create(row) for row in node.get("children") or []]}
    return out


def _fill(client, page_id, nodes):
    _write(client, page_id, nodes)
    full_width.clear_filler(client, page_id)


def _page_titles(client, page_id):
    found = {}
    for block in _kids(client, page_id):
        if block.get("type") == "child_page":
            found.setdefault((block.get("child_page") or {}).get("title", ""), []).append(block["id"])
    return found


def _one(found, title):
    ids = found.get(title, [])
    if len(ids) > 1:
        raise _fail("DUPLICATE_SNAPSHOT")
    return ids[0] if ids else None


def _make_page(client, parent_id, title, emoji):
    """A page from the Full Width template; fails closed when the template is missing."""
    try:
        return full_width.create(client, parent_id, title, emoji)
    except full_width.FullWidthError as error:
        raise _fail("TEMPLATE_MISSING") from error


def _complete(client, page_id):
    kids = _kids(client, page_id)
    return bool(kids) and block_text(kids[-1]) == MARK


def _week_page(client, archive_id, found, end, live):
    page = _one(found, WEEK_TITLE.format(label(end)))
    if page and (not live or _kids(client, page)):
        return page
    if not page and not live:
        return None
    start = end - timedelta(days=6)
    intro = [{"type": "heading_2", "heading_2": {"rich_text": rich_text("Reporting Week")}},
             {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text(f"Start: {label(start)}")}},
             {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text(f"End: {label(end)}")}},
             {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text("Static Daily LIFE OS Report snapshots are stored below once each day (the first V7 run at or after 6 PM CT).")}},
             {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text("Live linked database views are not archived here.")}}]
    # fill, don't duplicate, an empty page from an interrupted run
    page = page or _make_page(client, archive_id, WEEK_TITLE.format(label(end)), "🗓️")
    _fill(client, page, intro)
    if digest(read_tree(client, page)) != digest(intro):
        raise _fail("READBACK_MISMATCH")
    return page


def _snapshot(client, parent_id, title, emoji, head, make_nodes, live, may_be_empty=False):
    """Make page `title`, mark it complete and read it back; returns (state, rows).

    Fails closed on an empty or short body unless `may_be_empty`."""
    found = _page_titles(client, parent_id)
    existing = _one(found, title)
    if existing and _complete(client, existing):
        return "exists", 0
    if not live:
        return "would_create", 0
    nodes = make_nodes()
    if not nodes:
        if may_be_empty:
            return "empty", 0
        raise _fail("EMPTY_SNAPSHOT")
    if existing:
        # interrupted write: archive and redo
        client.call_once("PATCH", f"/pages/{quote(existing, safe='')}", {"archived": True})
    body = head + nodes
    page = _make_page(client, parent_id, title, emoji)
    _fill(client, page, body)
    read = read_tree(client, page)
    if len(read) < len(body) or digest(read) != digest(body):
        raise _fail("READBACK_MISMATCH")
    _write(client, page, [{"type": "paragraph", "paragraph": {"rich_text": rich_text(MARK)}}])
    if not _complete(client, page):
        raise _fail("MARKER_MISSING")
    return "created", len(digest(body))


def find_notes(client, page_id):
    def scan(block_id, depth):
        for block in _kids(client, block_id):
            kind = block.get("type")
            if kind in ("callout", "heading_1", "heading_2", "heading_3") and block_text(block).strip().split("\n")[0].strip() in NOTES_NAMES:
                return block["id"]
            if depth < 2 and kind in FLATTEN:
                found = scan(block["id"], depth + 1)
                if found:
                    return found
        return None
    return scan(page_id, 0)


def _notes_nodes(client, notes_id):
    return freeze(client, notes_id, [])


def _reset_notes(client, notes_id):
    for block in _kids(client, notes_id):
        client.call_once("PATCH", f"/blocks/{quote(block['id'], safe='')}", {"archived": True})
    _write(client, notes_id, [{"type": "paragraph", "paragraph": {"rich_text": []}}])


def run(limit, live, environ=os.environ, client=None, now=None):
    archive_id, card = _clean(environ.get("WEEKLY_ARCHIVE_PAGE_ID")), _clean(environ.get("DCC_CARD_BLOCK_ID"))
    if not archive_id or not card:
        return {"status": "not_configured"}
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    client = client or _client(environ)
    page = _page_of(client, card)
    out = {"status": "ok", "date": label(now.date()), "live": bool(live)}
    # an unreadable archive root fails the stage closed
    root = client.call("GET", f"/pages/{quote(archive_id, safe='')}")
    out["archive"] = {"root": "readable", "archived": bool(root.get("archived") or root.get("in_trash")), "weeks": sorted(t for t in _page_titles(client, archive_id) if t.startswith("Weekly Reports"))}
    notes_id = find_notes(client, page)
    out["notes"] = "present" if notes_id else "none"
    if now.weekday() == 0 and notes_id:  # Monday: reset gate first
        out["reset"] = _monday(client, archive_id, notes_id, now.date(), live)
    if now.hour < DUE_HOUR:
        out["snapshot"] = "not_due"
        return out
    end = week_end(now.date())
    found = _page_titles(client, archive_id)
    week = _week_page(client, archive_id, found, end, live)
    if not week:
        out["snapshot"] = "would_create_week"
        return out
    skipped = []
    head = [{"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Frozen from the {label(now.date())} V7 Daily Report taken at {now.strftime('%-I:%M %p')} CT. Historical evidence only; no live linked views are embedded."},
                                                              "annotations": {"italic": True}}]}}]
    out["snapshot"], out["blocks"] = _snapshot(client, week, DAY_TITLE.format(label(now.date())), "📄", head, lambda: freeze(client, page, skipped), live)
    out["live_views_skipped"] = len(skipped)
    if now.weekday() == 6 and notes_id:  # Sunday: weekly Notes snapshot
        out["weekly_notes"] = _snapshot(client, week, NOTES_TITLE.format(label(end)), "🗒️", [], lambda: _notes_nodes(client, notes_id), live, may_be_empty=True)[0]
    return out


def _monday(client, archive_id, notes_id, today, live):
    """Reset the live Notes only if the prior week's snapshot re-reads equal; returns "reset", "already_empty",
    "would_reset" or a refusal word."""
    live_nodes = _notes_nodes(client, notes_id)
    if not any(text.strip() for _, _, text in digest(live_nodes)):
        return "already_empty"
    prior = week_end(today - timedelta(days=1))
    week = _one(_page_titles(client, archive_id), WEEK_TITLE.format(label(prior)))
    if not week:
        return "kept_no_week_page"
    found = _page_titles(client, week)
    title = NOTES_TITLE.format(label(prior))
    if len(found.get(title, [])) > 1:
        return "kept_duplicate_snapshot"
    snap = _one(found, title)
    if not snap or not _complete(client, snap):
        return "kept_no_snapshot"
    saved = [row for row in digest(read_tree(client, snap)) if row[2] != MARK]
    if saved != digest(live_nodes):
        return "kept_mismatch"
    if not live:
        return "would_reset"
    _reset_notes(client, notes_id)
    return "reset"
