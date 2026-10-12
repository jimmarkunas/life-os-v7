"""Turn a classified message into a Recruiters candidate: only source-backed fields, never a guess. -> ("CANDIDATE", fields) | ("UNRESOLVED", code) | ("EXCLUDED", code).

Person: a human name from the sender. Company: only from the signature line or a first-person statement ("I'm with (Acme)", "I work with Acme"). Client: only when the source
says so explicitly. Role: only from a fixed pattern ("for a X role", or a "Role: [requisition -] X" line). What they want: only a fixed request pattern (intent.py).
A missing company, a relay or an ambiguous request makes the row Needs review; a name that cannot be read at all is UNRESOLVED (no row, nothing invented), and so is a LinkedIn
notification with no recruiting evidence (D185)."""
import re
from datetime import datetime

from . import classify as C
from .identity import clean_name, week_ending
from .intent import request

CLIENT = re.compile(r"(?:\bon behalf of\b|\bour client,?|\bmy client,?)\s+(?P<c>[A-Z][A-Za-z0-9&'’.\- ]{1,50}?)\s*(?:[,.;]|\bis\b|\bhas\b|\bare\b|$)")
ROLE = re.compile(r"\b(?:for|regarding|about)\s+(?:a|an|the|our)\s+(?P<r>[A-Z][A-Za-z0-9/&+.\- ]{2,60}?)\s+(?:role|position|opening|opportunity)\b")
TITLE_AT = re.compile(rf"{C.TITLE}\s*(?:(?:\bat\b|\bwith\b|[|@,\-–—])\s*)(?P<c>[A-Z][A-Za-z0-9&'’.\- ]{{1,60}}?)\s*(?:$|[|,.;(])", re.I)
ORG_LINE = re.compile(r"^[\s|\-–—•*]*(?P<c>[A-Z][A-Za-z0-9&'’.\- ]{1,50}?\s(?:Inc\.?|LLC|L\.L\.C\.|Ltd\.?|Corp\.?|Corporation|Technologies|Technology|Solutions|Systems|Staffing|Consulting|Group|Networks|Services|Partners|Global|Labs|Software|Infotech))[\s|,.\-–—]*$")
ORG = r"\(?(?P<c>[A-Z0-9][A-Za-z0-9&'’.\-]*(?:\s+[A-Za-z0-9&'’.\-]+){0,4}?)\)?(?=\s*[,;:\n]|\.(?:\s|$)|\s+(?:and|a|an|the|who|which|where|in|on|for|as)\b|$)"
STATED_COMPANY = re.compile(r"\bI(?:(?:'m|’m|\s+am)\s+(?:with|at|from)|\s+work\s+(?:with|for|at))\s+" + ORG)
ROLE_LABEL = re.compile(r"^[ \t>*•\-]*(?:role|position|job title)[ \t]*[:：][ \t]*(?P<v>[^\n]{2,120}?)[ \t]*$", re.I | re.M)
REQUISITION = re.compile(r"^(?:(?:req(?:uisition)?|job|ref)\s*(?:id|no\.?|number|#)?\s*[:#]?\s*)?(?P<req>[A-Z]{0,4}[-_]?\d{3,10})\s*[-–—:|]\s*(?P<r>\S.*)$", re.I)
NOT_A_TITLE = {"remote", "hybrid", "onsite", "on-site", "full time", "full-time", "part time", "part-time", "contract", "w2", "c2c", "tbd", "n/a", "open", "various"}
NOT_AN_ORG = {"you", "your", "me", "us", "our", "my", "them", "this", "that", "one", "several", "many", "top", "leading", "various", "clients", "companies", "team", "everyone"}
SUFFIX_DOT = {"inc", "corp", "ltd", "co", "llc", "l.l.c", "l.p", "plc"}
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


def _stated_company(body):
    """The organization a first-person statement names ("I'm with (Acme)", "I work with Acme Staffing"), or ''. Title Case words only, five at most, no generic noun:
    'I work with Fortune 500 companies' names no one."""
    for found in STATED_COMPANY.finditer(str(body or "")[:4000]):
        name = found.group("c").strip(" ,;:")
        words = name.split()
        if name.endswith(".") and words[-1].casefold().rstrip(".") not in SUFFIX_DOT:
            name = name.rstrip(".")
            words = name.split()
        if not words or len(words) > 5 or words[0].casefold() in NOT_AN_ORG:
            continue
        if all(w[0].isupper() or w[0].isdigit() or w[0] == "&" for w in words):
            return name
    return ""


def _role(subject, body):
    """-> (role, requisition). The sentence pattern first (so a row written before this label rule keeps its key), then a 'Role: [requisition -] Title' line."""
    role = _search(ROLE, "r", subject, body)
    labeled = ROLE_LABEL.search(body or "")
    req = ""
    if labeled:
        value = labeled.group("v").strip()
        found = REQUISITION.match(value)
        title = (found.group("r") if found else value).strip(" .;|")
        if found and len(title) <= 80:
            req = found.group("req").strip()
        if not role and 3 <= len(title) <= 80 and len(title.split()) <= 10 and not title.endswith(("?", ":")) and title.casefold() not in NOT_A_TITLE:
            role = title
        elif role and role.casefold() != title.casefold():
            req = ""                                                  # the requisition belongs to the labeled title, not to a different sentence-pattern role
    return role, req


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
    if relay and reason == C.RELAY_UNVERIFIED:
        return "UNRESOLVED", reason                                   # a name and a notification are not recruiting evidence
    body = message.get("body", "")[:6000]
    company = "" if relay else (_company(evidence) or _stated_company(body) or _org_from_signature(message.get("body", "")))
    client = _search(CLIENT, "c", body)
    role, requisition = _role(message.get("subject", ""), body)
    what, ambiguous = ("", False) if relay else request(body, role, requisition)
    review = relay or not company or len(person.split()) < 2 or ambiguous
    return "CANDIDATE", {"person": person, "company": company, "client": client, "role": role, "medium": MEDIUM, "what": what, "last_contact": minute(message["received"]),
                         "week_ending": week_ending(message["received"]), "source_url": message.get("url", ""), "needs_review": review, "shape": shape}
