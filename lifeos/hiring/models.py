"""Hiring Pipeline vocabulary and the one identity rule. Pure: no I/O.

A stage appears only when deterministic evidence supports it: nothing here is a default. `rank` orders the stages so
a missing source can never roll a row backward."""
import re
from datetime import datetime, timezone

from lifeos.platform import names

TITLE = "📈 Hiring Pipeline"                                   # the floating heading, exactly (router.HIRING_REGION)
HEADERS = ("Company — Role", "Stage", "Next step · Due", "Notes")
OLD_HEADERS = ("Company / Role", "Stage / Update", "Action Needed", "Source")
NOTES_LABEL, NO_NOTES = "Opportunity Page ↗", "Needs Notes"

SUBMITTED, RECRUITER_SCREEN, HM_SCHEDULED, INTERVIEWING, ASSESSMENT = (
    "Submitted", "Recruiter Screen", "Hiring-Manager Interview Scheduled", "Interviewing", "Assessment")
LEAD = "Recruiter lead"
LEAD_REASONS = {"I'm passing": ("Withdrew", "Passed: not interested"), "Not a real job": ("Other", "Not a real job"), "Went nowhere": ("Other", "Went nowhere")}
RANK = {SUBMITTED: 1, RECRUITER_SCREEN: 2, ASSESSMENT: 2, HM_SCHEDULED: 3, INTERVIEWING: 4}
AWAIT_DAYS = 7                                                 # 7 calendar days without direct contact since the last evidence is Stale: a reversible prompt, never a closure
APPLIED_DAYS = 30

OUTCOMES = ("Rejected", "Withdrew", "Position filled", "Declined offer", "Hired", "Other")
CLOSED_DECISIONS = OUTCOMES + ("Passed",)
KINDS = ("assessment", "hiring_manager", "recruiter_screen", "interview")


# Hand-set state is one line each, in a Notes page's LIFE OS fold or the Job Ledger row's decision.HAND_SET property.
# The latest line of each kind is the state; `active` or an empty next action clears it. Stages are never hand-set.
PAUSED, WAITING, ACTIVE = "paused", "waiting", "active"
HOLD_LABEL = {PAUSED: "Paused", WAITING: "Waiting on employer"}
_AT = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"
HOLD_LINE = re.compile(rf"v7-hold:1;state=({PAUSED}|{WAITING}|{ACTIVE});at=({_AT});op=([0-9a-f]{{16}})")          # the Interview writer keeps its own copies (contract-tested)
NEXT_LINE = re.compile(rf"v7-next:1;due=(\d{{4}}-\d{{2}}-\d{{2}}|none);at=({_AT});op=([0-9a-f]{{16}});text=(.*)")
NEXT_MAX = 200
FORM, FILE = "form", "file"
SENT_KINDS = {FORM: "Application form / portal answers", FILE: "Google Drive file"}
SENT_LINE = re.compile(rf"v7-sent:1;kind=({FORM}|{FILE});at=({_AT});op=([0-9a-f]{{16}});url=(https://\S+|none);text=(.*)")
SENT_MAX = 20
UNSENT_LINE = re.compile(rf"v7-unsent:1;at=({_AT});op=([0-9a-f]{{16}});of=([0-9a-f]{{16}})")


def hold_line(state, at, op):
    return f"v7-hold:1;state={state};at={at};op={op}"


def next_line(text, due, at, op):
    return f"v7-next:1;due={due or 'none'};at={at};op={op};text={text}"


def sent_line(kind, text, url, at, op):
    return f"v7-sent:1;kind={kind};at={at};op={op};url={url or 'none'};text={text}"


def unsent_line(of, at, op):
    return f"v7-unsent:1;at={at};op={op};of={of}"


def hand_lines(text):
    return tuple(t for t in (text or "").splitlines() if HOLD_LINE.fullmatch(t) or NEXT_LINE.fullmatch(t) or SENT_LINE.fullmatch(t) or UNSENT_LINE.fullmatch(t))


def sent_of(lines):
    """-> [{"kind", "text", "url" or None, "at", "op"}] of every sent-material line, oldest first."""
    out, seen = [], {m.group(3) for m in map(UNSENT_LINE.fullmatch, lines) if m}
    for m in sorted(filter(None, map(SENT_LINE.fullmatch, lines)), key=lambda m: datetime.fromisoformat(m.group(2))):
        if m.group(3) not in seen:
            seen.add(m.group(3))
            out.append({"kind": m.group(1), "text": m.group(5).strip(), "url": None if m.group(4) == "none" else m.group(4), "at": m.group(2), "op": m.group(3)})
    return out


def _latest(lines, pattern):
    found = [(datetime.fromisoformat(m.group(2)), i, m) for i, m in enumerate(map(pattern.fullmatch, lines)) if m]
    return max(found, key=lambda f: f[:2])[2] if found else None


def hold_of(lines):
    m = _latest(lines, HOLD_LINE)
    return {"state": m.group(1), "at": m.group(2), "op": m.group(3)} if m and m.group(1) != ACTIVE else None


def next_of(lines):
    """-> {"text", "due", "at", "op"} of the latest next-action line, or None when there is none or it is empty."""
    m = _latest(lines, NEXT_LINE)
    if not m or not (m.group(4).strip() or m.group(1) != "none"):
        return None
    return {"text": m.group(4).strip(), "due": None if m.group(1) == "none" else m.group(1), "at": m.group(2), "op": m.group(3)}


WHEN = r"(?:(?:[A-Z][a-z]{2} \d{1,2}|\d{1,2}/\d{1,2}) · )?\d{1,2}:\d{2} [AP]M CT"       # `11/13 · 3:31 PM CT`; the `Nov 13 · ...` and time-only forms are read too (no ct_stamp import: shipped in the Close closure)
_DEGRADED = re.compile(rf"^DEGRADED · (?P<what>.*?) at (?P<clock>{WHEN})(?P<rest> · .*)?$")
_UPDATED = re.compile(rf"^(?P<stamp>(?:Updated )?{WHEN})(?: · (?P<rest>.*))?$")


def split_status(text):
    """The compiled status line -> (the stamp the heading carries, the paragraph under it).

    `11/13 · 2:00 PM CT · 5 active` -> (`11/13 · 2:00 PM CT`, `5 active`); a degraded line puts `DEGRADED at ...`
    in the heading and what is wrong in the paragraph."""
    text = (text or "").strip()
    found = _DEGRADED.match(text)
    if found:
        return f"DEGRADED at {found.group('clock')}", (found.group("what") + (found.group("rest") or "")).strip()
    if text.startswith("DEGRADED"):
        return "DEGRADED", text[len("DEGRADED"):].lstrip(" ·")
    found = _UPDATED.match(text)
    if found:
        return found.group("stamp"), found.group("rest") or ""
    head, _, rest = text.partition(" · ")
    return head, rest


def heading_text(stamp):
    return f"{TITLE} ({stamp})" if stamp else TITLE


def same(a_company, a_role, b_company, b_role):
    return names.same_company(a_company, b_company) and names.same_role(a_role, b_role)


def label(company, role):
    return f"{company} — {role}" if role else company


def contains(haystack, needle):
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def moment(value):
    """An ISO date or time as an aware moment: a bare date is the start of that day, Central; no zone means UTC."""
    at = datetime.fromisoformat(value if "T" in value else value + "T00:00:00-05:00")
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
