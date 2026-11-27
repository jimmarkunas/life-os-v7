"""A private report goes to a private Notion page, never into a log.

The table is written to Notion as a child of the owner's private Reports page; the log carries counts and fixed codes
only. Dry run unless live. One page per title: an existing page is left untouched (delete it to write a fresh one).
"""

from lifeos.platform.notion_ids import notion_id
from lifeos.platform import full_width, limits, stored_pages
from lifeos.platform.notion_client import NotionError, rich_text

ROWS_PER_TABLE = 90


def _fail(counts, code):
    counts["failed"] += 1
    counts["why"][code] = counts["why"].get(code, 0) + 1
    return counts


# the private "Reports" page; an id is not a secret
REPORTS_PAGE = notion_id("REPORTS_PAGE")


def _cell(text, link=False):
    if link and text.startswith("https://") and len(text) <= 1900:
        return [{"type": "text", "text": {"content": text, "link": {"url": text}}}]
    return rich_text(text)


def _row(values, links):
    return {
        "object": "block",
        "type": "table_row",
        "table_row": {"cells": [_cell(v, i in links) for i, v in enumerate(values)]},
    }


def tables(header, rows, links=()):
    """-> table blocks of at most ROWS_PER_TABLE data rows each, every one with the header row."""
    out = []
    for start in range(0, max(len(rows), 1), ROWS_PER_TABLE):
        part = rows[start : start + ROWS_PER_TABLE]
        out.append(
            {
                "object": "block",
                "type": "table",
                "table": {
                    "table_width": len(header),
                    "has_column_header": True,
                    "has_row_header": False,
                    "children": [_row(header, ()), *[_row(r, links) for r in part]],
                },
            }
        )
    return out


def _children(client, block):
    found, cursor = [], None
    while True:
        page = client.call(
            "GET",
            f"/blocks/{block}/children?page_size={limits.NOTION_PAGE_SIZE}"
            + (f"&start_cursor={cursor}" if cursor else ""),
        )
        found += page.get("results", [])
        if not page.get("has_more"):
            return found
        cursor = page.get("next_cursor")


def publish(client, title, header, rows, live, links=(), intro=(), key=None, connect=None):
    """-> counts: {"rows", "page": "dry" | "new" | "present", "failed", "why"}.

    Write only when live; read back the row count before reporting 'new'. `intro`: summary lines written as ONE
    paragraph above the tables. `key`: a fixed report key; when given, the page id is kept in the private store under
    it, so a second run leaves the first page alone. Without a key (a dated, always-new report) there is nothing to
    look up.
    """
    counts = {"rows": len(rows), "page": "dry", "failed": 0, "why": {}}
    try:
        parent = REPORTS_PAGE
        if key:
            if connect is None:
                from lifeos.jobs import store  # noqa: PLC0415 - the Jobs private store, only when a report is keyed

                connect = store.connect
            if stored_pages.find(client, connect, key, live):
                counts["page"] = "present"
                return counts
        if not live:
            return counts
        blocks = (
            [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text("\n".join(intro))}}]
            if intro
            else []
        ) + tables(header, rows, links)
        if len(blocks) > limits.NOTION_MAX_CHILD_BLOCKS - 1:
            return _fail(counts, "PRIVATE_REPORT_TOO_LARGE")
        page = full_width.create_page(client, parent, title, blocks)
        written = sum(
            len(_children(client, t["id"])) - 1 for t in _children(client, page["id"]) if t.get("type") == "table"
        )
        if written != len(rows):
            return _fail(counts, "PRIVATE_REPORT_READBACK_MISMATCH")
        if key:
            stored_pages.remember(connect, key, page["id"])
        counts["page"] = "new"
    except NotionError:
        return _fail(counts, "PRIVATE_REPORT_NOTION_FAILED")
    return counts
