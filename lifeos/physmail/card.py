"""Render the Physical Mail state into V7's one "Mail Alerts" callout on the Daily Report (MAIL-1.2). V7 owns this region exclusively (router.OWNERS).

The card only READS the stored state (the row `stage` wrote and read back) and writes the text of the one callout `MAIL_ALERTS_CARD_BLOCK_ID` names; it never touches another
region and never reacquires the source. Missing, stale, failed or unaccepted evidence renders DEGRADED and keeps the last accepted state visible: it is never shown as
"none" or zero. Write and read-back go through `report_region.replace_text`. Counts only; fixed codes."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform import db, report_region, router
from lifeos.platform.notion_client import Client, NotionError, rich_text
from . import chains, manual, state as state_mod
from .stage import KEY, STORE

TZ = ZoneInfo("America/Chicago")
TITLE = router.MAIL_ALERTS_REGION
MODULE = router.OWNERS[TITLE]
STALE_HOURS = 6                                    # the pipeline is hourly: no accepted evidence for six hours is stale
PROTECTED = (("JIRA_CARD_BLOCK_ID", router.JIRA_REGION), ("CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION), ("BILLS_CARD_BLOCK_ID", router.BILLS_REGION),
             ("AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION), ("ATTENTION_CARD_BLOCK_ID", router.ATTENTION_REGION), ("HIRING_CARD_BLOCK_ID", router.HIRING_REGION))


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


MAX_ITEMS = 30                                      # a pile longer than this ends with a "+N more" line: the heading's count is always the whole pile
WORDS = {"SCAN": "Scanned", "FORWARD": "Forwarded"}


def _item(parts):
    """One native bulleted line: [(text, bold, link)]."""
    rich = []
    for text, bold, link in parts:
        for piece in rich_text(text):
            if bold:
                piece["annotations"] = {"bold": True}
            if link:
                piece["text"]["link"] = {"url": link}
            rich.append(piece)
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich}}


def _day(iso):
    return _local(datetime.fromisoformat(iso)).strftime("%b %-d")


def _https(value):
    return value if isinstance(value, str) and value.startswith("https://") else None


def _label(text):
    """A bold group label (a paragraph, so the bullets under it stay a plain list)."""
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{**piece, "annotations": {"bold": True}} for piece in rich_text(text)]}}


def _muted(text, link=None):
    rich = rich_text(text)
    for piece in rich:
        piece["annotations"] = {"color": "gray"}
        if link:
            piece["text"]["link"] = {"url": link}
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich}}


def _line(piece, portal, now):
    """-> one active piece as a short bullet: bold mail number, then only what differs from its group (the group label carries the shared next action). Forward completed is never shown as delivered."""
    number, status, when = piece["number"], piece["status"], _day(piece["at"])
    head = (f"#{number}", True, None)
    if status == chains.REVIEW:
        return _item([head, (f"  ·  last notice {when}", False, None)])
    if status == chains.OPEN:
        return _item([head, (f"  ·  scanned {when}", False, None)])
    tracking = piece.get("tracking") if isinstance(piece.get("tracking"), dict) else None
    if tracking and isinstance(tracking.get("number"), str) and tracking["number"].strip():
        carrier = " ".join(t.strip() for t in (tracking.get("bundle"), tracking.get("service") or tracking.get("carrier")) if isinstance(t, str) and t.strip()) or "Carrier not stated"
        state_text = tracking.get("state") if isinstance(tracking.get("state"), str) and tracking["state"].strip() else "no delivery status yet"
        return _item([head, (f"  ·  forwarded {when}  ·  {carrier} ", False, None), (tracking["number"].strip(), False, _https(tracking.get("url"))), (f"  ·  {state_text}", False, None)])
    aged = _local(datetime.fromisoformat(piece["at"])) < now - timedelta(days=state_mod.AGED_DAYS)
    return _item([head, (f"  ·  forwarded {when}  ·  ", False, None), ("⚠ Tracking unavailable — obtain from provider", True, portal),
                  (f"  ·  over {state_mod.AGED_DAYS} days, check the portal" if aged else "", False, None)])


GROUPS = ((chains.REVIEW, "⚠️ Needs a look — notices conflict"),
          (chains.WAITING, "🚚 Forwarded — not delivered until tracking says so"),
          (chains.OPEN, "📥 Scanned — decide: forward, shred or recycle"))


def render(stored, now, sync_failed=False, environ=None):
    """-> (blocks, counts). Pure. The first line keeps its shape (the Command Center and Attention read it); then one native line per active piece."""
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
    environ = environ if environ is not None else {}
    portal = manual.portal(environ) or _https(stored.get("portal"))
    entered = manual.tracking(environ)
    items = state_mod.pieces(stored)
    for piece in items:
        if not piece["tracking"] and piece["status"] == chains.WAITING:
            piece["tracking"] = entered.get(piece["number"])                                    # entered by hand: shown as written, never inferred
    delivered = [p for p in items if isinstance(p["tracking"], dict) and p["tracking"].get("delivered")]
    items = [p for p in items if p not in delivered]
    waiting = facts["unidentified"] + facts["opened"] + facts["waiting_tracking"] - len(delivered)
    blocks = [_paragraph(f"{head} · {waiting} waiting · {facts['review']} need review")]
    cutoff = now - timedelta(days=state_mod.KEEP_DAYS)
    gone = state_mod.disposed(stored, cutoff)
    arrived = sum(1 for p in delivered if _local(datetime.fromisoformat(p["tracking"]["delivered"] + "T00:00:00")) >= cutoff.replace(hour=0, minute=0, second=0, microsecond=0))
    if facts["unidentified"]:
        blocks.append(_paragraph(f"{facts['unidentified']} new item(s) not yet identified by a mail number"))
    shown = items[:MAX_ITEMS]
    for status, label in GROUPS:
        group = [piece for piece in shown if piece["status"] == status]
        if group:
            blocks.append(_label(f"{label} ({sum(1 for piece in items if piece['status'] == status)})"))
            blocks += [_line(piece, portal, now) for piece in group]
    if len(items) > MAX_ITEMS:
        blocks.append(_paragraph(f"+{len(items) - MAX_ITEMS} more active pieces: open the mailbox portal"))
    if facts["review"] > sum(1 for piece in items if piece["status"] == chains.REVIEW):
        blocks.append(_paragraph(f"{facts['review'] - sum(1 for piece in items if piece['status'] == chains.REVIEW)} notice(s) could not be read and need a look"))
    if not waiting and not facts["review"] and not reason:
        blocks.append(_paragraph("No physical mail waiting"))
    if portal and (waiting or facts["review"]):
        blocks.append(_paragraph("Open the mailbox portal", portal))
    recent = [f"{gone['recycled']} recycled" if gone["recycled"] else "", f"{gone['shredded']} shredded" if gone["shredded"] else "", f"{arrived} delivered" if arrived else ""]
    if any(recent):
        blocks.append(_muted(f"Last {state_mod.KEEP_DAYS} days: " + ", ".join(r for r in recent if r)))
    return blocks, {"status": "degraded" if reason else "fresh", "reason": reason or "", "waiting": waiting, "review": facts["review"], "listed": len(items[:MAX_ITEMS]), "blocks_written": 0}


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
    blocks, counts = render(stored, now, failed, environ)
    client = client or _writer(environ)
    protected_ids = {region: (environ.get(name) or "").strip() for name, region in PROTECTED}
    _, removed = report_region.replace_text(client, block_id, TITLE, MODULE, blocks, protected_ids, _fail, "mailalerts", live)
    if live:
        counts["blocks_written"], counts["removed"] = len(blocks), removed
    return counts
