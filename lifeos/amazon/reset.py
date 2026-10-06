"""D146: put the orders that were frozen EMPTY back into the queue. A REVIEW row that holds only its order number (no event date, no order date, no total, no source messages)
carries no information and, because REVIEW is absorbing, can never fill in. This moves exactly those rows to the Notion trash (recoverable) so the next backfill rebuilds
them under the current rules. A REVIEW row that holds any data is never touched. Dry run unless live; counts only; at most 100 a run."""
import os
from urllib.parse import quote

from lifeos.platform.notion_client import NotionError
from .stage import AmazonError, _config, _row_from_page, _schema

MAX_ROWS = 100
EMPTY_FIELDS = ("Latest Event At", "Ordered At", "Grand Total", "Source Message IDs")


def _empty_review(row):
    return row.get("Status") == "REVIEW" and not any(row.get(field) not in (None, "") for field in EMPTY_FIELDS)


def run(limit, live, environ=os.environ, notion=None):
    counts = {"review_rows": 0, "empty": 0, "kept_with_data": 0, "trashed": 0, "failed": 0}
    source_id, notion = _config(environ) if notion is None else ((environ.get("NOTION_AMAZON_DATA_SOURCE_ID") or "").strip(), notion)
    if not source_id:
        raise AmazonError("AMAZON_CONFIG_MISSING")
    _schema(notion, source_id)
    pages, cursor, seen = [], None, set()
    for _ in range(20):
        body = {"page_size": 100, "filter": {"property": "Status", "select": {"equals": "REVIEW"}}, **({"start_cursor": cursor} if cursor else {})}
        try:
            page = notion.query_data_source(source_id, body)
        except NotionError:
            raise AmazonError("AMAZON_NOTION_QUERY_FAILED") from None
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise AmazonError("AMAZON_NOTION_QUERY_INCOMPLETE")
        pages.extend(page["results"])
        if not page["has_more"]:
            break
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise AmazonError("AMAZON_NOTION_PAGINATION_INVALID")
        seen.add(cursor)
    else:
        raise AmazonError("AMAZON_NOTION_PAGINATION_INCOMPLETE")
    rows = [_row_from_page(p) for p in pages]
    counts["review_rows"] = len(rows)
    empty = [r for r in rows if _empty_review(r)]
    counts["empty"], counts["kept_with_data"] = len(empty), len(rows) - len(empty)
    if not live:
        return counts
    for row in empty[:limit if limit and limit > 0 else MAX_ROWS]:
        try:
            notion.call_once("PATCH", f"/pages/{quote(row['page_id'], safe='')}", {"in_trash": True})
            check = notion.call("GET", f"/pages/{quote(row['page_id'], safe='')}")
            if not (check.get("in_trash") or check.get("archived")):
                raise AmazonError("AMAZON_NOTION_READBACK_MISMATCH")
            counts["trashed"] += 1
        except (NotionError, AmazonError):
            counts["failed"] += 1
    return counts
