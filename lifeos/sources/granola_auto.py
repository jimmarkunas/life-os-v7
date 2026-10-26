"""LIFE-122 (Jim, Oct 26: "Approved, go"): the Granola bridge, automatic. Every synced meeting that has not been carried before is matched to its Interview OS round; one that matches
exactly one round is written to the round's Full Transcript page (notes and summary above the word-for-word transcript when Oracle supplies them), read back, catalogued in Hostinger
(`v7_meeting_catalog`, the dedupe key), and ONLY THEN purged from Oracle. A meeting that matches no round, or several, is never touched: it stays on Oracle and is counted.

Jim's safety rule (LIFE-37 comment 11261): purge only after (a) exactly one round matched, (b) the Notion page was written and read back, (c) the catalog row was saved and read back.
Oracle announces what it can do in `list`'s `caps`: `purge_all` (delete everything held for a meeting) and `notes` (`show` also returns `notes` and `summary`). Without `purge_all` nothing is
purged, so an Oracle that has not shipped the whole-meeting purge is never asked to do a partial one by mistake. Dry run unless live. Counts and fixed codes only; no title, name, address,
id or transcript text leaves this module (the meeting id is stored as a 16-character hash)."""
import hashlib
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.interview import create, granola_match, granola_oracle, granola_write, notion
from lifeos.interview.granola_bridge import Stop, meetings
from lifeos.interview.models import Ownership
from lifeos.interview.transcript import ContractError, normalize_granz
from lifeos.jobs import store
from lifeos.platform.names import split_title
from lifeos.platform.notion_client import NotionError
from lifeos.platform.runtime import DeadlineExceeded, RunContext
from lifeos.sources import interview_evidence

TZ = ZoneInfo("America/Chicago")
PER_RUN = 3                        # meetings carried per run: a backlog drains over a few runs and one run stays well inside its time
DONE = ("carried", "carried_purged")
NOTE_LIMIT = 60_000                # characters of notes or summary kept


def key_of(meeting_id):
    return hashlib.sha256(str(meeting_id).encode()).hexdigest()[:16]


def catalog_done(cursor, keys):
    """-> the set of meeting keys already carried (outcome in DONE)."""
    if not keys:
        return set()
    cursor.execute("SELECT meeting_key FROM v7_meeting_catalog WHERE outcome IN %s AND meeting_key IN %s", (DONE, tuple(keys)))
    return {row[0] for row in cursor.fetchall()}


def catalog_save(cursor, key, day, round_key, job_key, outcome, segments, header, purged, now):
    cursor.execute("INSERT INTO v7_meeting_catalog (meeting_key, day, round_key, job_key, outcome, segments, header_written, purged, carried_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                   "ON DUPLICATE KEY UPDATE round_key=VALUES(round_key), job_key=VALUES(job_key), outcome=VALUES(outcome), segments=VALUES(segments), header_written=VALUES(header_written), "
                   "purged=VALUES(purged), carried_at=VALUES(carried_at)", (key, day, round_key, job_key, outcome, int(segments), int(bool(header)), int(bool(purged)), now))


def catalog_has(cursor, key, outcome):
    cursor.execute("SELECT outcome FROM v7_meeting_catalog WHERE meeting_key = %s", (key,))
    row = cursor.fetchone()
    return bool(row) and row[0] == outcome


def rounds_with_parents(client, environ, context):
    """-> ([granola_match.Round], {round page id: its opportunity page id}); GRAN_PIPELINE_INCOMPLETE as a Stop when a scan is partial."""
    scan = notion.parent_scan(client, environ["HIRING_PIPELINE_PAGE_ID"].strip(), context)
    if not scan.complete:
        raise Stop("GRAN_PIPELINE_INCOMPLETE")
    found, parent_of = [], {}
    for parent in scan.items:
        company, role = split_title(parent.title)
        if not company.strip():
            continue
        kids = notion.child_scan(client, parent.page_id, context)
        if not kids.complete:
            raise Stop("GRAN_PIPELINE_INCOMPLETE")
        for c in kids.items:
            if c.interview_date:
                found.append(granola_match.Round(c.page_id, c.interview_date, company, role, c.interviewer or ""))
                parent_of[c.page_id] = parent.page_id
    return found, parent_of


def header_of(shown):
    """((label, text), ...) from Oracle's `show` reply when it carries notes or a summary, bounded; else ()."""
    out = []
    for label, field in (("Summary", "summary"), ("Notes", "notes")):
        text = shown.get(field)
        if isinstance(text, str) and text.strip():
            out.append((label, text[:NOTE_LIMIT]))
    return tuple(out)


def run(limit, live, environ=os.environ, send=granola_oracle.exchange, client=None, context=None, connect=store.connect, now=None):
    counts = {"meetings": 0, "already": 0, "unmatched": 0, "carried": 0, "purged": 0, "left_on_oracle": 0, "header": 0, "segments": 0, "failed": 0, "purge_capable": 0, "why": {}}
    context = context or RunContext.start(840)
    now = (now or datetime.now(TZ)).astimezone(TZ).replace(tzinfo=None)
    host = (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()

    def why(code):
        counts["why"][code] = counts["why"].get(code, 0) + 1

    def ask(op, **fields):
        try:
            return granola_oracle.call(host, op, send=send, **fields)
        except granola_oracle.OracleError as error:
            raise Stop(error.code) from None

    try:
        if not environ.get("NOTION_INTERVIEW_TOKEN") or not environ.get("HIRING_PIPELINE_PAGE_ID"):
            raise Stop("GRAN_TARGET_CONFIG_MISSING")
        client = client or notion.make_client(environ)
        if notion.target_check(client, environ) != "target_ok":
            raise Stop("GRAN_TARGET_NOT_OK")
        synced = ask("sync")
        listed_reply = ask("list")
        caps = {str(c) for c in (listed_reply.get("caps") or ())}
        counts["purge_capable"] = int("purge_all" in caps)
        listed = meetings(listed_reply)
        counts["meetings"] = len(listed)
        counts["sync_documents"], counts["sync_transcripts"] = int(synced.get("documents") or 0), int(synced.get("transcripts") or 0)
        keys = {m.id: key_of(m.id) for m in listed}
        with connect() as connection, connection.cursor() as cursor:
            if live:
                store.ensure_schema(connection)
            try:
                done = catalog_done(cursor, list(keys.values()))
            except Exception:                                   # noqa: BLE001 - a missing table on a dry run before the first live run reads as nothing carried yet
                if live:
                    raise
                done = set()
        todo = [m for m in listed if keys[m.id] not in done]
        counts["already"] = len(listed) - len(todo)
        if not todo:
            return {"code": "GRAN_AUTO_NOTHING_NEW", "counts": counts}
        context.require_time()
        have, parent_of = rounds_with_parents(client, environ, context)
        matched = granola_match.match(listed, have)                 # every meeting is matched against every round, so a round two meetings claim is ambiguous for both
        order = sorted(todo, key=lambda m: m.start, reverse=True)
        carried_now = 0
        for meeting in order:
            code, round_id = matched[meeting.id]
            if code != "MATCH":
                counts["unmatched"] += 1
                counts["left_on_oracle"] += 1
                why(code)
                continue
            if carried_now >= max(1, limit or PER_RUN):
                counts["left_on_oracle"] += 1
                why("GRAN_AUTO_LATER_RUN")
                continue
            context.require_time()
            if notion.ownership(client, round_id, "round") != Ownership.MACHINE:
                counts["left_on_oracle"] += 1
                why("GRAN_ROUND_NOT_MACHINE")
                continue
            shown = ask("show", id=meeting.id)
            try:
                envelope = normalize_granz(meeting.id, shown.get("json") or "")
            except ContractError as error:
                counts["failed"] += 1
                counts["left_on_oracle"] += 1
                why(error.code)
                continue
            header = header_of(shown) if "notes" in caps else ()
            result, wrote = granola_write.write(client, round_id, envelope, live, header)
            if not live or result not in ("TRANSCRIPT_WRITTEN", "TRANSCRIPT_NOOP"):
                counts["left_on_oracle"] += 1
                why(result)
                counts["failed"] += int(result not in ("TRANSCRIPT_PLANNED",))
                continue
            job_key = None
            try:
                job_key = (interview_evidence.facts(client, parent_of[round_id], context) or (None,))[0]
            except (NotionError, KeyError):
                job_key = None
            day = granola_match.central_day(meeting.start)
            round_key = hashlib.sha256(round_id.encode()).hexdigest()[:16]
            with connect() as connection, connection.cursor() as cursor:
                catalog_save(cursor, keys[meeting.id], day, round_key, job_key, "carried", len(envelope.segments), header if result == "TRANSCRIPT_WRITTEN" else (), False, now)
                saved = catalog_has(cursor, keys[meeting.id], "carried")
            if not saved:
                counts["failed"] += 1
                counts["left_on_oracle"] += 1
                why("GRAN_CATALOG_FAILED")
                continue
            counts["carried"] += 1
            carried_now += 1
            counts["segments"] += len(envelope.segments)
            counts["header"] += int(bool(header) and result == "TRANSCRIPT_WRITTEN")
            create.link_transcript(client, round_id, True, {})
            if "purge_all" in caps:
                reply = ask("purge", id=meeting.id, content_sha256=envelope.source_content_sha256, scope="all")
                if reply.get("scope") == "all" and int(reply.get("purged") or 0) >= 1:
                    with connect() as connection, connection.cursor() as cursor:
                        catalog_save(cursor, keys[meeting.id], day, round_key, job_key, "carried_purged", len(envelope.segments), header if result == "TRANSCRIPT_WRITTEN" else (), True, now)
                    counts["purged"] += 1
                else:
                    counts["left_on_oracle"] += 1
                    why("GRAN_PURGE_SCOPE_NOT_CONFIRMED")
            else:
                counts["left_on_oracle"] += 1
                why("GRAN_PURGE_ALL_UNSUPPORTED")
        return {"code": "GRAN_AUTO_DONE", "counts": counts}
    except Stop as stop:
        counts["failed"] += 1
        return {"code": stop.code, "counts": counts}
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        counts["failed"] += 1
        return {"code": "GRAN_NOTION_FAILED", "counts": counts}
    except Exception:                                           # noqa: BLE001 - a store failure: a fixed code, never the exception text (its message can carry hosts)
        counts["failed"] += 1
        return {"code": "GRAN_CATALOG_FAILED", "counts": counts}


def stage(limit, live, environ=os.environ):
    """`python -m lifeos.run granola-auto [--live]`: every synced meeting that has not been carried. Counts and the final fixed code only."""
    out = run(limit, live, environ=environ)
    return {"code": out["code"], **out["counts"]}
