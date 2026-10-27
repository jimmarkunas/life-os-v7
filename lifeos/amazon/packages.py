"""Packages from stores other than Amazon, for the Daily Report (Jim, Oct 27 2026: an Ejuice Store order shipped by DHL was nowhere on it).

Stateless and read-only: each run reads the last WINDOW_DAYS of shipping mail from Gmail, folds it into packages and renders nothing it cannot read back from the mail
itself, so there is no store to rebuild and a package leaves on its own when its mail ages out. Amazon mail and the Physical Mail FedEx notices are excluded: they have
their own sections. A package is "in transit" from its shipped mail until a delivered mail for the same tracking number (or, with no number, from the same store) arrives
or STALE_DAYS pass. Counts only in logs; fixed codes."""
import re
from datetime import datetime, timedelta
from email.utils import parseaddr
from urllib.parse import quote

from lifeos.platform.gmail import GmailError

WINDOW_DAYS = 30
STALE_DAYS = 21                                   # an in-transit package with no delivered mail leaves the card after this long
DELIVERED_DAYS = 7                                # a delivered package is listed this long, like Amazon orders
LIMIT = 300
SUBJECT_TERMS = ('"on its way"', '"on the way"', "shipped", '"has shipped"', "shipment", '"out for delivery"', "delivered", "tracking", '"has been delivered"')
FEDEX_FOLLOW = "TrackingUpdates" + chr(64) + "fedex" + ".com"   # the follow-up the Physical Mail section already reads
EXCLUDED = ("amazon.com", "anytimemailbox.com")   # domains with their own sections (Amazon Orders, Mail Alerts)
SHIPPED_SUBJECT = re.compile(r"\b(on (?:its|the) way|shipped|has shipped|shipment|tracking|out for delivery|dispatched)\b", re.I)
DELIVERED_SUBJECT = re.compile(r"\b(?:has been |was |is )?delivered\b", re.I)
NOT_DELIVERED = re.compile(r"\b(?:will be|to be|not|undeliver\w*|attempt\w*|delayed?|exception)\b", re.I)
TABLE = re.compile(r"Provider\s+Tracking\s+Number(?:\s+(?:Date|Status|Shipped))?\s+((?:[A-Za-z][A-Za-z0-9&.]*\s+){0,2}[A-Za-z][A-Za-z0-9&.]*)\s+(\d[A-Za-z0-9-]{7,33})\b")
INLINE = re.compile(r"(?i)\btracking\s*(?:number|no\.?|#|id)\s*[:#]?\s*(\d[A-Za-z0-9-]{7,33})\b")
CARRIERS = (("DHLECOMMERCE", "DHL eCommerce"), ("DHLGLOBALMAIL", "DHL eCommerce"), ("DHL", "DHL"), ("UPS", "UPS"), ("USPS", "USPS"), ("FEDEX", "FedEx"), ("ONTRAC", "OnTrac"), ("LASERSHIP", "LaserShip"), ("ROYALMAIL", "Royal Mail"))
URLS = {"DHL": "https://www.dhl.com/us-en/home/tracking.html?tracking-id=", "DHL eCommerce": "https://www.dhl.com/us-en/home/tracking/tracking-ecommerce.html?submit=1&tracking-id=", "UPS": "https://www.ups.com/track?tracknum=", "USPS": "https://tools.usps.com/go/TrackConfirmAction?tLabels=",
        "FedEx": "https://www.fedex.com/fedextrack/?trknbr="}


SHAPES = ((re.compile(r"\b1Z[0-9A-Z]{16}\b"), "UPS"), (re.compile(r"\b9[2-5][0-9]{18,24}\b"), "USPS"))          # carriers whose numbers need no label to be recognised


def query(now):
    terms = " ".join("subject:" + term for term in SUBJECT_TERMS)
    return "{" + terms + "} " + " ".join("-from:" + domain for domain in EXCLUDED) + f" -from:{FEDEX_FOLLOW} after:{int((now - timedelta(days=WINDOW_DAYS)).timestamp())}"


def carrier_name(raw):
    squashed = re.sub(r"[^A-Za-z]", "", raw or "").upper()
    for prefix, name in CARRIERS:
        if squashed.startswith(prefix):
            return name
    return (raw or "").strip().title()[:24] or "Carrier"


ANY_CARRIER = "https://t.17track.net/en#nums="             # a carrier the mail did not name: one public page that recognises most tracking numbers


def tracking_url(carrier, number):
    """The carrier's own public tracking page for this number; a number with no recognised carrier opens the any-carrier page instead of nothing."""
    return (URLS.get(carrier) or ANY_CARRIER) + quote(number, safe="")


def store_name(sender):
    """The store's display name from the From header, else the second-level domain; never the address."""
    name, address = parseaddr(sender or "")
    name = " ".join(name.replace('"', "").split())
    if name:
        return name[:40]
    host = address.rpartition("@")[2].lower()
    parts = [p for p in host.split(".") if p]
    return (parts[-2] if len(parts) >= 2 else host).title()[:40] or "Store"


def numbers(text):
    """Distinct (carrier, number) pairs in a message body: a provider/number table, an inline "tracking number", then the census carrier shapes."""
    found, taken = [], set()
    flat = " ".join((text or "").split())
    for match in TABLE.finditer(flat):
        number = match.group(2).upper()
        if sum(c.isdigit() for c in number) >= 6 and number not in taken:
            taken.add(number)
            found.append((carrier_name(match.group(1)), number))
    for match in INLINE.finditer(flat):
        number = match.group(1).upper()
        if sum(c.isdigit() for c in number) >= 6 and number not in taken:
            taken.add(number)
            found.append(("", number))
    for shape, carrier in SHAPES:
        for match in shape.finditer(flat):
            number = match.group(0).upper()
            if number not in taken:
                taken.add(number)
                found.append((carrier, number))
    return found


def extract(record):
    """-> {"kind": "SHIPPED" | "DELIVERED", "at", "store", "numbers": [(carrier, number)]} or None for mail that is neither."""
    subject = record.get("subject") or ""
    delivered = bool(DELIVERED_SUBJECT.search(subject)) and not NOT_DELIVERED.search(subject) and not re.search(r"out for delivery", subject, re.I)
    shipped = bool(SHIPPED_SUBJECT.search(subject)) and not delivered
    if not (delivered or shipped):
        return None
    found = numbers(record.get("body_text", ""))
    if shipped and not found:
        return None                                # a shipping notice with no tracking number cannot be followed or closed: it is not listed
    return {"kind": "DELIVERED" if delivered else "SHIPPED", "at": record["received_at"], "store": store_name(record.get("sender")), "numbers": found}


def _at(value):
    return datetime.fromisoformat(value)


def fold(extracted):
    """-> packages [{key, store, carrier, number, shipped_at, delivered_at}], pure. Delivered closes a package by number, else the oldest open one from the same store."""
    packages = {}
    for event in sorted((e for e in extracted if e), key=lambda e: e["at"]):
        if event["kind"] == "SHIPPED":
            for carrier, number in event["numbers"]:
                pkg = packages.setdefault(number, {"key": number, "store": event["store"], "carrier": carrier, "number": number, "shipped_at": event["at"], "delivered_at": None})
                pkg["carrier"] = pkg["carrier"] or carrier
        else:
            closed = False
            for _, number in event["numbers"]:
                if number in packages and not packages[number]["delivered_at"]:
                    packages[number]["delivered_at"], closed = event["at"], True
            if not closed and not event["numbers"]:
                open_ = sorted((p for p in packages.values() if not p["delivered_at"] and p["store"] == event["store"] and p["shipped_at"] <= event["at"]), key=lambda p: p["shipped_at"])
                if open_:
                    open_[0]["delivered_at"] = event["at"]
    return list(packages.values())


def read(gmail, now):
    """-> packages. Complete or it raises GmailError: a partial census would show a package as undelivered that a later mail closed."""
    ids = gmail.list_ids_complete(query(now), LIMIT)
    return fold(extract(gmail.message_record(message_id, keep_lines=True)) for message_id in ids)


def split(packages, now):
    """-> (in_transit, delivered) newest first, with the stale and old ones dropped."""
    transit = [p for p in packages if not p["delivered_at"] and _at(p["shipped_at"]) >= now - timedelta(days=STALE_DAYS)]
    delivered = [p for p in packages if p["delivered_at"] and _at(p["delivered_at"]) >= now - timedelta(days=DELIVERED_DAYS)]
    return (sorted(transit, key=lambda p: p["shipped_at"], reverse=True), sorted(delivered, key=lambda p: p["delivered_at"], reverse=True))


def safe_read(gmail, now):
    """-> (packages | None, code). Never raises: a failed read is shown as unavailable, not as no packages."""
    try:
        return read(gmail, now), None
    except GmailError:
        return None, "PACKAGES_GMAIL_FAILED"
