"""Hiring Pipeline vocabulary (Production Contract, Hiring Pipeline) and the one identity rule. Pure: no I/O.

A stage appears only when deterministic evidence supports it: nothing here is a default. `rank` orders the stages the first slice can establish, so a missing source can never roll a row backward."""
import re
from datetime import datetime

from lifeos.platform import names

TITLE = "📈 Hiring Pipeline"                                   # the floating heading, exactly (router.HIRING_REGION)
HEADERS = ("Company — Role", "Stage", "Next step · Due", "Notes")                   # LIFE-47 (Jim, comment 10177)
OLD_HEADERS = ("Company / Role", "Stage / Update", "Action Needed", "Source")        # recognised only to migrate the header row once
NOTES_LABEL, NO_NOTES = "Opportunity Page ↗", "Needs Notes"

SUBMITTED, RECRUITER_SCREEN, HM_SCHEDULED, INTERVIEWING, ASSESSMENT = (
    "Submitted", "Recruiter Screen", "Hiring-Manager Interview Scheduled", "Interviewing", "Assessment")
LEAD = "Recruiter lead"                                       # E01b: a Notes page for recruiter outreach with no application, round or event
LEAD_REASONS = {"I'm passing": ("Withdrew", "Passed: not interested"), "Not a real job": ("Other", "Not a real job"), "Went nowhere": ("Other", "Went nowhere")}      # the Close screen's three lead reasons, recorded as an existing outcome plus a fixed note
RANK = {SUBMITTED: 1, RECRUITER_SCREEN: 2, ASSESSMENT: 2, HM_SCHEDULED: 3, INTERVIEWING: 4}
AWAIT_DAYS = 7                                                 # LIFE-26 (supersedes D189's 14): 7 calendar days with no direct contact since the last evidence is Stale, a reversible follow-up prompt, never a closure; it is still listed
APPLIED_DAYS = 30                                              # a submitted role with no newer evidence is listed for 30 days after Applied On, then counted in the status line, never dropped silently

OUTCOMES = ("Rejected", "Withdrew", "Position filled", "Declined offer", "Hired", "Other")      # the six distinct closure outcomes: each is its own value, none stands in for another
CLOSED_DECISIONS = OUTCOMES + ("Passed",)                      # D194: a Job Ledger Hiring Decision a person confirmed; it closes the pursuit whatever Notes pages say (Applied stays as history)
KINDS = ("assessment", "hiring_manager", "recruiter_screen", "interview")         # calendar evidence kinds, most specific first


# LIFE-111 / LIFE-112 (Jim, Oct 25 2026): what Jim sets by hand on an opportunity's own record, one line each: in its Notes page's ⚙️ LIFE OS fold, or in the Job Ledger row's
# decision.HAND_SET property. Evidence stages are never hand-set. The latest line of each kind (by its `at`) is the state; `active`, or an empty next action, clears it.
PAUSED, WAITING, ACTIVE = "paused", "waiting", "active"
HOLD_LABEL = {PAUSED: "Paused", WAITING: "Waiting on employer"}
_AT = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"
HOLD_LINE = re.compile(rf"v7-hold:1;state=({PAUSED}|{WAITING}|{ACTIVE});at=({_AT});op=([0-9a-f]{{16}})")          # the Interview writer keeps its own copies (contract-tested)
NEXT_LINE = re.compile(rf"v7-next:1;due=(\d{{4}}-\d{{2}}-\d{{2}}|none);at=({_AT});op=([0-9a-f]{{16}});text=(.*)")
NEXT_MAX = 200                                                 # a hand-entered next action longer than this is refused, never cut
# LIFE-114 (Jim, Oct 29): sent material Jim records on Manage, one line each, kept with the hold and next-action lines: an application form or portal answers (an optional link),
# or a Google Drive file (a project plan, an article). Each line is its own record; none replaces another.
FORM, FILE = "form", "file"
SENT_KINDS = {FORM: "Application form / portal answers", FILE: "Google Drive file"}
SENT_LINE = re.compile(rf"v7-sent:1;kind=({FORM}|{FILE});at=({_AT});op=([0-9a-f]{{16}});url=(https://\S+|none);text=(.*)")
SENT_MAX = 20                                                  # sent-material lines kept per record, newest last
UNSENT_LINE = re.compile(rf"v7-unsent:1;at=({_AT});op=([0-9a-f]{{16}});of=([0-9a-f]{{16}})")     # LIFE-172 (Jim, Oct 30): a sent item removed on Manage, by its op; append-only


def hold_line(state, at, op):
    return f"v7-hold:1;state={state};at={at};op={op}"


def next_line(text, due, at, op):
    return f"v7-next:1;due={due or 'none'};at={at};op={op};text={text}"


def sent_line(kind, text, url, at, op):
    return f"v7-sent:1;kind={kind};at={at};op={op};url={url or 'none'};text={text}"


def unsent_line(of, at, op):
    return f"v7-unsent:1;at={at};op={op};of={of}"


def hand_lines(text):
    """The hand-set lines in a record's text, in order (holds, next actions and LIFE-114 sent material)."""
    return tuple(t for t in (text or "").splitlines() if HOLD_LINE.fullmatch(t) or NEXT_LINE.fullmatch(t) or SENT_LINE.fullmatch(t) or UNSENT_LINE.fullmatch(t))


def sent_of(lines):
    """LIFE-114: -> [{"kind", "text", "url" or None, "at", "op"}] of every sent-material line, oldest first; a repeated op counts once and (LIFE-172) a removed one not at all."""
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
    """-> {"state", "at", "op"} of the latest hold line, or None when there is none or it says Active."""
    m = _latest(lines, HOLD_LINE)
    return {"state": m.group(1), "at": m.group(2), "op": m.group(3)} if m and m.group(1) != ACTIVE else None


def next_of(lines):
    """-> {"text", "due", "at", "op"} of the latest next-action line, or None when there is none or it is empty. `due` is exactly the date Jim typed, or None."""
    m = _latest(lines, NEXT_LINE)
    if not m or not (m.group(4).strip() or m.group(1) != "none"):
        return None
    return {"text": m.group(4).strip(), "due": None if m.group(1) == "none" else m.group(1), "at": m.group(2), "op": m.group(3)}


_DEGRADED = re.compile(r"^DEGRADED · (?P<what>.*?) at (?P<clock>\d{1,2}:\d{2} [AP]M CT)(?P<rest> · .*)?$")


def split_status(text):
    """The compiled status line -> (the stamp the heading carries, the paragraph under it). `Updated 2:00 PM CT · 5 active` -> (`Updated 2:00 PM CT`, `5 active`); a degraded line puts `DEGRADED at 2:00 PM CT`
    in the heading and what is wrong in the paragraph, so the state is the first thing read and the Command Center reads the same words it always did."""
    text = (text or "").strip()
    found = _DEGRADED.match(text)
    if found:
        return f"DEGRADED at {found.group('clock')}", (found.group("what") + (found.group("rest") or "")).strip()
    if text.startswith("DEGRADED"):
        return "DEGRADED", text[len("DEGRADED"):].lstrip(" ·")
    head, _, rest = text.partition(" · ")
    return head, rest


def heading_text(stamp):
    return f"{TITLE} ({stamp})" if stamp else TITLE


def same(a_company, a_role, b_company, b_role):
    """One opportunity: the company matches AND the role matches (platform.names, the same rule Jobs and Interview use). No fuzzy guessing beyond it."""
    return names.same_company(a_company, b_company) and names.same_role(a_role, b_role)


def label(company, role):
    return f"{company} — {role}" if role else company
