"""The hourly router contract for the ChatGPT-native `LIFE OS Daily Runs` task: the three outcome words and strict
ownership of Daily Report regions. Fixed codes only.
"""
PASS, NO_ACTION, DEGRADED = "PASS", "NO_ACTION", "DEGRADED"

# A region is a callout whose first child heading names its owner; nothing else is writable.
JIRA_REGION = "JIRA Execution"  # lifeos/jira/card.py; ChatGPT modules never touch it
CALENDAR_REGION = "Calendar"  # lifeos/agenda/card.py
BILLS_REGION = "Bills: This Week"  # lifeos/bills/card.py
AMAZON_REGION = "Amazon Orders"  # lifeos/amazon/card.py
MAIL_ALERTS_REGION = "Mail Alerts"  # lifeos/physmail/card.py
HIRING_REGION = "📈 Hiring Pipeline"  # lifeos/hiring/card.py
# The one region the Daily Command Center module may write (stage `dcc-card`); matched by heading text.
DCC_REGION = "ChatGPT · Daily Command Center"
COMMS_REGION = "Communications Attention"  # V7 owns only the Messages row and heading time (imessage/comms_card.py)
ORACLE_REGION = "System"  # lifeos/oraclecloud/card.py: the heading and the cells of its one table
RECRUITER_OUTREACH_REGION = "🤝 Recruiter Outreach"  # lifeos/recruiters/outreach_card.py
# Not on the Daily Report: a callout on the Weekly LIFE OS Review page (lifeos/platform/branches.py).
REPO_REGION = "🧹 Repository hygiene"
OWNERS = {JIRA_REGION: "v7-jira", CALENDAR_REGION: "v7-calendar", BILLS_REGION: "v7-bills", AMAZON_REGION: "v7-amazon", MAIL_ALERTS_REGION: "v7-mail-alerts", HIRING_REGION: "v7-hiring", DCC_REGION: "daily-command-center", COMMS_REGION: "v7-comms", ORACLE_REGION: "v7-oracle", REPO_REGION: "v7-repo-hygiene", RECRUITER_OUTREACH_REGION: "v7-recruiter-outreach"}

# owned regions that are NOT callouts
FLOATING = (HIRING_REGION, RECRUITER_OUTREACH_REGION)


class RouterError(RuntimeError):
    """The message is always a fixed code."""


def check_write(module, headings):
    """Every region a write touches must be owned by `module`; unknown regions fail closed."""
    if any(OWNERS.get(h.strip()) != module for h in headings):
        raise RouterError("ROUTER_REGION_NOT_OWNED")


def protected_intact(module, before, after):
    """True only if every region owned by someone else is exactly as before; a missing V7 block is never recreated."""
    return all(before.get(h) == after.get(h) for h, owner in OWNERS.items() if owner != module)
