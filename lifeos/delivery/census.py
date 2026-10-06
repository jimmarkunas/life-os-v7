"""DEL-1.1: count the real tracking numbers in recent mail, read-only. Nothing is written, labelled, trashed or stored.

A candidate message mentions a shipment or tracking in its subject; its body is searched for carrier-shaped numbers. Digit-only shapes (FedEx, DHL) count only
next to the word "tracking" so order numbers and phone numbers are not miscounted. Output is counts only (D7): numbers per carrier, distinct and repeated, and how
many candidate messages hold none. No tracking number, sender or subject is ever returned or logged."""
import os
import re
from collections import Counter
from urllib.parse import unquote
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
HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
PARAM = re.compile(r"(?:tracknum|tracknumbers?|trackingnumbers?|tracking_number|trknbr|tlabels|trackid|trackingid|tracking_id|trackingnum)=([0-9A-Za-z]{10,34})", re.I)
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


def link_numbers(html):
    """Distinct (carrier, number) pairs held in the links of an HTML body: a carrier-shaped number anywhere in a link, or a number in a tracking query parameter."""
    found, taken = [], set()
    for href in HREF.findall(html or ""):
        href = unquote(unquote(href.replace("&amp;", "&")))
        candidates = [(c, n) for c, n in numbers_in(href)]
        candidates += [(c, n) for c, n in numbers_in("tracking " + " tracking ".join(PARAM.findall(href)))]
        for carrier, number in candidates:
            if number not in taken:
                taken.add(number)
                found.append((carrier, number))
    return found


def run(limit, live, environ=os.environ, gmail=None, now=None):
    """Read-only: `live` changes nothing here. Returns counts only."""
    counts = {"messages": 0, "in_links_only": 0, "with_numbers": 0, "without_numbers": 0, "numbers": 0, "distinct": 0, "repeated": 0, "unreadable": 0, "since_days": DAYS,
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
        try:
            linked = link_numbers(gmail.message(message_id)[1])
        except GmailError:
            linked = []
        known = {n for _, n in pairs}
        extra = [(c, n) for c, n in linked if n not in known]
        counts["in_links_only"] += 1 if extra and not pairs else 0
        pairs += extra
        counts["with_numbers" if pairs else "without_numbers"] += 1
        for carrier, number in pairs:
            counts["numbers"] += 1
            counts["by_carrier"][carrier] += 1
            seen[number] += 1
    counts["distinct"] = len(seen)
    counts["repeated"] = sum(1 for n in seen.values() if n > 1)
    return counts
