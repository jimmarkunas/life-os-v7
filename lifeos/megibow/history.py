"""MegIBOW history (D148): the weeks before V7 took over, recorded from Jim's own Google Sheet and shown in their weeks, never added to Cumulative twice.

Counts only: no names, no addresses."""
# Why these numbers and not V7's reading of the mailbox (D148): a recruiter who phoned Jim's mobile leaves no mail or calendar trace, so the sheet is the history.
# The weeks are already inside the Legacy totals seeded in the Notion block (Outreach 20, Scheduled 13, Networking Calls 11, Recruiter Calls 36, Company Calls 15 = 95).
# Each sheet week-of date is placed in the Monday to Sunday week that contains it.
from datetime import date

ORDER = ("Outreach", "Scheduled", "Networking Calls", "Recruiter Calls", "Company Calls")


def _week(*counts):
    return dict(zip(ORDER, counts))


WEEKS = {
    date(2026, 8, 17): _week(4, 0, 1, 5, 4),      # sheet week of 8/20
    date(2026, 8, 24): _week(4, 1, 5, 3, 0),      # 8/27
    date(2026, 8, 31): _week(1, 0, 0, 3, 1),      # 9/3
    date(2026, 9, 7): _week(1, 3, 0, 2, 1),       # 9/10
    date(2026, 9, 14): _week(1, 1, 2, 1, 3),      # 9/17
    date(2026, 9, 21): _week(0, 1, 1, 3, 1),      # 9/24
    date(2026, 9, 28): _week(0, 1, 0, 1, 1),      # 10/1
}
