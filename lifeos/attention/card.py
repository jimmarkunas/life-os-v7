"""Attention card (D128): the one line the Daily Report's Attention callout shows above Jim's interactive Attention view.

It READS the canonical Attention rows (current week, Active, not Done, never Physical Mail: the same rows the view shows) and writes only the TEXT of the callout it owns
through `report_region.replace_text`; the linked view and every non-text block are never touched. No rows -> "No active exceptions."; when the sync step just failed the line says
DEGRADED, so a failed read is never shown as an empty queue. Counts only in the log."""
from datetime import datetime
from zoneinfo import ZoneInfo
import os

from lifeos.attention import reconcile, stage
from lifeos.platform import report_region, router
from lifeos.platform.notion_client import Client, NotionError, rich_text

TZ = ZoneInfo("America/Chicago")
TITLE = router.ATTENTION_REGION
MODULE = router.OWNERS[TITLE]
PROTECTED = (("JIRA_CARD_BLOCK_ID", router.JIRA_REGION), ("CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION), ("BILLS_CARD_BLOCK_ID", router.BILLS_REGION),
             ("AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION))


class CardError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return CardError("ATTENTION_" + code)


def active(rows):
    return [r for r in rows if r["active"] and not r["done"] and r["category"] != "Physical Mail"]


def render(count, now, degraded=False):
    stamp = now.strftime("%-I:%M %p")
    if degraded:
        line = f"DEGRADED · Attention sync failed at {stamp} CT · showing the last accepted exceptions"
    elif count:
        line = f"Updated {stamp} CT · {count} active exception{'s' if count != 1 else ''}"
    else:
        line = f"Updated {stamp} CT · No active exceptions."
    return [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(line)}}]


def _writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def run(limit, live, environ=os.environ, reader=None, client=None, now=None):
    block_id = (environ.get("ATTENTION_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        return {"active": 0, "status": "not_configured", "blocks_written": 0}
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    source_id = (environ.get("NOTION_ATTENTION_DATA_SOURCE_ID") or stage.SOURCE_ID).strip().replace("collection://", "")
    reader = reader or Client({**environ, "NOTION_ATTENTION_DATA_SOURCE_ID": source_id}, token_name="NOTION_JIRA_TOKEN", source_name="NOTION_ATTENTION_DATA_SOURCE_ID")
    stage._schema(reader, source_id)
    rows = stage.read_week(reader, source_id, reconcile.week_ending(now.date()))
    count = len(active(rows))
    degraded = (environ.get("ATTENTION_SYNC_OUTCOME") or "").strip().lower() in ("failure", "cancelled")
    blocks = render(count, now, degraded)
    client = client or _writer(environ)
    protected_ids = {region: (environ.get(name) or "").strip() for name, region in PROTECTED}
    _, removed = report_region.replace_text(client, block_id, TITLE, MODULE, blocks, protected_ids, _fail, "attention", live, headless=True)
    counts = {"active": count, "status": "degraded" if degraded else "fresh", "blocks_written": 0}
    if live:
        counts["blocks_written"], counts["removed"] = len(blocks), removed
    return counts
