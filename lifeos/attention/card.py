"""Attention card (D128): status, suggested decisions and hiring summary above Jim's interactive view.

It READS the canonical Attention rows (current week, Active, not Done, never Physical Mail: the same rows the view shows) and writes only the TEXT of the callout it owns
through `report_region.replace_text`; the linked view and every non-text block are never touched. No rows -> "No active exceptions."; when the sync step just failed the line says
DEGRADED, so a failed read is never shown as an empty queue. Decision counts use only fresh sources; counts only in the log."""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os

from lifeos.attention import reconcile, stage
from lifeos.platform import report_region, router
from lifeos.platform.notion_client import Client, NotionError, rich_text

TZ = ZoneInfo("America/Chicago")
TITLE = router.ATTENTION_REGION
MODULE = router.OWNERS[TITLE]
PROTECTED = (("JIRA_CARD_BLOCK_ID", router.JIRA_REGION), ("CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION), ("BILLS_CARD_BLOCK_ID", router.BILLS_REGION),
             ("AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION), ("MAIL_ALERTS_CARD_BLOCK_ID", router.MAIL_ALERTS_REGION), ("HIRING_CARD_BLOCK_ID", router.HIRING_REGION))
FRESH_MINUTES = 90
STAMP = re.compile(r"Updated (\d{1,2}):(\d{2}) ([AP]M) CT")
TIME = re.compile(r"(\d{1,2}):(\d{2}) ([AP]M) CT")
AREAS = (("Calendar", "CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION, "head"), ("Jira", "JIRA_CARD_BLOCK_ID", router.JIRA_REGION, "head"),
         ("Bills", "BILLS_CARD_BLOCK_ID", router.BILLS_REGION, "line"), ("Mail Alerts", "MAIL_ALERTS_CARD_BLOCK_ID", router.MAIL_ALERTS_REGION, "line"),
         ("Amazon", "AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION, "line"), ("Attention", "ATTENTION_CARD_BLOCK_ID", router.ATTENTION_REGION, "own"),
         ("Hiring Pipeline", "HIRING_CARD_BLOCK_ID", router.HIRING_REGION, "floating"))


class CardError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return CardError("ATTENTION_" + code)


def active(rows):
    return [r for r in rows if r["active"] and not r["done"] and r["category"] != "Physical Mail"]


def _status(count, now, degraded=False):
    stamp = now.strftime("%-I:%M %p")
    if degraded:
        return f"DEGRADED · Attention sync failed at {stamp} CT · showing the last accepted exceptions"
    return f"Updated {stamp} CT · {count} active exception{'s' if count != 1 else ''}" if count else f"Updated {stamp} CT · No active exceptions."


def render(count, now, decisions=(), hiring="Hiring Pipeline unset", degraded=False):
    """The callout holds ONE status line and then the table: no generated advice (Jim, Oct 26: the text above the table is not attention). `decisions` and `hiring` are accepted and ignored."""
    return [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(_status(count, now, degraded))}}]


def _link(block):
    return next((url for part in (block.get(block.get("type")) or {}).get("rich_text") or [] if (url := ((part.get("text") or {}).get("link") or {}).get("url"))), None)


def _text_of(client, block_id, kind, region):
    if kind == "own":
        meta = client.call("GET", f"/blocks/{report_region.clean_id(block_id)}")
        if not isinstance(meta, dict) or meta.get("type") != "callout":
            raise _fail("SOURCE_UNREADABLE")
        return report_region.plain({"type": "callout", "callout": meta.get("callout") or {}}).strip(), []
    if kind == "floating":
        found = report_region.floating_region(client, report_region.clean_id(block_id), region, _fail, with_status=False)
        heading, active = report_region.plain(found["heading"]).strip(), len(found["rows"]) - 1
        stamp = heading[len(region) + 2:-1].strip() if heading.startswith(region + " (") and heading.endswith(")") else ""
        return (f"{stamp} · {active} active" if stamp and active else stamp), []
    kids = report_region.children(client, report_region.clean_id(block_id), _fail)
    texts = [report_region.plain(b).strip() for b in kids if b.get("type") not in ("table", "child_database", "child_page", "link_to_page")]
    texts = [text for text in texts if text]
    if kind == "head":
        return (texts[0] if texts else ""), texts[1:]
    body = [block for block in kids if block.get("type") not in report_region.HEADINGS]
    lines = [report_region.plain(block).strip() for block in body]
    lines = [text for text in lines if text]
    return (lines[0] if lines else ""), lines[1:]


def _areas(client, environ, now):
    areas = {}
    for label, name, region, kind in AREAS:
        block_id = (environ.get(name) or "").strip()
        if not block_id:
            areas[label] = {"unset": True}
            continue
        try:
            status, lines = _text_of(client, block_id, kind, region)
            links = {}
            if label in ("Jira", "Bills"):
                for block in report_region.children(client, report_region.clean_id(block_id), _fail):
                    url = _link(block)
                    if url:
                        links[report_region.plain(block).strip()] = url
            areas[label] = {"status": status, "lines": lines, "links": links}
        except Exception:                                  # one card that cannot be read, however it fails, must never stop the status line
            areas[label] = {"unreadable": True}
    return areas


def _classify(area, now):
    if area.get("unset"):
        return "unset", None
    if area.get("unreadable"):
        return "unreadable", None
    text = area.get("status") or ""
    shown = TIME.search(text)
    stamp = f"{shown.group(1)}:{shown.group(2)} {shown.group(3)}" if shown else None
    if text.startswith("DEGRADED"):
        return "degraded", stamp
    if text.startswith("STALE"):
        return "stale", stamp
    found = STAMP.search(text)
    if not found:
        return "unknown", None
    hour = int(found.group(1)) % 12 + (12 if found.group(3) == "PM" else 0)
    at = now.replace(hour=hour, minute=int(found.group(2)), second=0, microsecond=0)
    if at > now:
        at -= timedelta(days=1)
    return ("fresh" if now - at <= timedelta(minutes=FRESH_MINUTES) else "stale"), stamp


def _decisions(areas, now):
    states = {label: _classify(area, now) for label, area in areas.items()}
    bad = [(label, state) for label, (state, _) in states.items() if state != "fresh"]

    def count(label, pattern, text=None):
        source = text if text is not None else (areas.get(label) or {}).get("status") or ""
        found = re.search(pattern, source)
        return int(found.group(1)) if found and states.get(label, ("unset",))[0] == "fresh" else None

    jira = areas.get("Jira") or {}
    lines = list(jira.get("lines", [])) if states.get("Jira", ("unset",))[0] == "fresh" else []
    sprint = next((text for text in lines if "Overdue/Blocked" in text), None)
    overdue = count("Bills", r"(\d+) overdue")
    review = count("Mail Alerts", r"(\d+) need review")
    combined = count("Jira", r"(\d+) Overdue/Blocked", sprint)
    status = (areas.get("Attention") or {}).get("status") or ""
    exceptions = 0 if "No active exceptions" in status and states.get("Attention", ("unset",))[0] == "fresh" else count("Attention", r"(\d+) active exception")
    items = [("Check " + ", ".join(label for label, _ in bad), "not current, so no decision is drawn from them", None)] if bad else []
    bill_url = ((areas.get("Bills") or {}).get("links") or {}).get((areas.get("Bills") or {}).get("status"))
    items += [("Triage overdue bills", f"{overdue} on the Bills card", bill_url)] if overdue else []
    items += [("Clear overdue or blocked Jira items", f"{combined} on the Jira card", None)] if combined else []
    items += [("Review Attention exceptions", f"{exceptions} active", None)] if exceptions else []
    items += [("Review mail items", f"{review} need review", None)] if review else []
    hiring_count = count("Hiring Pipeline", r"(\d+) active")
    hiring = f"{hiring_count} active hiring opportunities" if hiring_count is not None else "Hiring Pipeline " + states.get("Hiring Pipeline", ("unset",))[0]
    return items[:3] or [("Nothing flagged by the cards", "continue the day's planned work", None)], hiring


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
    client = client or _writer(environ)
    areas = _areas(client, environ, now)
    areas["Attention"] = {"status": _status(count, now, degraded), "lines": [], "links": {}}
    decisions, hiring = _decisions(areas, now)
    blocks = render(count, now, decisions, hiring, degraded)
    protected_ids = {region: (environ.get(name) or "").strip() for name, region in PROTECTED}
    _, removed = report_region.replace_text(client, block_id, TITLE, MODULE, blocks, protected_ids, _fail, "attention", live, headless=True)
    counts = {"active": count, "status": "degraded" if degraded else "fresh", "blocks_written": 0}
    if live:
        counts["blocks_written"], counts["removed"] = len(blocks), removed
    return counts
