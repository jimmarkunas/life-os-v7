"""Hiring Pipeline vocabulary (Production Contract, Hiring Pipeline) and the one identity rule. Pure: no I/O.

A stage appears only when deterministic evidence supports it: nothing here is a default. `rank` orders the stages the first slice can establish, so a missing source can never roll a row backward."""
from lifeos.platform import names

TITLE = "📈 Hiring Pipeline"                                   # the floating heading, exactly (router.HIRING_REGION)
HEADERS = ("Company / Role", "Stage / Update", "Action Needed", "Source")
NOTES_LABEL, NO_NOTES = "Opportunity Notes", "Needs Notes"

SUBMITTED, RECRUITER_SCREEN, HM_SCHEDULED, INTERVIEWING, ASSESSMENT = (
    "Submitted", "Recruiter Screen", "Hiring-Manager Interview Scheduled", "Interviewing", "Assessment")
RANK = {SUBMITTED: 1, RECRUITER_SCREEN: 2, ASSESSMENT: 2, HM_SCHEDULED: 3, INTERVIEWING: 4}
APPLIED_DAYS = 30                                              # a submitted role with no newer evidence is listed for 30 days after Applied On, then counted in the status line, never dropped silently

KINDS = ("assessment", "hiring_manager", "recruiter_screen", "interview")         # calendar evidence kinds, most specific first


def same(a_company, a_role, b_company, b_role):
    """One opportunity: the company matches AND the role matches (platform.names, the same rule Jobs and Interview use). No fuzzy guessing beyond it."""
    return names.same_company(a_company, b_company) and names.same_role(a_role, b_role)


def label(company, role):
    return f"{company} — {role}" if role else company
