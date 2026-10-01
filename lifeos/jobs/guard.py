"""Human-state guard: nothing destructive happens to a Job Ledger page while a human pursuit may exist.

Every destructive Jobs operation on a published page (audit today; any future one) asks here first. A page is protected when the human
has applied (Applied, or an Applied On date), made a Saturn Decision, or the hiring-pipeline handoff finds an ACTIVE opportunity for it.
Anything this cannot read is UNKNOWN and protected too: fail closed, never guess. Reasons are fixed codes (counts-only logs).
"""
from lifeos.jobs import hiring_pipeline
from lifeos.platform.notion_client import NotionError

APPLIED, APPLIED_ON, SATURN, HIRING, UNREADABLE = "applied", "applied_on", "saturn_decision", "hiring_pipeline", "unreadable"


def protection(client, page_id, company, title, opportunities=()):
    """None when nothing protects the page, else the fixed reason."""
    try:
        props = (client.call("GET", f"/pages/{page_id}").get("properties")) or {}
    except NotionError:
        return UNREADABLE
    if (props.get("Applied") or {}).get("checkbox"):
        return APPLIED
    if ((props.get("Applied On") or {}).get("date") or {}).get("start"):
        return APPLIED_ON
    if (props.get("Saturn Decision") or {}).get("select"):
        return SATURN
    if hiring_pipeline.handoff_for(company, title, opportunities).protected:
        return HIRING
    return None
