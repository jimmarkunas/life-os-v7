"""The hourly router contract for the ChatGPT-native `LIFE OS Daily Runs` task: the three outcome words and strict ownership of Daily Report regions.

No scheduler, queue, datastore or framework: the ChatGPT task runs hourly outside GitHub and applies these rules; V7's own hourly workflow is unrelated and unchanged. Fixed codes only: no content, ids or messages."""
PASS, NO_ACTION, DEGRADED = "PASS", "NO_ACTION", "DEGRADED"

# A Daily Report region is a callout whose first child heading names its owner. Nothing else is writable by any module.
JIRA_REGION = "JIRA Execution"                          # V7 owns it exclusively (lifeos/jira/card.py): ChatGPT modules never touch it
CALENDAR_REGION = "Calendar"                            # V7 owns it exclusively (lifeos/agenda/card.py)
BILLS_REGION = "Bills: This Week"                       # V7 owns it exclusively (lifeos/bills/card.py)
ATTENTION_REGION = "Attention"                           # Jim's interactive Attention view; V7 no longer writes it (LIFE-645), but other cards still anchor on and protect this callout
AMAZON_REGION = "Amazon Orders"                         # V7 owns it exclusively (lifeos/amazon/card.py)
MAIL_ALERTS_REGION = "Mail Alerts"                      # V7 owns it exclusively (lifeos/physmail/card.py; Production Contract v2.15.12): LIFE OS Daily Runs neither reads the source nor writes it
HIRING_REGION = "📈 Hiring Pipeline"                    # V7 owns it exclusively (lifeos/hiring/card.py). The ONE floating region: a heading_3, a status paragraph and a four-column table, never a callout
DCC_REGION = "ChatGPT · Daily Command Center"      # the one region the Daily Command Center module may write; V7 stage `dcc-card` has been that module since D175 (the heading text is unchanged, so renaming it in Notion needs this constant changed too)
COMMS_REGION = "Communications Attention"                # V7 owns only the Messages row and the heading time (lifeos/imessage/comms_card.py, LIFE-34); the rest of the callout is Jim's
ORACLE_REGION = "Oracle Cloud"                           # V7 owns it exclusively (lifeos/oraclecloud/card.py): the heading and the one paragraph under it
RECRUITER_OUTREACH_REGION = "🤝 Recruiter Outreach"      # LIFE-20: V7 owns it exclusively (lifeos/recruiters/outreach_card.py). A second floating region: a heading_3 and one four-column table, right under the Hiring Pipeline table
REPO_REGION = "🧹 Repository hygiene"                   # D244 (LIFE-81): V7 owns it exclusively (lifeos/platform/branches.py). Not on the Daily Report: one callout on the Weekly LIFE OS Review page
OWNERS = {JIRA_REGION: "v7-jira", CALENDAR_REGION: "v7-calendar", BILLS_REGION: "v7-bills", AMAZON_REGION: "v7-amazon", MAIL_ALERTS_REGION: "v7-mail-alerts", HIRING_REGION: "v7-hiring", ATTENTION_REGION: "v7-attention", DCC_REGION: "daily-command-center", COMMS_REGION: "v7-comms", ORACLE_REGION: "v7-oracle", REPO_REGION: "v7-repo-hygiene", RECRUITER_OUTREACH_REGION: "v7-recruiter-outreach"}

FLOATING = (HIRING_REGION, RECRUITER_OUTREACH_REGION)   # owned regions that are NOT callouts; every other owned region is one (single ownership is unchanged: one OWNERS entry each)


class RouterError(RuntimeError):
    """The message is always a fixed code."""


def check_write(module, headings):
    """Every region a write would touch must be owned by `module`; an unknown region is not owned by anyone, so it fails closed too."""
    if any(OWNERS.get(h.strip()) != module for h in headings):
        raise RouterError("ROUTER_REGION_NOT_OWNED")


def protected_intact(module, before, after):
    """before/after: {heading: any stable digest of that region}. True only if every region owned by someone else is exactly as it was:
    present stays present and unchanged, absent stays absent (a missing V7 block is never recreated)."""
    return all(before.get(h) == after.get(h) for h, owner in OWNERS.items() if owner != module)
