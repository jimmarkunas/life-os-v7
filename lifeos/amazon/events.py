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
        if labels and len(labels) != len(matches):
            return {"status": "REVIEW", "reason": "TOTAL_INVALID", "order_id": order_id}
        for match in matches:
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
