"""Render the canonical Amazon Orders rows into V7's one "Amazon Orders" callout on the Daily Report (D118).

Gmail -> amazon-orders -> canonical Notion Amazon Orders -> amazon-card. The card only READS the canonical orders (the same data source the ingest writes)
and writes the text of the one callout it owns; it never changes an order. When the ingest step just failed the line says DEGRADED and the last accepted
orders stay visible: a failed sync is never shown as current, and an unreadable order list changes nothing. Counts-only logs, fixed error codes."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform import report_region, router
from lifeos.platform.notion_client import Client, NotionError, rich_text
from . import stage

TZ = ZoneInfo("America/Chicago")
TITLE = router.AMAZON_REGION
MODULE = router.OWNERS[TITLE]
DELIVERED_DAYS = 7
MAX_LISTED = 10
MAX_PAGES = 10
PROTECTED = (("JIRA_CARD_BLOCK_ID", router.JIRA_REGION), ("CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION), ("BILLS_CARD_BLOCK_ID", router.BILLS_REGION))


class CardError(NotionError):
    """Fixed codes only; never expose order ids, items or amounts."""


def _fail(code):
    return CardError("AMAZON_" + code)


def _query_body(since):
    return {"page_size": 100, "sorts": [{"property": "Latest Event At", "direction": "descending"}],
            "filter": {"or": [{"property": "Status", "select": {"equals": "ORDERED"}}, {"property": "Status", "select": {"equals": "SHIPPED"}},
                              {"property": "Needs Review", "checkbox": {"equals": True}},
                              {"and": [{"property": "Status", "select": {"equals": "DELIVERED"}},
                                       {"property": "Latest Event At", "date": {"on_or_after": since}}]}]}}


def read_rows(client, source_id, since):
    """Every open, review and recently delivered order, or a fixed error: a partial list is never rendered as the whole."""
    rows, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        body = _query_body(since)
        if cursor:
            if cursor in seen:
                raise _fail("CARD_PAGINATION_INVALID")
            seen.add(cursor)
            body["start_cursor"] = cursor
        try:
            page = client.query_data_source(source_id, body)
        except NotionError:
            raise _fail("CARD_READ_FAILED") from None
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise _fail("CARD_READ_INCOMPLETE")
        try:
            rows.extend(stage._row_from_page(item) for item in page["results"])
        except NotionError:
            raise _fail("CARD_ROW_INVALID") from None
        if not page["has_more"]:
            return rows
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise _fail("CARD_READ_INCOMPLETE")
    raise _fail("CARD_PAGINATION_INCOMPLETE")


def _when(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def _day(value):
    when = _when(value)
    return f"{when.strftime('%b')} {when.day}" if when else "date unknown"


def _amount(row):
    try:
        value = float(row["Grand Total"]) if row.get("Grand Total") not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return None if value is None else (f"${value:,.0f}" if value.is_integer() else f"${value:,.2f}")


def _bullet(row, label):
    name = (row.get("Item Summary") or "").strip() or "Amazon order"
    link = row.get("Amazon Order URL")
    text = rich_text(name)
    if isinstance(link, str) and link.startswith("https://") and text:
        text[0]["text"]["link"] = {"url": link}
    tail = " · ".join(part for part in (label, _amount(row) or "amount unknown", _day(row.get("Latest Event At"))) if part)
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": text + rich_text(" · " + tail)}}


def _paragraph(content):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(content)}}


def sections(rows, now):
    cutoff = now - timedelta(days=DELIVERED_DAYS)
    review = [r for r in rows if r.get("Needs Review") is True or r.get("Status") == "REVIEW"]
    open_ = [r for r in rows if r.get("Status") in ("ORDERED", "SHIPPED") and r not in review]
    delivered = [r for r in rows if r.get("Status") == "DELIVERED" and r not in review and (_when(r.get("Latest Event At")) or now) >= cutoff]
    key = lambda r: _when(r.get("Latest Event At")) or now
    return (sorted(open_, key=key, reverse=True), sorted(delivered, key=key, reverse=True), sorted(review, key=key, reverse=True))


def render(rows, now, degraded=False):
    """-> (blocks, counts). Pure."""
    open_, delivered, review = sections(rows, now)
    head = (f"DEGRADED · Amazon sync failed at {now.strftime('%-I:%M %p')} CT · showing last accepted orders" if degraded else f"Updated {now.strftime('%-I:%M %p')} CT")
    summary = f"{head} · {len(open_)} in transit · {len(delivered)} delivered (last {DELIVERED_DAYS} days) · {len(review)} need review"
    blocks = [_paragraph(summary)]
    for title, items, label in (("In transit", open_, None), (f"Delivered, last {DELIVERED_DAYS} days", delivered, "Delivered"), ("Needs review", review, "Review")):
        if items:
            blocks.append(_paragraph(title))
            blocks += [_bullet(row, (row.get("Status", "").title() if label is None else label)) for row in items[:MAX_LISTED]]
            if len(items) > MAX_LISTED:
                blocks.append(_paragraph(f"…and {len(items) - MAX_LISTED} more"))
    if not (open_ or delivered or review):
        blocks.append(_paragraph(f"No open orders and nothing delivered in the last {DELIVERED_DAYS} days"))
    counts = {"in_transit": len(open_), "delivered": len(delivered), "review": len(review), "status": "degraded" if degraded else "fresh", "blocks_written": 0}
    return blocks, counts


def _writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def run(limit, live, environ=os.environ, reader=None, client=None, now=None):
    block_id = (environ.get("AMAZON_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        return {"in_transit": 0, "delivered": 0, "review": 0, "status": "not_configured", "blocks_written": 0}
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    if reader is None:
        source_id, reader = stage._config(environ)
    else:
        source_id = (environ.get("NOTION_AMAZON_DATA_SOURCE_ID") or "").strip().replace("collection://", "")
    stage._schema(reader, source_id)
    rows = read_rows(reader, source_id, (now - timedelta(days=DELIVERED_DAYS + 1)).date().isoformat())
    degraded = (environ.get("AMAZON_SYNC_OUTCOME") or "").strip().lower() in ("failure", "cancelled")
    blocks, counts = render(rows, now, degraded)
    client = client or _writer(environ)
    protected_ids = {region: (environ.get(name) or "").strip() for name, region in PROTECTED}
    _, removed = report_region.replace_text(client, block_id, TITLE, MODULE, blocks, protected_ids, _fail, "amazon", live)
    if live:
        counts["blocks_written"], counts["removed"] = len(blocks), removed
    return counts
