"""LIFE-37 Step 2, part 2: one manual run that carries ONE Granola meeting from Oracle into its round's Full Transcript page, and stops.

Order (Jim's approved plan, LIFE-37 comments 11218, 11231): ask Oracle to sync and list its meetings -> keep the pinned Central day (exactly one meeting must fall on it)
-> match every listed meeting to the rounds in Notion under the approved rule -> the pinned meeting must match exactly one round -> fetch its transcript and run the
contract check -> write the Full Transcript page and read it back -> only after a verified write (or an identical page already there) ask Oracle to purge its copy ->
refresh the round's Transcript lines. Never loops to a second meeting. Returns codes and counts only; no title, name, address, id or transcript text leaves this module."""
import os

from lifeos.platform.notion_client import NotionError
from lifeos.platform.names import split_title
from lifeos.platform.runtime import DeadlineExceeded, RunContext

from . import create, granola_match, granola_oracle, granola_write, notion
from .models import Ownership
from .transcript import ContractError, normalize_granz


class Stop(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def rounds(client, environ, context):
    scan = notion.parent_scan(client, environ["HIRING_PIPELINE_PAGE_ID"].strip(), context)
    if not scan.complete:
        raise Stop("GRAN_PIPELINE_INCOMPLETE")
    found = []
    for parent in scan.items:
        company, role = split_title(parent.title)
        if not company.strip():
            continue
        kids = notion.child_scan(client, parent.page_id, context)
        if not kids.complete:
            raise Stop("GRAN_PIPELINE_INCOMPLETE")
        found += [granola_match.Round(c.page_id, c.interview_date, company, role, c.interviewer or "") for c in kids.items if c.interview_date]
    return found


def meetings(reply):
    try:
        return [granola_match.Meeting(str(m["id"]), str(m["start"]), str(m.get("title") or ""), "", tuple(str(a) for a in (m.get("attendees") or ()))) for m in reply["meetings"]]
    except (KeyError, TypeError, ValueError):
        raise Stop("GRAN_REPLY_INVALID") from None


def run(live, only_day, environ=os.environ, send=granola_oracle.exchange, client=None, context=None):
    """-> {"code": the final fixed code, "counts": {...}}. `only_day` (YYYY-MM-DD, Central) is required: the first write is pinned to one day, and exactly one synced meeting must fall on it."""
    counts = {"sync_documents": 0, "sync_transcripts": 0, "meetings": 0, "on_day": 0, "rounds": 0, "segments": 0, "writes": 0, "readback_ok": 0, "purged": 0, "linked": 0}
    context = context or RunContext.start(600)
    host = (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()

    def ask(op, **fields):
        try:
            return granola_oracle.call(host, op, send=send, **fields)
        except granola_oracle.OracleError as error:
            raise Stop(error.code) from None

    try:
        if not only_day or granola_match.central_day(f"{only_day}T12:00:00-05:00") != only_day:
            raise Stop("GRAN_PIN_INVALID")
        if not environ.get("NOTION_INTERVIEW_TOKEN") or not environ.get("HIRING_PIPELINE_PAGE_ID"):
            raise Stop("GRAN_TARGET_CONFIG_MISSING")
        client = client or notion.make_client(environ)
        if notion.target_check(client, environ) != "target_ok":
            raise Stop("GRAN_TARGET_NOT_OK")
        synced = ask("sync")
        counts["sync_documents"], counts["sync_transcripts"] = int(synced.get("documents") or 0), int(synced.get("transcripts") or 0)
        listed = meetings(ask("list"))
        counts["meetings"] = len(listed)
        pinned = [m for m in listed if granola_match.central_day(m.start) == only_day]
        counts["on_day"] = len(pinned)
        if len(pinned) != 1:
            raise Stop("GRAN_PIN_NOT_ONE_MEETING")
        context.require_time()
        have = rounds(client, environ, context)
        counts["rounds"] = len(have)
        code, round_id = granola_match.match(listed, have)[pinned[0].id]
        if code != "MATCH":
            raise Stop(code)
        if notion.ownership(client, round_id, "round") != Ownership.MACHINE:
            raise Stop("GRAN_ROUND_NOT_MACHINE")
        shown = ask("show", id=pinned[0].id)
        try:
            envelope = normalize_granz(pinned[0].id, shown.get("json") or "")
        except ContractError as error:
            raise Stop(error.code) from None
        counts["segments"] = len(envelope.segments)
        result, wrote = granola_write.write(client, round_id, envelope, live)
        counts["writes"], counts["readback_ok"] = wrote["blocks_written"], wrote["readback_ok"]
        if live and result in ("TRANSCRIPT_WRITTEN", "TRANSCRIPT_NOOP"):
            counts["purged"] = int(ask("purge", id=pinned[0].id, content_sha256=envelope.source_content_sha256).get("purged") or 0)
            counts["linked"] = int(create.link_transcript(client, round_id, True, {}) in ("transcript_linked", "transcript_unchanged"))
        return {"code": result, "counts": counts}
    except Stop as stop:
        return {"code": stop.code, "counts": counts}
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return {"code": "GRAN_NOTION_FAILED", "counts": counts}


def stage(limit, live, environ=os.environ):
    """`python -m lifeos.run granola [--live]`: GRAN_PIN_DAY (YYYY-MM-DD, Central) names the one day to carry. Counts and the final fixed code only. `limit` is unused: one meeting, always."""
    out = run(live, (environ.get("GRAN_PIN_DAY") or "").strip(), environ=environ)
    return {"code": out["code"], "failed": 0 if out["code"] in ("TRANSCRIPT_WRITTEN", "TRANSCRIPT_NOOP", "TRANSCRIPT_PLANNED") else 1, **out["counts"]}
