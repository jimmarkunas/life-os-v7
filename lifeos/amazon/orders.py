"""Pure monotone reconciliation of Amazon order events."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

RANK = {"ORDERED": 1, "SHIPPED": 2, "DELIVERED": 3, "REVIEW": 99}
FIELDS = (
    "Order ID", "Status", "Ordered At", "Latest Event At", "Grand Total", "Item Summary", "Item Count",
    "Amazon Order URL", "Source Message IDs", "Last Source Subject", "Last Reconciled At", "Needs Review",
)


def _text(value):
    return str(value).strip() if value is not None else ""


def _time(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("AMAZON_EVENT_TIME_INVALID")
    return parsed.astimezone(timezone.utc)


def _total(value):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("AMAZON_TOTAL_INVALID") from None
    return format(parsed, ".2f")


def _conflict(existing):
    """Only the review fields change after a conflict; every other property stays untouched."""
    return {"Status": "REVIEW", "Needs Review": True}


def reconcile(events, existing=None, now=None):
    """Return (full desired row, conflict reason or None); input events must share one order id."""
    existing = dict(existing or {})
    if not events:
        raise ValueError("AMAZON_EVENTS_EMPTY")
    order_ids = {event.get("order_id") for event in events}
    if len(order_ids) != 1 or not next(iter(order_ids)):
        raise ValueError("AMAZON_ORDER_ID_CONFLICT")
    order_id = next(iter(order_ids))
    by_key = {}
    try:
        for event in events:
            if (event.get("status") not in ("ORDERED", "SHIPPED", "DELIVERED")
                    or not isinstance(event.get("id"), str) or not event["id"]):
                return _conflict(existing), "EVENT_INVALID"
            event_time = _time(event.get("received_at"))
            if event_time is None:
                return _conflict(existing), "EVENT_TIME_INVALID"
            expected_url = f"https://www.amazon.com/your-orders/order-details?orderID={order_id}"
            if event.get("amazon_order_url") != expected_url:
                return _conflict(existing), "URL_CONFLICT"
            normalized_event = dict(event)
            # Shipment and delivery amounts are source details, never the canonical order total.
            normalized_event["grand_total"] = _total(event.get("grand_total")) if event["status"] == "ORDERED" else None
            key = (order_id, event["status"], event["id"])
            prior = by_key.get(key)
            candidate = normalized_event
            if prior is not None and prior != candidate:
                return _conflict(existing), "DUPLICATE_EVENT_CONFLICT"
            by_key[key] = candidate
    except (TypeError, ValueError):
        return _conflict(existing), "EVENT_INVALID"
    unique = list(by_key.values())
    current_status = _text(existing.get("Status"))
    if current_status and current_status not in RANK:
        return _conflict(existing), "STATUS_INVALID"
    if current_status == "REVIEW" or existing.get("Needs Review") is True:
        return _conflict(existing), "ORDER_ALREADY_REVIEW"
    existing_url = _text(existing.get("Amazon Order URL"))
    canonical = f"https://www.amazon.com/your-orders/order-details?orderID={order_id}"
    if existing_url and existing_url != canonical:
        return _conflict(existing), "URL_CONFLICT"
    try:
        existing_latest = _time(existing.get("Latest Event At"))
    except (TypeError, ValueError):
        return _conflict(existing), "EVENT_TIME_INVALID"
    existing_rank = RANK.get(current_status, 0)
    chronological = sorted(unique, key=lambda event: (_time(event["received_at"]), RANK[event["status"]], event["id"]))
    previous_rank = 0
    previous_time = None
    for event in chronological:
        event_time = _time(event["received_at"])
        rank = RANK[event["status"]]
        if previous_time is not None and event_time > previous_time and rank < previous_rank:
            return _conflict(existing), "LIFECYCLE_REGRESSION"
        previous_rank = max(previous_rank, rank) if event_time == previous_time else rank
        previous_time = event_time
        if existing_latest is not None and event_time > existing_latest and existing_rank and rank < existing_rank:
            return _conflict(existing), "LIFECYCLE_REGRESSION"
    ordered = [event for event in unique if event["status"] == "ORDERED"]
    incoming_totals = {event["grand_total"] for event in ordered if event.get("grand_total") is not None}
    try:
        prior_total = _total(existing.get("Grand Total"))
    except ValueError:
        return _conflict(existing), "TOTAL_INVALID"
    if len(incoming_totals) > 1 or (prior_total is not None and incoming_totals and prior_total not in incoming_totals):
        return _conflict(existing), "TOTAL_CONFLICT"
    desired_status = max([existing_rank, *(RANK[event["status"]] for event in unique)])
    status = next((name for name, rank in RANK.items() if rank == desired_status), "ORDERED")
    if status == "REVIEW":
        return _conflict(existing), "STATUS_INVALID"
    times = [(_time(event["received_at"]), event) for event in unique]
    latest_time, latest_event = max(times, key=lambda pair: (pair[0], pair[1]["id"]))
    ordered_times = [_time(event["received_at"]) for event in ordered]
    prior_ordered = _time(existing.get("Ordered At"))
    all_ordered_times = ordered_times + ([prior_ordered] if prior_ordered else [])
    ordered_at = min(all_ordered_times).isoformat() if all_ordered_times else existing.get("Ordered At")
    source_ids = set(x for x in _text(existing.get("Source Message IDs")).splitlines() if x)
    source_ids.update(event["id"] for event in unique)
    all_latest = max([latest_time] + ([existing_latest] if existing_latest else []))
    existing_subject = _text(existing.get("Last Source Subject"))
    last_subject = existing_subject if existing_latest and existing_latest > latest_time else _text(latest_event.get("subject"))
    latest_summary = _text(latest_event.get("item_summary")) or _text(existing.get("Item Summary"))
    latest_count = latest_event.get("item_count") if latest_event.get("item_count") is not None else existing.get("Item Count")
    total = next(iter(incoming_totals), prior_total)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("AMAZON_NOW_INVALID")
    row = {
        "Order ID": order_id,
        "Status": status,
        "Ordered At": ordered_at,
        "Latest Event At": all_latest.isoformat(),
        "Grand Total": total,
        "Item Summary": latest_summary,
        "Item Count": latest_count,
        "Amazon Order URL": canonical,
        "Source Message IDs": "\n".join(sorted(source_ids)),
        "Last Source Subject": last_subject,
        "Last Reconciled At": now.astimezone(timezone.utc).isoformat(),
        "Needs Review": False,
    }
    return row, None


def same(existing, desired):
    """Compare Notion-normalized properties, excluding the audit timestamp."""
    if not existing:
        return False
    for field in FIELDS:
        if field == "Last Reconciled At":
            continue
        left, right = existing.get(field), desired.get(field)
        if field in ("Grand Total",) and left not in (None, "") and right not in (None, ""):
            try:
                if Decimal(str(left)).quantize(Decimal("0.01")) != Decimal(str(right)).quantize(Decimal("0.01")):
                    return False
            except (InvalidOperation, ValueError):
                return False
        elif field == "Item Count" and left not in (None, "") and right not in (None, ""):
            try:
                if int(left) != int(right):
                    return False
            except (TypeError, ValueError):
                return False
        elif field in ("Ordered At", "Latest Event At"):
            if _time(left) != _time(right):
                return False
        elif field == "Needs Review":
            if bool(left) != bool(right):
                return False
        elif _text(left) != _text(right):
            return False
    return True
