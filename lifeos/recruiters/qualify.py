"""Turn a classified message into a Recruiters candidate: only source-backed fields, never a guess. -> ("CANDIDATE", fields) | ("UNRESOLVED", code) | ("EXCLUDED", code).

Person: a human name from the sender. Company: only from the signature line or a first-person statement. Client: only when the source says so explicitly. Role: only from a fixed
pattern. A missing company, or a relay, makes the row Needs review; a name that cannot be read at all is UNRESOLVED (no row, nothing invented)."""
import re
from datetime import datetime

from . import classify as C
from .identity import clean_name, week_ending

CLIENT = re.compile(r"(?:\bon behalf of\b|\bour client,?|\bmy client,?)\s+(?P<c>[A-Z][A-Za-z0-9&'’.\- ]{1,50}?)\s*(?:[,.;]|\bis\b|\bhas\b|\bare\b|$)")
ROLE = re.compile(r"\b(?:for|regarding|about)\s+(?:a|an|the|our)\s+(?P<r>[A-Z][A-Za-z0-9/&+.\- ]{2,60}?)\s+(?:role|position|opening|opportunity)\b")
TITLE_AT = re.compile(rf"{C.TITLE}\s*(?:(?:\bat\b|\bwith\b|[|@,\-–—])\s*)(?P<c>[A-Z][A-Za-z0-9&'’.\- ]{{1,60}}?)\s*(?:$|[|,.;(])", re.I)
ORG_LINE = re.compile(r"^[\s|\-–—•*]*(?P<c>[A-Z][A-Za-z0-9&'’.\- ]{1,50}?\s(?:Inc\.?|LLC|L\.L\.C\.|Ltd\.?|Corp\.?|Corporation|Technologies|Technology|Solutions|Systems|Staffing|Consulting|Group|Networks|Services|Partners|Global|Labs|Software|Infotech))[\s|,.\-–—]*$")
MEDIUM = "Email"


def minute(moment):
    """The message time to the minute, as ISO 8601. Notion stores date-times to the minute, so comparing and reading back at full precision would never match."""
    return datetime.fromisoformat(str(moment).replace("Z", "+00:00")).replace(second=0, microsecond=0).isoformat()


def _company(evidence):
    for text in (evidence.get("signature", ""), evidence.get("first_person", "")):
        found = TITLE_AT.search(text.strip())
        if found:
            return found.group("c").strip()
    return ""


def _org_from_signature(body):
    """A closing line that is only an organization name (it ends in a company word such as Inc, LLC, Solutions or Staffing), or ''."""
    for line in [l.strip() for l in str(body or "").splitlines() if l.strip()][-14:]:
        found = ORG_LINE.match(line) if len(line) <= 70 else None
        if found:
            return found.group("c").strip()
    return ""


def _search(pattern, group, *texts):
    for text in texts:
        found = pattern.search(text or "")
        if found:
            return found.group(group).strip()
    return ""


def qualify(message, shape, reason, evidence):
    if shape == C.OTHER:
        return "EXCLUDED", reason
    relay = shape == C.HUMAN_NAMED_RELAY
    person = clean_name(evidence.get("name", ""), allow_single=relay)
    if not person:
        return "UNRESOLVED", "NAME_UNREADABLE"
    company = "" if relay else (_company(evidence) or _org_from_signature(message.get("body", "")))
    body = message.get("body", "")[:6000]
    client = _search(CLIENT, "c", body)
    role = _search(ROLE, "r", message.get("subject", ""), body)
    review = relay or not company or len(person.split()) < 2
    return "CANDIDATE", {"person": person, "company": company, "client": client, "role": role, "medium": MEDIUM, "last_contact": minute(message["received"]),
                         "week_ending": week_ending(message["received"]), "source_url": message.get("url", ""), "needs_review": review, "shape": shape}
