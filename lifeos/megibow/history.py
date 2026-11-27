"""MegIBOW history: the weeks before V7 took over, recorded from a Google Sheet and shown in their weeks, never added to Cumulative twice.

Counts only: no names, no addresses.
"""

# These numbers, not V7's reading of the mailbox: a recruiter who phoned the mobile leaves no mail or calendar trace,
# so the sheet is the history.
# The weeks are already inside the Legacy totals seeded in the Notion block (Outreach 20, Scheduled 13, Networking Calls 11, Recruiter Calls 36, Company Calls 15 = 95).
# Each sheet week-of date is placed in the Monday to Sunday week that contains it.
from datetime import date

ORDER = ("Outreach", "Scheduled", "Networking Calls", "Recruiter Calls", "Company Calls")


def _week(*counts):
    return dict(zip(ORDER, counts))


WEEKS = {
    date(2026, 8, 17): _week(4, 0, 1, 5, 4),
    date(2026, 8, 24): _week(4, 1, 5, 3, 0),
    date(2026, 8, 31): _week(1, 0, 0, 3, 1),
    date(2026, 9, 7): _week(1, 3, 0, 2, 1),
    date(2026, 9, 14): _week(1, 1, 2, 1, 3),
    date(2026, 9, 21): _week(0, 1, 1, 3, 1),
    date(2026, 9, 28): _week(0, 1, 0, 1, 1),
}
