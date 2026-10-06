"""Deterministic identity for a recruiter contact: name cleaning, normalized natural key, Monday-to-Sunday week. No fuzzy matching, no guessing."""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
NOT_A_PERSON = {"team", "hr", "recruiting", "recruitment", "careers", "career", "jobs", "job", "talent", "staffing", "hiring", "inc", "llc", "ltd", "group", "solutions", "services",
                "support", "admin", "notifications", "alerts", "noreply", "no-reply", "info", "hello", "mail", "mailer", "the", "of", "and", "at", "apply", "applications"}
TOKEN = re.compile(r"^[A-Za-z][A-Za-z'’.\-]*$")
VIA = re.compile(r"\s+(?:via|\(via|from|at|\||-|–|—|,)\s+.*$|\s*\(.*?\)\s*", re.I)


def clean_name(display, allow_single=False):
    """A human name from a sender display name, or None. 'Last, First' is reordered; trailing 'via Board' / 'at Company' / '(...)' is dropped."""
    text = str(display or "").strip().strip("\"'")
    if "," in text and text.count(",") == 1 and "@" not in text:
        last, first = [part.strip() for part in text.split(",")]
        if first and last and " " not in last and len(first.split()) <= 2:
            text = f"{first} {last}"
    text = VIA.sub("", text).strip()
    tokens = text.split()
    if not 1 <= len(tokens) <= 5 or any(not TOKEN.match(t) or t.casefold().strip(".") in NOT_A_PERSON for t in tokens):
        return None
    if len(tokens) == 1:
        return text if allow_single and len(text) >= 2 else None
    return text if sum(len(t.strip(".")) >= 2 for t in tokens) >= 2 else None


def norm(value):
    """The comparison form of a name, company or role: case, spacing and edge punctuation only."""
    return re.sub(r"\s+", " ", str(value or "")).strip().strip(".,;:").casefold()


def key(week_ending, person, company, role):
    return (week_ending, norm(person), norm(company), norm(role))


def week_ending(moment):
    """The Sunday (ISO date) of the Monday-to-Sunday week, in Chicago time, that holds `moment` (an aware datetime or an ISO string)."""
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    local = moment.astimezone(CHICAGO)
    return (local.date() + timedelta(days=6 - local.weekday())).isoformat()
