"""LIFE-933: V7 writes the Weekly Reports Archive (it was the retired ChatGPT 6 PM closeout's), one static snapshot of the Daily Report per day.

On the first run at or after 6:00 PM CT the stage freezes the live Daily Report as plain static blocks into its week's child page `Weekly Reports — Week Ending <Sunday>` (made if missing), as
`Daily LIFE OS Report — <date>`: no live linked database view is copied, one snapshot per date (a re-run is a no-op), and the write is read back block by block against what was meant to be written.
Sunday: after the day's snapshot verifies, the Report's Notes callout (if the page has one) is frozen once as `Weekly Notes — Week Ending <date>`. Monday: the live Notes callout is reset only when the prior
week's one Notes snapshot re-verifies exactly; a missing, duplicate or different snapshot keeps the live Notes. No Notes callout on the page: the receipt says `notes: none` and nothing is made or reset.
It reads and writes with the existing Notion integration only (NOTION_JIRA_TOKEN), in the existing dcc job: no new token and no schedule. Counts and fixed codes only in logs."""
import os
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.platform import full_width
from lifeos.platform.notion_client import Client, NotionError, rich_text
from lifeos.platform.notion_text import block_text

TZ = ZoneInfo("America/Chicago")
DUE_HOUR = 18                                                            # 6:00 PM CT: the first run at or after it takes the day's snapshot
WEEK_TITLE, DAY_TITLE, NOTES_TITLE = "Weekly Reports — Week Ending {}", "Daily LIFE OS Report — {}", "Weekly Notes — Week Ending {}"
NOTES_NAMES = ("Notes", "Live Notes", "Protected Notes")
MARK = "Snapshot complete and read back"                                   # the last block of a finished snapshot: a page without it is an interrupted write, never counted
BATCH = 90
STATIC = {"paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item", "to_do", "toggle", "callout", "quote", "code", "divider", "table", "table_row"}
FLATTEN = {"column_list", "column"}                                         # their children are copied in order, one after the other
LIVE = {"child_database", "link_to_page", "synced_block", "child_page", "embed", "bookmark", "image", "file", "pdf", "video", "audio", "unsupported"}


class ArchiveError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return ArchiveError("ARCHIVE_" + code)


def week_end(day):
    """The Sunday that ends the Monday to Sunday week of `day`."""
    return day + timedelta(days=6 - day.weekday())


def label(day):
    return f"{day.strftime('%b')} {day.day}, {day.year}"


def _client(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _clean(raw):
    value = (raw or "").strip().replace("-", "").lower()
    return value if len(value) == 32 and all(c in "0123456789abcdef" for c in value) else ""


def _kids(client, block_id):
    """Every child block of `block_id`, all pages (a page that reports more than it gave, or a loop, is a fixed code)."""
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
    """The page a block lives on, walking up its parents (at most 12 levels)."""
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
    """Rich text as plain static pieces: the text, its link and its annotations; a mention is only its text."""
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
    """The static copy of one block's children as [{type, <type>: payload, children: [...]}]: only plain content kinds; a live view, embed, image or link-to-page is skipped and counted, never copied."""
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
            inner.pop("icon", None)                                         # only an emoji icon is copied; a hosted file or custom icon cannot be re-created
        if kind == "table_row":
            inner["cells"] = [_rich(cell) for cell in inner.get("cells") or []]
        node = {"type": kind, kind: inner}
        if block.get("has_children"):
            node["children"] = freeze(client, block["id"], skipped)
        out.append(node)
    return out


def digest(nodes, depth=0):
    """[(depth, type, text)] of a frozen tree or of one read back from Notion (rows read as their cells)."""
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
    """Append `nodes` under `parent_id` in batches, then each node's children under the block just made (one non-retried write each; the caller reads everything back)."""
    for start in range(0, len(nodes), BATCH):
        batch = nodes[start:start + BATCH]
        body = {"children": [_create(node) for node in batch]}
        made = client.call_once("PATCH", f"/blocks/{quote(parent_id, safe='')}/children", body).get("results") or []
        if len(made) != len(batch):
            raise _fail("WRITE_COUNT")
        for node, block in zip(batch, made):
            if node.get("children") and node["type"] != "table":            # a table's rows went in with the table
                _write(client, block["id"], node["children"])


def _create(node):
    """The create payload of one node: Notion refuses a table without its rows in the same request, so the rows travel inside it; every other node's children are appended afterwards."""
    out = {k: v for k, v in node.items() if k != "children"}
    if node["type"] == "table":
        out["table"] = {**out["table"], "children": [_create(row) for row in node.get("children") or []]}
    return out


def _fill(client, page_id, nodes):
    """Write `nodes` into a page made from the template, then remove the template's one empty paragraph (it can only go once there is another block under it)."""
    _write(client, page_id, nodes)
    full_width.clear_filler(client, page_id)


def _page_titles(client, page_id):
    """{title: [child page ids]} for the child pages of `page_id`."""
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
    """The empty page from the Full Width template (D16.1); its blocks are written after it. Fails closed, making nothing, when the template is not configured."""
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
    page = page or _make_page(client, archive_id, WEEK_TITLE.format(label(end)), "🗓️")      # a week page left empty by an interrupted run is filled, not duplicated
    _fill(client, page, intro)
    if digest(read_tree(client, page)) != digest(intro):
        raise _fail("READBACK_MISMATCH")
    return page


def _snapshot(client, parent_id, title, emoji, head, make_nodes, live, may_be_empty=False):
    """Make the page `title` under `parent_id` (full width) with `head` and the frozen `nodes`, finish it with the marker and read it all back. -> ("created" | "exists" | "would_create" | "empty", rows).
    Fails closed on an empty body, or one with fewer top-level blocks than the source gave: a title with no content is never done. Frozen notes that are empty make no page (`may_be_empty`)."""
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
        client.call_once("PATCH", f"/pages/{quote(existing, safe='')}", {"archived": True})                      # an interrupted write: set it aside, never counted, then write it afresh
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
    """The id of the Report's Notes callout (top level or inside a column), or None when the page has none."""
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
    notes_id = find_notes(client, page)
    out["notes"] = "present" if notes_id else "none"
    if now.weekday() == 0 and notes_id:                                       # Monday: the reset gate, before anything else is written
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
    if now.weekday() == 6 and notes_id:                                       # Sunday: the one final weekly Notes snapshot, after the day's snapshot verified
        out["weekly_notes"] = _snapshot(client, week, NOTES_TITLE.format(label(end)), "🗒️", [], lambda: _notes_nodes(client, notes_id), live, may_be_empty=True)[0]
    return out


def _monday(client, archive_id, notes_id, today, live):
    """Reset the live Notes only when the prior week's one Notes snapshot re-reads exactly equal to it. -> "reset", "already_empty", "would_reset" or a fixed refusal word (the live Notes stays)."""
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
