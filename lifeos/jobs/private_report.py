"""LIFE-121 (Jim, Oct 26): a private report goes to a private Notion page, never into a log. The runner writes the table straight to Notion, as a child of the page that holds the Job Ledger
(already private to Jim's workspace); the log carries counts and fixed codes only. Dry run unless live: a dry run reads, counts and writes nothing. One page per title: a page that already exists is
left untouched (delete it in Notion to write a fresh one). Nothing is encoded, encrypted or copied out of Notion."""
from lifeos.platform import limits
from lifeos.platform.notion_client import NotionError, rich_text

ROWS_PER_TABLE = 90                      # a table's children are capped at 100 per request; one header row plus these


def _fail(counts, code):
    counts["failed"] += 1
    counts["why"][code] = counts["why"].get(code, 0) + 1
    return counts


def parent_page(client):
    """-> the id of the page that holds the Job Ledger database (data source -> database -> page), or None."""
    source = client.call("GET", f"/data_sources/{client.source}")
    database = (source.get("parent") or {}).get("database_id")
    if not database:
        return None
    return ((client.call("GET", f"/databases/{database}").get("parent") or {}).get("page_id")) or None


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


def _existing(client, parent, title):
    reply = client.call("POST", "/search", {"query": title, "filter": {"property": "object", "value": "page"}, "page_size": 25})
    for page in reply.get("results", []):
        name = "".join(part.get("plain_text", "") for part in ((page.get("properties") or {}).get("title") or {}).get("title", []))
        if name == title and ((page.get("parent") or {}).get("page_id") or "").replace("-", "") == parent.replace("-", "") and not page.get("archived") and not page.get("in_trash"):
            return page["id"]
    return None


def publish(client, title, header, rows, live, links=()):
    """-> counts: {"rows", "page": "dry" | "new" | "present", "failed", "why"}. Write only when live; read back the row count before reporting 'new'."""
    counts = {"rows": len(rows), "page": "dry", "failed": 0, "why": {}}
    try:
        parent = parent_page(client)
        if not parent:
            return _fail(counts, "PRIVATE_REPORT_NO_PARENT")
        if _existing(client, parent, title):
            counts["page"] = "present"
            return counts
        if not live:
            return counts
        blocks = tables(header, rows, links)
        if len(blocks) > limits.NOTION_MAX_CHILD_BLOCKS - 1:
            return _fail(counts, "PRIVATE_REPORT_TOO_LARGE")
        page = client.call_once("POST", "/pages", {"parent": {"type": "page_id", "page_id": parent}, "properties": {"title": {"title": rich_text(title)}}, "children": blocks})
        written = sum(len(_children(client, t["id"])) - 1 for t in _children(client, page["id"]) if t.get("type") == "table")
        if written != len(rows):
            return _fail(counts, "PRIVATE_REPORT_READBACK_MISMATCH")
        counts["page"] = "new"
    except NotionError:
        return _fail(counts, "PRIVATE_REPORT_NOTION_FAILED")
    return counts
