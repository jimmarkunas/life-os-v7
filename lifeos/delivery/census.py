"""DEL-1.1: count the real tracking numbers in recent mail, read-only. Nothing is written, labelled, trashed or stored.

A candidate message mentions a shipment or tracking in its subject; its body is searched for carrier-shaped numbers. Digit-only shapes (FedEx, DHL) count only
next to the word "tracking" so order numbers and phone numbers are not miscounted. Output is counts only (D7): numbers per carrier, distinct and repeated, and how
many candidate messages hold none. No tracking number, sender or subject is ever returned or logged."""
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from lifeos.platform.gmail import Gmail, GmailError

QUERY = "{subject:tracking subject:shipped subject:shipment subject:delivery subject:delivered subject:\"on its way\" subject:\"out for delivery\"}"
DEFAULT_LIMIT = 2000
DAYS = 365
NEAR = r"(?:tracking|track)[^0-9A-Za-z]{0,40}(?:number|no|#|id)?[^0-9A-Za-z]{0,10}"
SHAPES = (                                                    # (carrier, regex, needs the word "tracking" just before it)
    ("UPS", re.compile(r"\b1Z[0-9A-Z]{16}\b"), False),
    ("AMAZON", re.compile(r"\bTBA[0-9]{12}\b"), False),
    ("USPS", re.compile(r"\b9[2-5][0-9]{18,24}\b"), False),
    ("FEDEX", re.compile(NEAR + r"([0-9]{12}|[0-9]{15})\b", re.I), True),
    ("DHL", re.compile(NEAR + r"([0-9]{10})\b", re.I), True),
)
CARRIERS = tuple(name for name, _, _ in SHAPES)


class CensusError(GmailError):
    """Fixed failure codes only."""


def numbers_in(text):
    """Distinct (carrier, number) pairs in one message body. A number belongs to the first carrier shape that claims it."""
    found, taken = [], set()
    for carrier, shape, needs_word in SHAPES:
        for match in shape.finditer(text or ""):
            number = (match.group(1) if needs_word else match.group(0)).upper()
            if number not in taken:
                taken.add(number)
                found.append((carrier, number))
    return found


def run(limit, live, environ=os.environ, gmail=None, now=None):
    """Read-only: `live` changes nothing here. Returns counts only."""
    counts = {"messages": 0, "with_numbers": 0, "without_numbers": 0, "numbers": 0, "distinct": 0, "repeated": 0, "unreadable": 0, "since_days": DAYS,
              "by_carrier": {c: 0 for c in CARRIERS}, "written": 0}
    try:
        gmail = gmail or Gmail.from_env()
        ids = gmail.list_ids_complete(f"{QUERY} newer_than:{DAYS}d", limit if limit and limit > 0 else DEFAULT_LIMIT)
    except GmailError as error:
        raise CensusError(str(error)[:60]) from None
    seen = Counter()
    for message_id in ids:
        counts["messages"] += 1
        try:
            record = gmail.message_record(message_id)
        except GmailError:
            counts["unreadable"] += 1
            continue
        pairs = numbers_in(record.get("body_text", ""))
        counts["with_numbers" if pairs else "without_numbers"] += 1
        for carrier, number in pairs:
            counts["numbers"] += 1
            counts["by_carrier"][carrier] += 1
            seen[number] += 1
    counts["distinct"] = len(seen)
    counts["repeated"] = sum(1 for n in seen.values() if n > 1)
    return counts
