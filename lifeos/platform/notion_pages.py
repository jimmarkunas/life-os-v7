"""LIFE-645: the one Notion paging loop: yields each page of a query or a block's children; anything short of a provably complete read raises a fixed code.

`fail` is the caller's error factory (`fail(code)` returns the exception). Codes: `incomplete` (a page without results/has_more, or a missing or empty next_cursor), `repeat` (a cursor seen before; default `incomplete`), `capped` (more than `limit` pages; default `incomplete`), `read` (the fetch itself raised NotionError; when None that error passes through). `limit=None` is unbounded."""
from urllib.parse import quote

from lifeos.platform import limits
from lifeos.platform.notion_client import NotionError


def pages(fetch, fail, incomplete, repeat=None, capped=None, limit=None, read=None):
    """Yield every page `fetch(cursor)` returns (cursor is None for the first), following next_cursor."""
    seen, cursor, turn = set(), None, 0
    while limit is None or turn < limit:
        turn += 1
        try:
            page = fetch(cursor)
        except NotionError:
            if not read:
                raise
            raise fail(read) from None
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise fail(incomplete)
        yield page
        if not page["has_more"]:
            return
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise fail(incomplete)
        if cursor in seen:
            raise fail(repeat or incomplete)
        seen.add(cursor)
    raise fail(capped or incomplete)


def query(client, source_id, body, fail, **codes):
    """The pages of a data-source query; `body` is the request body without start_cursor (source_id None means the client's own source)."""
    return pages(lambda cursor: client.query_data_source(source_id, {**body, **({"start_cursor": cursor} if cursor else {})}), fail, **codes)


def children(client, block_id, fail, **codes):
    """The pages of a block's (or page's) children, page_size 100."""
    path = f"/blocks/{quote(block_id, safe='')}/children?page_size={limits.NOTION_PAGE_SIZE}"
    return pages(lambda cursor: client.call("GET", path + (f"&start_cursor={quote(cursor, safe='')}" if cursor else "")), fail, **codes)
