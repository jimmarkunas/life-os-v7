"""MegIBOW history (D148): the weeks before V7 took over, from Jim's own Google Sheet "Megibow Dashboard" (his week-of dates, each placed in the Monday to Sunday week that contains it).

V7 cannot see everything Jim did before the cutover (a recruiter who phoned his mobile leaves no mail or calendar trace), so these recorded numbers, not V7's reading of the mailbox, are the history.
They are already inside the Legacy totals seeded in the Notion block (Outreach 20, Scheduled 13, Networking Calls 11, Recruiter Calls 36, Company Calls 15 = 95), so they are SHOWN in their weeks and never added to Cumulative a second time.
Counts only: no names, no addresses."""
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
