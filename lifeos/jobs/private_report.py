"""LIFE-121 (Jim, Oct 26): a private report goes to a private Notion page, never into a log. The runner writes the table straight to Notion, as a child of Jim's private Reports page; the log carries counts and fixed codes only. Dry run unless live: a dry run reads, counts and writes nothing. One page per title: a page that already exists is
left untouched (delete it in Notion to write a fresh one). Nothing is encoded, encrypted or copied out of Notion."""
from datetime import datetime, timezone

from lifeos.platform.notion_ids import notion_id
from lifeos.platform import limits, snapshot_store
from lifeos.platform.notion_client import NotionError, rich_text

ROWS_PER_TABLE = 90                      # a table's children are capped at 100 per request; one header row plus these


def _fail(counts, code):
    counts["failed"] += 1
    counts["why"][code] = counts["why"].get(code, 0) + 1
    return counts


REPORTS_PAGE = notion_id("REPORTS_PAGE")          # Jim's private "Reports" page (shared with the Jobs integration, Oct 27 2026); an id is not a secret


def _cell(text, link=False):
    if link and text.startswith("https://") and len(text) <= 1900:
        return [{"type": "text", "text": {"content": text, "link": {"url": text}}}]
    return rich_text(text)


def _row(values, links):
    return {"object": "block", "type": "table_row", "table_row": {"cells": [_cell(v, i in links) for i, v in enumerate(values)]}}


def tables(header, rows, links=()):
    """-> table blocks of at most ROWS_PER_TABLE data rows each, every one with the header row."""
    out = []
    for start in range(0, max(len(rows), 1), ROWS_PER_TABLE):
        part = rows[start:start + ROWS_PER_TABLE]
        out.append({"object": "block", "type": "table", "table": {"table_width": len(header), "has_column_header": True, "has_row_header": False,
                                                                  "children": [_row(header, ()), *[_row(r, links) for r in part]]}})
    return out


def _children(client, block):
    found, cursor = [], None
    while True:
        page = client.call("GET", f"/blocks/{block}/children?page_size={limits.NOTION_PAGE_SIZE}" + (f"&start_cursor={cursor}" if cursor else ""))
        found += page.get("results", [])
        if not page.get("has_more"):
            return found
        cursor = page.get("next_cursor")


STORE = snapshot_store.Store("v7_private_reports", key_column="report_key", key_type="VARCHAR(96)")     # LIFE-369 (D250-10): report key -> the page id V7 created; one private row per report


def _existing(client, connect, key, live):
    """The page id stored for this report key, if that page is still live in Notion, else None. D250: no Notion search, no title match, no parent check: the stored id is read back by id."""
    with connect() as connection:
        if live:
            STORE.ensure(connection)                                  # a dry run never creates the table; it reads, and a missing table means "no report yet"
            saved = STORE.load(connection, key)
        else:
            try:
                saved = STORE.load(connection, key)
            except Exception:                                         # noqa: BLE001  the table does not exist before the first live run
                saved = None
    page_id = (saved or {}).get("page_id")
    if not page_id:
        return None
    page = client.call("GET", f"/pages/{page_id}")
    return None if page.get("archived") or page.get("in_trash") else page_id


def _remember(connect, key, page_id):
    with connect() as connection:
        STORE.ensure(connection)
        snapshot = {"taken_at": datetime.now(timezone.utc).isoformat(), "schema": 1, "page_id": page_id}
        STORE.save(connection, key, snapshot)
        if STORE.load(connection, key) != snapshot:
            raise NotionError("PRIVATE_REPORT_KEY_READBACK")


def publish(client, title, header, rows, live, links=(), intro=(), key=None, connect=None):
    """-> counts: {"rows", "page": "dry" | "new" | "present", "failed", "why"}. Write only when live; read back the row count before reporting 'new'.
    `intro` (LIFE-212): summary lines written as ONE paragraph above the tables. `key` (LIFE-369): a fixed report key; when given, the page id is kept in the private store under it, so a second run finds
    the first page by that id and leaves it alone (delete the page in Notion to write a fresh one). Without a key (a dated, always-new report) there is nothing to look up."""
    counts = {"rows": len(rows), "page": "dry", "failed": 0, "why": {}}
    try:
        parent = REPORTS_PAGE
        if key:
            if connect is None:
                from lifeos.jobs import store                                      # noqa: PLC0415  the Jobs private store, only when a report is keyed
                connect = store.connect
            if _existing(client, connect, key, live):
                counts["page"] = "present"
                return counts
        if not live:
            return counts
        blocks = ([{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text("\n".join(intro))}}] if intro else []) + tables(header, rows, links)
        if len(blocks) > limits.NOTION_MAX_CHILD_BLOCKS - 1:
            return _fail(counts, "PRIVATE_REPORT_TOO_LARGE")
        page = client.call_once("POST", "/pages", {"parent": {"type": "page_id", "page_id": parent}, "properties": {"title": {"title": rich_text(title)}}, "children": blocks})
        written = sum(len(_children(client, t["id"])) - 1 for t in _children(client, page["id"]) if t.get("type") == "table")
        if written != len(rows):
            return _fail(counts, "PRIVATE_REPORT_READBACK_MISMATCH")
        if key:
            _remember(connect, key, page["id"])
        counts["page"] = "new"
    except NotionError:
        return _fail(counts, "PRIVATE_REPORT_NOTION_FAILED")
    return counts
