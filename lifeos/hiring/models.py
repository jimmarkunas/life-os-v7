"""Hiring Pipeline vocabulary (Production Contract, Hiring Pipeline) and the one identity rule. Pure: no I/O.

A stage appears only when deterministic evidence supports it: nothing here is a default. `rank` orders the stages the first slice can establish, so a missing source can never roll a row backward."""
import re

from lifeos.platform import names

TITLE = "📈 Hiring Pipeline"                                   # the floating heading, exactly (router.HIRING_REGION)
HEADERS = ("Company / Role", "Stage / Update", "Action Needed", "Source")
NOTES_LABEL, NO_NOTES = "Opportunity Notes", "Needs Notes"

SUBMITTED, RECRUITER_SCREEN, HM_SCHEDULED, INTERVIEWING, ASSESSMENT = (
    "Submitted", "Recruiter Screen", "Hiring-Manager Interview Scheduled", "Interviewing", "Assessment")
RANK = {SUBMITTED: 1, RECRUITER_SCREEN: 2, ASSESSMENT: 2, HM_SCHEDULED: 3, INTERVIEWING: 4}
AWAIT_DAYS = 14                                                # D189: a role waiting on an outcome or a response this long says so and asks for a follow-up (it is still listed)
APPLIED_DAYS = 30                                              # a submitted role with no newer evidence is listed for 30 days after Applied On, then counted in the status line, never dropped silently

KINDS = ("assessment", "hiring_manager", "recruiter_screen", "interview")         # calendar evidence kinds, most specific first


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
