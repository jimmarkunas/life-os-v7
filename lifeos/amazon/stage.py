"""Gmail to canonical Notion Amazon Orders; filing follows a verified durable write."""
import os
from datetime import datetime, timezone
from urllib.parse import quote

from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.notion_client import Client, NotionError, rich_text
from . import events, orders

SENDERS_QUERY = "{" + " ".join("from:" + address for address in events.SENDERS) + "} in:inbox -label:Amazon"
SCHEMA_TYPES = {
    "Order ID": "title", "Status": "select", "Ordered At": "date", "Latest Event At": "date",
    "Grand Total": "number", "Item Summary": "rich_text", "Item Count": "number",
    "Amazon Order URL": "url", "Source Message IDs": "rich_text", "Last Source Subject": "rich_text",
    "Last Reconciled At": "date", "Needs Review": "checkbox",
}
STATUS_OPTIONS = {"ORDERED", "SHIPPED", "DELIVERED", "REVIEW"}
MAX_QUERY_PAGES = 100


class AmazonError(NotionError):
    """Fixed failure codes only; never include mail or property values."""


def _now():
    return datetime.now(timezone.utc)


def _schema(client, source_id):
    try:
        result = client.call("GET", f"/data_sources/{quote(source_id, safe='')}")
    except NotionError:
        raise AmazonError("AMAZON_NOTION_SCHEMA_READ_FAILED") from None
    props = result.get("properties") if isinstance(result, dict) else None
    if not isinstance(props, dict):
        raise AmazonError("AMAZON_NOTION_SCHEMA_INVALID")
    for name, expected in SCHEMA_TYPES.items():
        prop = props.get(name)
        if not isinstance(prop, dict) or prop.get("type") != expected:
            raise AmazonError("AMAZON_NOTION_SCHEMA_MISMATCH")
    options = props["Status"].get("select", {}).get("options")
    if not isinstance(options, list) or not STATUS_OPTIONS.issubset({o.get("name") for o in options if isinstance(o, dict)}):
        raise AmazonError("AMAZON_NOTION_SCHEMA_MISMATCH")


def _value(prop):
    if not isinstance(prop, dict):
        raise AmazonError("AMAZON_NOTION_ROW_INVALID")
    kind = prop.get("type")
    raw = prop.get(kind) if kind else None
    if kind in ("title", "rich_text"):
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise AmazonError("AMAZON_NOTION_ROW_INVALID")
        return "".join(item.get("plain_text") or (item.get("text") or {}).get("content", "") for item in raw)
    if kind == "select":
        return raw.get("name") if isinstance(raw, dict) else None
    if kind == "date":
        return raw.get("start") if isinstance(raw, dict) else None
    if kind == "url":
        return raw
    if kind == "number":
        return raw
    if kind == "checkbox":
        return raw if isinstance(raw, bool) else False
    raise AmazonError("AMAZON_NOTION_ROW_INVALID")


def _row_from_page(page):
    if not isinstance(page, dict) or not isinstance(page.get("id"), str) or not isinstance(page.get("properties"), dict):
        raise AmazonError("AMAZON_NOTION_ROW_INVALID")
    props = page["properties"]
    if any(not isinstance(props.get(field), dict) or props[field].get("type") != kind
           for field, kind in SCHEMA_TYPES.items()):
        raise AmazonError("AMAZON_NOTION_ROW_INVALID")
    row = {field: _value(props.get(field)) for field in SCHEMA_TYPES}
    row["page_id"] = page["id"]
    return row


def _query_order(client, source_id, order_id):
    rows, cursor, seen = [], None, set()
    for _ in range(MAX_QUERY_PAGES):
        body = {"page_size": 100, "filter": {"property": "Order ID", "title": {"equals": order_id}}}
        if cursor:
            if cursor in seen:
                raise AmazonError("AMAZON_NOTION_PAGINATION_INVALID")
            seen.add(cursor)
            body["start_cursor"] = cursor
        try:
            page = client.query_data_source(source_id, body)
        except NotionError:
            raise AmazonError("AMAZON_NOTION_QUERY_FAILED") from None
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise AmazonError("AMAZON_NOTION_QUERY_INCOMPLETE")
        parsed = [_row_from_page(item) for item in page["results"]]
        if any(row.get("Order ID") != order_id for row in parsed):
            raise AmazonError("AMAZON_NOTION_QUERY_MISMATCH")
        rows.extend(parsed)
        if not page["has_more"]:
            return rows
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise AmazonError("AMAZON_NOTION_QUERY_INCOMPLETE")
    raise AmazonError("AMAZON_NOTION_PAGINATION_INCOMPLETE")


def _properties(row, partial=False):
    def rt(value):
        value = str(value or "")
        if len(value) > 200_000:
            raise AmazonError("AMAZON_NOTION_VALUE_TOO_LONG")
        return {"rich_text": rich_text(value)}
    def date(value):
        return {"date": {"start": value} if value else None}
    if partial:
        return {"Status": {"select": {"name": "REVIEW"}}, "Needs Review": {"checkbox": True}}
    total = row.get("Grand Total")
    try:
        total_value = float(total) if total not in (None, "") else None
    except (ValueError, TypeError, OverflowError):
        raise AmazonError("AMAZON_NOTION_VALUE_INVALID") from None
    return {
        "Order ID": {"title": rich_text(row["Order ID"])},
        "Status": {"select": {"name": row["Status"]}},
        "Ordered At": date(row.get("Ordered At")),
        "Latest Event At": date(row.get("Latest Event At")),
        "Grand Total": {"number": total_value},
        "Item Summary": rt(row.get("Item Summary")),
        "Item Count": {"number": row.get("Item Count")},
        "Amazon Order URL": {"url": row.get("Amazon Order URL")},
        "Source Message IDs": rt(row.get("Source Message IDs")),
        "Last Source Subject": rt(row.get("Last Source Subject")),
        "Last Reconciled At": date(row.get("Last Reconciled At")),
        "Needs Review": {"checkbox": bool(row.get("Needs Review"))},
    }


def _get_page(client, page_id):
    try:
        page = client.call("GET", f"/pages/{quote(page_id, safe='')}")
    except NotionError:
        raise AmazonError("AMAZON_NOTION_READBACK_FAILED") from None
    return _row_from_page(page)


def _write(client, source_id, existing, desired, review_only=False):
    props = _properties(desired, partial=True) if review_only else _properties(desired)
    if existing:
        page_id = existing["page_id"]
        try:
            client.call_once("PATCH", f"/pages/{quote(page_id, safe='')}", {"properties": props})
        except NotionError:
            raise AmazonError("AMAZON_NOTION_WRITE_FAILED") from None
    else:
        # REVIEW rows contain identity and the two review fields only; no event fields are guessed.
        create_props = ({"Order ID": {"title": rich_text(desired["Order ID"])}, **props}
                        if review_only else props)
        body = {"parent": {"type": "data_source_id", "data_source_id": source_id}, "properties": create_props}
        try:
            result = client.call_once("POST", "/pages", body)
        except NotionError:
            raise AmazonError("AMAZON_NOTION_WRITE_FAILED") from None
        page_id = result.get("id") if isinstance(result, dict) else None
        if not isinstance(page_id, str) or not page_id:
            raise AmazonError("AMAZON_NOTION_WRITE_FAILED")
    return _get_page(client, page_id)


def _minute(value):
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).replace(second=0, microsecond=0)
    except (TypeError, ValueError):
        raise AmazonError("AMAZON_NOTION_ROW_INVALID") from None


def _readback_equal(actual, expected, review_only=False):
    keys = ("Status", "Needs Review") if review_only else orders.FIELDS
    for field in keys:
        left, right = actual.get(field), expected.get(field)
        if field in ("Ordered At", "Latest Event At", "Last Reconciled At"):
            if _minute(left) != _minute(right):
                return False
        elif field == "Grand Total":
            if (left in (None, "")) != (right in (None, "")):
                return False
            if left not in (None, "") and orders._total(left) != orders._total(right):
                return False
        elif field == "Needs Review":
            if bool(left) != bool(right):
                return False
        elif field == "Item Count":
            if left is None and right is None:
                continue
            if left is None or right is None or int(left) != int(right):
                return False
        elif ("" if left is None else str(left)) != ("" if right is None else str(right)):
            return False
    return True


def _review_readback_equal(actual, desired, before):
    if not _readback_equal(actual, desired, review_only=True):
        return False
    for field in orders.FIELDS:
        if field in ("Status", "Needs Review"):
            continue
        left, right = actual.get(field), before.get(field)
        if field in ("Ordered At", "Latest Event At", "Last Reconciled At"):
            if _minute(left) != _minute(right):
                return False
        elif field == "Grand Total":
            if (left in (None, "")) != (right in (None, "")):
                return False
            if left not in (None, "") and orders._total(left) != orders._total(right):
                return False
        elif field == "Needs Review":
            if bool(left) != bool(right):
                return False
        elif ("" if left is None else str(left)) != ("" if right is None else str(right)):
            return False
    return True


def _review_desired(order_id):
    return {"Order ID": order_id, **orders._conflict({})}


def _config(environ):
    token = (environ.get("NOTION_AMAZON_TOKEN") or "").strip()
    source = (environ.get("NOTION_AMAZON_DATA_SOURCE_ID") or "").strip().replace("collection://", "")
    if not token or not source:
        raise AmazonError("AMAZON_CONFIG_MISSING")
    if not source or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for char in source):
        raise AmazonError("AMAZON_CONFIG_INVALID")
    return source, Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": source})


def _gmail_ids(gmail, limit):
    try:
        # The Gmail search has an exact sender allowlist; message headers are checked again before extraction.
        return gmail.list_ids_complete(SENDERS_QUERY, limit)
    except GmailError as error:
        code = str(error)
        if code in {"GMAIL_LIST_LIMIT_EXCEEDED", "GMAIL_LIST_LIMIT_INVALID", "GMAIL_LISTING_INCOMPLETE"}:
            raise AmazonError("AMAZON_" + code) from None
        raise AmazonError("AMAZON_GMAIL_LIST_FAILED") from None


def run(limit, live, environ=os.environ, gmail=None, notion=None, now=None):
    counts = {"listed": 0, "accepted": 0, "review": 0, "orders_new": 0, "orders_updated": 0,
              "orders_same": 0, "filed": 0, "failed": 0}
    try:
        gmail = gmail or Gmail.from_env()
        source_id, notion = _config(environ) if notion is None else ((environ.get("NOTION_AMAZON_DATA_SOURCE_ID") or "").strip(), notion)
        if not source_id:
            raise AmazonError("AMAZON_CONFIG_MISSING")
        label_id = gmail.label_id("Amazon", create=False)
        try:
            message_ids = _gmail_ids(gmail, limit)
        except AmazonError as error:
            if str(error) == "AMAZON_GMAIL_LIST_LIMIT_EXCEEDED":
                counts["listed"] = limit + 1
            raise
        counts["listed"] = len(message_ids)
        # Fetch the entire bounded census before any write. An incomplete read leaves the mailbox untouched.
        messages = []
        for message_id in message_ids:
            try:
                message = gmail.message_record(message_id)
            except GmailError:
                raise AmazonError("AMAZON_GMAIL_READ_FAILED") from None
            messages.append(message)
        events_by_order, review_by_order, accepted_ids = {}, {}, {}
        for message in messages:
            if label_id in message["label_ids"]:
                continue
            parsed = events.extract(message)
            if parsed.get("status") == "REVIEW":
                counts["review"] += 1
                if parsed.get("order_id"):
                    review_by_order.setdefault(parsed["order_id"], []).append(message)
                continue
            counts["accepted"] += 1
            order_id = parsed["order_id"]
            events_by_order.setdefault(order_id, []).append(parsed)
            accepted_ids.setdefault(order_id, []).append(message["id"])
        if not events_by_order and not review_by_order:
            return counts
        _schema(notion, source_id)
        now = now or _now()
        plans = []
        for order_id in sorted(set(events_by_order) | set(review_by_order)):
            found = _query_order(notion, source_id, order_id)
            if len(found) > 1:
                raise AmazonError("AMAZON_NOTION_DUPLICATE_ORDER")
            existing = found[0] if found else None
            conflict = bool(review_by_order.get(order_id))
            desired = _review_desired(order_id) if conflict else None
            if not conflict:
                desired, reason = orders.reconcile(events_by_order[order_id], existing, now)
                conflict = reason is not None
                if conflict:
                    desired = _review_desired(order_id)
            if conflict:
                counts["accepted"] -= len(events_by_order.get(order_id, []))
                counts["review"] += len(events_by_order.get(order_id, []))
                if existing:
                    already_review = existing.get("Status") == "REVIEW" and existing.get("Needs Review") is True
                    counts["orders_same" if already_review else "orders_updated"] += 1
                else:
                    counts["orders_new"] += 1
                plans.append((order_id, existing, desired, True, []))
            else:
                if existing and orders.same(existing, desired):
                    desired["Last Reconciled At"] = existing.get("Last Reconciled At")
                    counts["orders_same"] += 1
                else:
                    counts["orders_updated" if existing else "orders_new"] += 1
                plans.append((order_id, existing, desired, False, accepted_ids.get(order_id, [])))
        if not live:
            return counts
        failures = []
        for order_id, existing, desired, review_only, file_ids in plans:
            try:
                same = bool(existing) and (_readback_equal(existing, desired, review_only) if review_only
                                           else orders.same(existing, desired))
                if same and not review_only:
                    persisted = _get_page(notion, existing["page_id"])
                    if not _readback_equal(persisted, desired):
                        raise AmazonError("AMAZON_NOTION_READBACK_MISMATCH")
                elif review_only and existing and existing.get("Status") == "REVIEW" and existing.get("Needs Review") is True:
                    persisted = existing
                else:
                    persisted = _write(notion, source_id, existing, desired, review_only)
                    matches = (_review_readback_equal(persisted, desired, existing) if review_only and existing
                               else _readback_equal(persisted, desired, review_only))
                    if not matches:
                        raise AmazonError("AMAZON_NOTION_READBACK_MISMATCH")
                if review_only:
                    continue
                for message_id in file_ids:
                    try:
                        gmail.apply_amazon(message_id, label_id)
                        labels = gmail.message_labels(message_id)
                    except GmailError:
                        raise AmazonError("AMAZON_GMAIL_FILE_FAILED") from None
                    if label_id not in labels or "INBOX" in labels or "TRASH" in labels:
                        raise AmazonError("AMAZON_GMAIL_READBACK_MISMATCH")
                    counts["filed"] += 1
            except AmazonError as error:
                failures.append(str(error))
                counts["failed"] += max(1, len(file_ids) or len(review_by_order.get(order_id, [])))
            except Exception:
                failures.append("AMAZON_INTERNAL_FAILED")
                counts["failed"] += max(1, len(file_ids))
        if failures:
            codes = "+".join(sorted(set(failures)))
            raise AmazonError(f"AMAZON_FAILED:{counts['failed']}of{counts['listed']}:{codes}")
        return counts
    except AmazonError as error:
        code = str(error)
        if code.startswith("AMAZON_FAILED:"):
            raise
        raise AmazonError(f"AMAZON_FAILED:{max(1, counts['failed'])}of{counts['listed']}:{code}") from None
    except GmailError:
        raise AmazonError("AMAZON_GMAIL_FAILED") from None
    except NotionError:
        raise AmazonError("AMAZON_NOTION_FAILED") from None
    except Exception:
        raise AmazonError("AMAZON_FAILED:1of0:AMAZON_INTERNAL_FAILED") from None
