"""Render the Physical Mail state into V7's one "Mail Alerts" callout on the Daily Report (MAIL-1.2). V7 owns this region exclusively (router.OWNERS).

The card only READS the stored state (the row `stage` wrote and read back) and writes the text of the one callout `MAIL_ALERTS_CARD_BLOCK_ID` names; it never touches another
region and never reacquires the source. Missing, stale, failed or unaccepted evidence renders DEGRADED and keeps the last accepted state visible: it is never shown as
"none" or zero. Write and read-back go through `report_region.replace_text`. Counts only; fixed codes."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform import db, report_region, router
from lifeos.platform.notion_client import Client, NotionError, rich_text
from . import state as state_mod
from .stage import KEY, STORE

TZ = ZoneInfo("America/Chicago")
TITLE = router.MAIL_ALERTS_REGION
MODULE = router.OWNERS[TITLE]
STALE_HOURS = 6                                    # the pipeline is hourly: no accepted evidence for six hours is stale
PROTECTED = (("JIRA_CARD_BLOCK_ID", router.JIRA_REGION), ("CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION), ("BILLS_CARD_BLOCK_ID", router.BILLS_REGION),
             ("AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION), ("ATTENTION_CARD_BLOCK_ID", router.ATTENTION_REGION))


class CardError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return CardError("MAILALERTS_" + code)


def _paragraph(content, link=None):
    text = rich_text(content)
    if link and text:
        text[0]["text"]["link"] = {"url": link}
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": text}}


def _local(now):
    return (now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ))


def render(stored, now, sync_failed=False):
    """-> (blocks, counts). Pure."""
    now = _local(now)
    if stored is None:
        return ([_paragraph(f"DEGRADED · Physical Mail starting state unknown at {now.strftime('%-I:%M %p')} CT · nothing accepted yet")],
                {"status": "degraded", "reason": "no_state"})
    accepted = stored.get("accepted_at")
    stale = not accepted or _local(datetime.fromisoformat(accepted)) < now - timedelta(hours=STALE_HOURS)
    facts = state_mod.summary(stored, now)
    reason = "sync_failed" if sync_failed else "stale" if stale else facts["degraded_reason"].lower() if facts["degraded_reason"] else None
    when = _local(datetime.fromisoformat(accepted)).strftime("%b %-d, %-I:%M %p") if accepted else "never"
    if reason:
        head = f"DEGRADED · Physical Mail {'sync failed' if sync_failed else 'evidence stale' if stale else 'needs a look'} at {now.strftime('%-I:%M %p')} CT · showing last accepted state ({when} CT)"
    else:
        head = f"Updated {now.strftime('%-I:%M %p')} CT"
    waiting = facts["unidentified"] + facts["opened"] + facts["waiting_tracking"]
    blocks = [_paragraph(f"{head} · {waiting} waiting · {facts['review']} need review")]
    if facts["unidentified"]:
        blocks.append(_paragraph(f"{facts['unidentified']} new item(s) not yet identified by a mail number"))
    if facts["opened"]:
        blocks.append(_paragraph(f"{facts['opened']} opened or scanned, no final action yet"))
    if facts["waiting_tracking"]:
        blocks.append(_paragraph(f"{facts['waiting_tracking']} forwarded, waiting for tracking" + (f" · {facts['aged']} waiting over {state_mod.AGED_DAYS} days, check the portal" if facts["aged"] else "")))
    if facts["review"]:
        blocks.append(_paragraph(f"{facts['review']} item(s) with unclear or conflicting notices need a look"))
    if not waiting and not facts["review"] and not reason:
        blocks.append(_paragraph("No physical mail waiting"))
    portal = stored.get("portal")
    if isinstance(portal, str) and portal.startswith("https://") and (waiting or facts["review"]):
        blocks.append(_paragraph("Open the mailbox portal", portal))
    return blocks, {"status": "degraded" if reason else "fresh", "reason": reason or "", "waiting": waiting, "review": facts["review"], "blocks_written": 0}


def _writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def run(limit, live, environ=os.environ, connection=None, client=None, now=None):
    block_id = (environ.get("MAIL_ALERTS_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        return {"status": "not_configured", "blocks_written": 0}
    now = now or datetime.now(TZ)
    if connection is None:
        with db.connect() as opened:
            STORE.ensure(opened)
            stored = STORE.load(opened, KEY)
    else:
        stored = STORE.load(connection, KEY)
    failed = (environ.get("MAIL_SYNC_OUTCOME") or "").strip().lower() in ("failure", "cancelled")
    blocks, counts = render(stored, now, failed)
    client = client or _writer(environ)
    protected_ids = {region: (environ.get(name) or "").strip() for name, region in PROTECTED}
    _, removed = report_region.replace_text(client, block_id, TITLE, MODULE, blocks, protected_ids, _fail, "mailalerts", live)
    if live:
        counts["blocks_written"], counts["removed"] = len(blocks), removed
    return counts
