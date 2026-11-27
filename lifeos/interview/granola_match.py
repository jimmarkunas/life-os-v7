"""Which Interview OS round does a Granola meeting belong to? Pure, no I/O.

A meeting matches a round when it falls on the round's Central interview date AND at least one of: the company name
is in the title or description; the requisition or two distinctive role words are; an attendee matches the
interviewer; an attendee's email domain is the company's (or the recruiter/agency's). Exactly one round must match a
meeting and exactly one meeting that round; anything else is no write and a fixed code. Titles, names and addresses
are never logged."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.platform.names import core, norm, tokens

CENTRAL = ZoneInfo("America/Chicago")
ROLE_STOP = {"and", "for", "the", "of", "to", "in", "at", "with", "senior", "sr", "lead", "manager", "principal"}


@dataclass(frozen=True)
class Meeting:
    id: str
    start: str
    title: str = ""
    description: str = ""
    attendees: tuple = ()


@dataclass(frozen=True)
class Round:
    page_id: str
    interview_date: str
    company: str
    role: str = ""
    interviewer: str = ""
    requisition: str = ""
    company_domain: str = ""
    agency_domain: str = ""


def central_day(start):
    """The Central calendar day of a zoned ISO timestamp, or None when missing, naive or unreadable."""
    try:
        moment = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(CENTRAL).date().isoformat() if moment.tzinfo else None


def _has_all(words, haystack):
    return bool(words) and all(f" {w} " in haystack for w in words)


def _domains(attendees):
    return {a.rsplit("@", 1)[1].strip().lower() for a in attendees if "@" in a and a.rsplit("@", 1)[1].strip()}


def reasons(meeting, row):
    """Which signals (company, role, interviewer, domain) tie this meeting to this round."""
    text = norm(f"{meeting.title} {meeting.description}")
    found = set()
    if _has_all(core(row.company), text):
        found.add("company")
    role = [t for t in tokens(row.role) if len(t) > 2 and t not in ROLE_STOP]
    if (
        len(row.requisition.strip()) >= 4
        and row.requisition.strip().lower() in f"{meeting.title} {meeting.description}".lower()
    ) or (len(role) >= 2 and _has_all(role, text)):
        found.add("role")
    who = tokens(row.interviewer)
    if who and any(
        _has_all(who, norm(a.split("@")[0].replace(".", " ").replace("_", " "))) or _has_all(who, norm(a))
        for a in meeting.attendees
    ):
        found.add("interviewer")
    wanted = {d.strip().lower() for d in (row.company_domain, row.agency_domain) if d and d.strip()}
    if wanted & _domains(meeting.attendees):
        found.add("domain")
    return found


def match(meetings, rounds):
    """-> {meeting id: (code, round page id or None)}.

    Codes: MATCH, MATCH_NONE, MATCH_MANY_ROUNDS, MATCH_MANY_MEETINGS, MEETING_TIME_INVALID."""
    day = {m.id: central_day(m.start) for m in meetings}
    hits = {
        m.id: [r for r in rounds if day[m.id] and r.interview_date == day[m.id] and reasons(m, r)] for m in meetings
    }
    taken = {}
    for m in meetings:
        for r in hits[m.id]:
            taken.setdefault(r.page_id, []).append(m.id)
    out = {}
    for m in meetings:
        if day[m.id] is None:
            out[m.id] = ("MEETING_TIME_INVALID", None)
        elif not hits[m.id]:
            out[m.id] = ("MATCH_NONE", None)
        elif len(hits[m.id]) > 1:
            out[m.id] = ("MATCH_MANY_ROUNDS", None)
        elif len(taken[hits[m.id][0].page_id]) > 1:
            out[m.id] = ("MATCH_MANY_MEETINGS", None)
        else:
            out[m.id] = ("MATCH", hits[m.id][0].page_id)
    return out


def explain(meeting, rounds):
    """Counts only, for a stop: rounds on the meeting's day, and how many each signal ties to it."""
    day = central_day(meeting.start)
    on_day = [r for r in rounds if day and r.interview_date == day]
    out = {
        "rounds_on_day": len(on_day),
        "signal_company": 0,
        "signal_role": 0,
        "signal_interviewer": 0,
        "signal_domain": 0,
    }
    for r in on_day:
        for name in reasons(meeting, r):
            out["signal_" + name] += 1
    return out
