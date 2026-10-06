"""Pure extraction of the three accepted Amazon order mail events."""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import getaddresses

_AT = chr(64)
_DOMAIN = "amazon.com"
SENDERS = {
    f"auto-confirm{_AT}{_DOMAIN}": "ORDERED",
    f"shipment-tracking{_AT}{_DOMAIN}": "SHIPPED",
    f"order-update{_AT}{_DOMAIN}": "DELIVERED",
}
ORDER_ID_RE = re.compile(r"(?<![\w-])\d{3}-\d{7}-\d{7}(?![\w-])")
TOTAL_LABEL_RE = re.compile(r"\bGrand\s+Total\b", re.IGNORECASE)
TOTAL_RE = re.compile(r"\bGrand\s+Total\s*:?\s*(?:USD\s*)?([-+]?\$?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)(?![\d.,])", re.IGNORECASE)
MAX_ORDERS_PER_MESSAGE = 10
# Current confirmation mail states the total on its own lines: "Total" then "21.2 USD" (one or two decimals). Only a line that is exactly "Total", "Order Total" or "Grand Total" counts;
# "Subtotal" and the unlabeled item prices above it never do.
LINE_TOTAL_RE = re.compile(r"(?im)^[ \t]*(?:(?:Order|Grand)[ \t]+)?Total[ \t]*:?[ \t]*\r?\n?[ \t]*(?:USD[ \t]*)?(\$?[ \t]*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)(?:[ \t]*USD)?[ \t\r]*$")
CANONICAL_URL = "https://www.amazon.com/your-orders/order-details?orderID={}"


def _sender(value):
    if not isinstance(value, str):
        return None
    parsed = getaddresses([value])
    if len(parsed) != 1:
        return None
    address = parsed[0][1].strip()
    local, separator, domain = address.rpartition("@")
    if not separator or domain.lower() != _DOMAIN:
        return None
    normalized = local + _AT + _DOMAIN
    return normalized if normalized in SENDERS else None


def _received(value):
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def ambiguity_shape(message):
    """Counts-only description of a message that names several orders, for diagnosis: how many distinct order numbers, how many in the subject, how many in order links.
    No order number, subject or body text is ever returned."""
    body = message.get("body_text") if isinstance(message.get("body_text"), str) else ""
    subject = message.get("subject") if isinstance(message.get("subject"), str) else ""
    linked = set(re.findall(r"orderID=(\d{3}-\d{7}-\d{7})", body))
    return f"ids={len(set(ORDER_ID_RE.findall(body)))},subject={len(set(ORDER_ID_RE.findall(subject)))},linked={len(linked)}"


def _money(text):
    return format(Decimal(text.replace(",", "").replace("$", "").replace(" ", "")).quantize(Decimal("0.01")), ".2f")


def _grouped_by_total(body):
    """A multi-order confirmation lists each order's lines and then that order's own total. Split the body after every total: accepted only when every part holds exactly one
    order number, no order number repeats across parts, and nothing after the last total names an order. -> [(order_id, total)] or None."""
    found = sorted(list(TOTAL_RE.finditer(body)) + list(LINE_TOTAL_RE.finditer(body)), key=lambda m: m.start())
    parts, cursor = [], 0
    for match in found:
        if match.start() < cursor:                                  # the same total matched by both patterns
            continue
        parts.append((body[cursor:match.start()], match.group(1)))
        cursor = match.end()
    if not parts or ORDER_ID_RE.search(body[cursor:]):
        return None
    out = []
    for text, amount in parts:
        ids = set(ORDER_ID_RE.findall(text))
        if len(ids) != 1:
            return None
        try:
            out.append((next(iter(ids)), _money(amount)))
        except (InvalidOperation, ValueError):
            return None
    if len({order_id for order_id, _ in out}) != len(out) or len(out) > MAX_ORDERS_PER_MESSAGE:
        return None
    return out


def extract_all(message):
    """The events a message carries: normally one. A message that names several orders is accepted ONLY in one of two exact shapes, and then yields one event per order with
    the same status: (1) each order is followed by its own total (the confirmation layout), so every order gets its own total; or (2) every order number in it is one of its own
    order-detail links (orderID=...), with no total (a total cannot be assigned to one of several orders). Any other multi-order message stays a REVIEW result. Never returns body text."""
    first = extract(message)
    if first.get("reason") != "ORDER_ID_AMBIGUOUS":
        return [first]
    body = message.get("body_text")
    grouped = _grouped_by_total(body)
    if grouped:
        return [extract({**message, "body_text": f"orderID={order_id}\nGrand Total: {amount}"}) for order_id, amount in grouped]
    ids = sorted(set(ORDER_ID_RE.findall(body)))
    linked = set(re.findall(r"orderID=(\d{3}-\d{7}-\d{7})", body))
    if len(ids) > MAX_ORDERS_PER_MESSAGE or set(ids) != linked:
        return [first]
    return [extract({**message, "body_text": f"orderID={order_id}"}) for order_id in ids]


def extract(message):
    """Return a normalized event dict or a fixed REVIEW result; never return body text."""
    if not isinstance(message, dict):
        return {"status": "REVIEW", "reason": "MESSAGE_INVALID"}
    sender = _sender(message.get("sender"))
    body = message.get("body_text")
    message_id = message.get("id")
    received_at = _received(message.get("received_at"))
    if sender is None:
        return {"status": "REVIEW", "reason": "SENDER_UNSUPPORTED"}
    if not isinstance(message_id, str) or not message_id.strip() or not isinstance(body, str) or received_at is None:
        return {"status": "REVIEW", "reason": "MESSAGE_INCOMPLETE"}
    order_ids = set(ORDER_ID_RE.findall(body))
    if len(order_ids) != 1:
        return {"status": "REVIEW", "reason": "ORDER_ID_MISSING" if not order_ids else "ORDER_ID_AMBIGUOUS"}
    order_id = next(iter(order_ids))
    status = SENDERS[sender]
    totals = set()
    if status == "ORDERED":
        labels, matches = TOTAL_LABEL_RE.findall(body), TOTAL_RE.findall(body)
        if labels and len(labels) != len(matches) and not LINE_TOTAL_RE.findall(body):
            return {"status": "REVIEW", "reason": "TOTAL_INVALID", "order_id": order_id}
        for match in matches + LINE_TOTAL_RE.findall(body):
            try:
                amount = Decimal(match.replace(",", "").replace("$", "").replace(" ", "")).quantize(Decimal("0.01"))
                if amount < 0:
                    return {"status": "REVIEW", "reason": "TOTAL_INVALID", "order_id": order_id}
                totals.add(format(amount, ".2f"))
            except (InvalidOperation, ValueError):
                return {"status": "REVIEW", "reason": "TOTAL_INVALID", "order_id": order_id}
        if len(totals) > 1:
            return {"status": "REVIEW", "reason": "TOTAL_CONFLICT", "order_id": order_id}
    subject = message.get("subject") if isinstance(message.get("subject"), str) else ""
    subject = subject.strip()[:2000]
    item_summary = re.sub(r"^\s*(?:ordered|shipped|delivered)\s*:\s*", "", subject, flags=re.IGNORECASE).strip()[:1000]
    return {
        "id": message_id.strip(),
        "sender": sender,
        "received_at": received_at,
        "order_id": order_id,
        "status": status,
        "grand_total": next(iter(totals), None),
        "item_summary": item_summary,
        "item_count": None,
        "subject": subject,
        "amazon_order_url": CANONICAL_URL.format(order_id),
    }
