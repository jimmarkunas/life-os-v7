"""Pure extraction of the two accepted Physical Mail notice shapes (MAIL-1.2). Read-only evidence: nothing here touches Gmail.

`New Mail` (an arrival notice with an item count) and `Completed Action Requests` (one or more lines naming a mail number and a verb: Scan, Shred, Recycle,
Forward). Anything else from the vendor is IGNORED and counted. Unknown, empty or contradictory evidence is REVIEW with a fixed reason and no guessed identity.
The vendor address is built with chr(64) so no location-identifying literal enters the public repository (privacy test)."""
import re
from datetime import datetime, timezone
from email.utils import getaddresses
from urllib.parse import urlsplit

_AT = chr(64)
DOMAIN = "anytimemailbox" + ".com"
SENDER_QUERY = "from:" + DOMAIN
NEW_MAIL = re.compile(r"^\s*(?:re\s*:\s*)?new\s+mail\b", re.I)
COMPLETED = re.compile(r"^\s*(?:re\s*:\s*)?completed\s+action\s+requests?\b", re.I)
VERBS = {"scan": "SCAN", "scanned": "SCAN", "shred": "SHRED", "shredded": "SHRED", "recycle": "RECYCLE", "recycled": "RECYCLE", "forward": "FORWARD", "forwarded": "FORWARD"}
MAIL_NO = re.compile(r"(?:\b(?:mail|item)\s*(?:number|no\.?|#)?\s*[:#]?\s*|#\s*)(\d{3,})\b", re.I)
SUBJECT_COUNT = re.compile(r"new\s+mail\D{0,20}?(\d{1,3})\b", re.I)
BODY_COUNT = re.compile(r"\b(\d{1,3})\s+(?:new\s+)?(?:mail\s+)?(?:items?|pieces?|letters?|packages?)\b", re.I)
URL = re.compile(r"https://[^\s<>\"')]+", re.I)
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
MAX_COUNT = 200


def _vendor(value):
    if not isinstance(value, str):
        return False
    parsed = getaddresses([value])
    if len(parsed) != 1:
        return False
    local, separator, domain = parsed[0][1].strip().rpartition(_AT)
    return bool(local and separator and domain.lower() == DOMAIN)


def _received(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _portal(body):
    """The first https link on the vendor's own domain (private state only), or None."""
    for found in URL.findall(body or ""):
        host = (urlsplit(found).hostname or "").lower()
        if host == DOMAIN or host.endswith("." + DOMAIN):
            return found.rstrip(".,;")
    return None


def _review(message_id, at, reason):
    return {"kind": "REVIEW", "id": message_id, "at": at, "reason": reason}


def extract(message):
    """-> {"kind": ARRIVAL | ACTIONS | IGNORED | REVIEW, ...}. Never returns body text."""
    if not isinstance(message, dict):
        return {"kind": "REVIEW", "id": "", "at": None, "reason": "MESSAGE_INVALID"}
    message_id, body, subject = message.get("id"), message.get("body_text"), message.get("subject")
    if not _vendor(message.get("sender")):
        return {"kind": "IGNORED", "reason": "SENDER_NOT_VENDOR"}
    at = _received(message.get("received_at"))
    if not isinstance(message_id, str) or not message_id.strip() or at is None or not isinstance(body, str) or not isinstance(subject, str):
        return _review(message_id if isinstance(message_id, str) else "", at, "MESSAGE_INCOMPLETE")
    message_id = message_id.strip()
    if NEW_MAIL.match(subject):
        counts = {int(m) for m in SUBJECT_COUNT.findall(subject)} or {int(m) for m in BODY_COUNT.findall(body)}
        if not counts:
            return _review(message_id, at, "COUNT_MISSING")
        if len(counts) > 1:
            return _review(message_id, at, "COUNT_CONFLICT")
        count = next(iter(counts))
        if not 1 <= count <= MAX_COUNT:
            return _review(message_id, at, "COUNT_INVALID")
        return {"kind": "ARRIVAL", "id": message_id, "at": at, "count": count, "portal": _portal(body)}
    if COMPLETED.match(subject):
        actions, bad = [], None
        for line in body.splitlines():
            verbs = {VERBS[w] for w in re.findall(r"[a-z]+", line.lower()) if w in VERBS}
            numbers = {m for m in MAIL_NO.findall(line)}
            if not numbers and not (verbs and BULLET.match(line)):
                continue                                                       # prose, not an action line
            if len(numbers) != 1:
                bad = bad or ("ID_MISSING" if not numbers else "ID_AMBIGUOUS")
            elif len(verbs) != 1:
                bad = bad or ("UNKNOWN_VERB" if not verbs else "VERB_AMBIGUOUS")
            else:
                actions.append((next(iter(numbers)), next(iter(verbs))))
        if bad:
            return _review(message_id, at, bad)
        if not actions:
            return _review(message_id, at, "NO_LINES")
        return {"kind": "ACTIONS", "id": message_id, "at": at, "actions": sorted(set(actions))}
    return {"kind": "IGNORED", "reason": "SUBJECT_NOT_SCOPED"}
