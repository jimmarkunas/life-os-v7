"""Retention (weekly), per the Jobs canon plus Jim's rules (docs/LANES.md).

Hostinger: job descriptions go at 90 days; job rows that were never published or are already PURGED go at 365; suppression tombstones expire at 90 days.
Notion, OUR pages only (matched by stored page id; pre-existing ledger rows are never touched):
  an UNAPPLIED job with no Saturn Decision whose age is 30+ days (employer Posting Date, else First Surfaced; never Created At)
  and for which progression.resolve says NOT_PROTECTED  ->  trashed (recoverable 30 days), marked PURGED here, and a 90-day tombstone is written.
Applied jobs are never destructively retired by Jobs until the Interview progression handoff (INT-7.1A) exists: they resolve UNKNOWN and are kept.
No legacy Lifecycle / Liveness value is read for a decision or written. One filtered Notion query per page of 100 (the approved weekly read). Counts only.
"""
from datetime import date, datetime, timedelta, timezone
import os

from lifeos.jobs import hiring_pipeline, lanes, ledger, progression, store, tombstone
from lifeos.platform import limits, notion_client

RETIRE_DAYS = 30
DESC_DAYS = 90
JOB_ROW_DAYS = 365
MAX_PAGES = 20
WRITES_PER_RUN = 150


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def query_filter(cutoff, lane):
    """Unapplied, no Saturn Decision, our lane, and old by the employer Posting Date (else First Surfaced). The legacy Lifecycle field is not read."""
    day = cutoff.date().isoformat()
    return {"and": [
        {"property": "Applied", "checkbox": {"equals": False}},
        {"property": "Applied On", "date": {"is_empty": True}},            # an Applied On date is human state even if the checkbox is clear
        {"property": "Saturn Decision", "select": {"is_empty": True}},
        {"property": "Visible Lane", "select": {"equals": lane}},
        {"or": [{"property": "Posting Date", "date": {"before": day}},
                {"and": [{"property": "Posting Date", "date": {"is_empty": True}},
                         {"property": "First Surfaced", "date": {"before": day}}]}]},
    ]}


def age_days(posted, first_surfaced, today):
    """Whole days since the Posting Date, else since First Surfaced; None when neither is known (never retire on a guess)."""
    anchor = posted or first_surfaced
    return (today - anchor).days if anchor else None


def action(posted, first_surfaced, applied, saturn, today):
    """'retire' | None. Retire only a stale, unprotected, unapplied job."""
    age = age_days(posted, first_surfaced, today)
    if age is None or age < RETIRE_DAYS:
        return None
    return "retire" if progression.resolve(applied, saturn) == progression.NOT_PROTECTED else None


def purge_database(live, now):
    counts = {"descriptions": 0, "job_rows": 0, "tombstones_expired": 0}
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM v7_job_descriptions d JOIN v7_jobs j ON j.id=d.job_id WHERE j.first_seen < %s",
                       (now - timedelta(days=DESC_DAYS),))
        counts["descriptions"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE first_seen < %s AND (notion_page_id IS NULL OR status='PURGED')",
                       (now - timedelta(days=JOB_ROW_DAYS),))
        counts["job_rows"] = cursor.fetchone()[0]
        if live:
            cursor.execute("DELETE d FROM v7_job_descriptions d JOIN v7_jobs j ON j.id=d.job_id WHERE j.first_seen < %s",
                           (now - timedelta(days=DESC_DAYS),))
            cursor.execute("DELETE s FROM v7_job_sources s JOIN v7_jobs j ON j.id=s.job_id WHERE j.first_seen < %s"
                           " AND (j.notion_page_id IS NULL OR j.status='PURGED')", (now - timedelta(days=JOB_ROW_DAYS),))
            cursor.execute("DELETE FROM v7_jobs WHERE first_seen < %s AND (notion_page_id IS NULL OR status='PURGED')",
                           (now - timedelta(days=JOB_ROW_DAYS),))
            counts["tombstones_expired"] = tombstone.expire(cursor, now)
    return counts


def _date(prop):
    value = ((prop or {}).get("date") or {}).get("start")
    return date.fromisoformat(value[:10]) if value else None


def candidates(client, now, lane):
    """(page id, Posting Date, First Surfaced) of one lane from the filtered query, up to MAX_PAGES pages of 100."""
    found, cursor = [], None
    for _ in range(MAX_PAGES):
        body = {"page_size": limits.NOTION_PAGE_SIZE, "filter": query_filter(now - timedelta(days=RETIRE_DAYS), lane)}
        if cursor:
            body["start_cursor"] = cursor
        data = client.call("POST", f"/data_sources/{client.source}/query", body)
        for page in data.get("results", []):
            props = page.get("properties") or {}
            found.append((page["id"], _date(props.get("Posting Date")), _date(props.get("First Surfaced"))))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return found


def purge_notion(live, now, environ=None):
    counts = {"candidates": 0, "ours": 0, "retired": 0, "failed": 0, "protected": 0, "handoff": "not_configured", "target": ledger.OK}
    client = notion_client.Client(environ) if environ is not None else notion_client.Client()
    counts["target"] = ledger.verify(client)                         # never query or trash against a data source that is not the Job Ledger
    if counts["target"] != ledger.OK:
        return counts
    pipeline_id = ((environ if environ is not None else os.environ).get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    opportunities = []
    if pipeline_id:                                                  # INT-7.1A: protect pursuits with a live hiring process
        opportunities, counts["handoff"] = hiring_pipeline.snapshot(client, pipeline_id)
        if counts["handoff"] != "ok":
            return counts if not live else {**counts, "retired": 0}                  # unreadable handoff = UNKNOWN: fail closed
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT lane FROM v7_jobs WHERE notion_page_id IS NOT NULL")
        names = {name for (raw,) in cursor.fetchall() for name in (raw, lanes.lane_for(raw))}    # rows carry the lane they were published under
    pages = [page for lane in sorted(names) for page in candidates(client, now, lane)]            # slow: no DB connection open
    counts["candidates"] = len(pages)
    with store.connect() as connection, connection.cursor() as cursor:
        ours = {}
        for page_id, posted, first in pages:
            cursor.execute("SELECT id, dedupe_key, fuzzy_key, company, title FROM v7_jobs WHERE notion_page_id=%s", (page_id,))
            row = cursor.fetchone()
            if row:
                ours[page_id] = (row, posted, first)
    counts["ours"] = len(ours)
    if not live:
        return counts
    writes, today = 0, now.date()
    for page_id, ((job_id, key, fuzzy, company, title), posted, first) in ours.items():
        # the filter already excluded Applied and Saturn Decision rows; the handoff can still protect an unapplied pursuit
        if hiring_pipeline.handoff_for(company, title, opportunities).protected:
            counts["protected"] += 1
            continue
        if action(posted, first, False, None, today) != "retire" or writes >= WRITES_PER_RUN:
            continue
        writes += 1
        try:
            client.call("PATCH", f"/pages/{page_id}", {"in_trash": True})
        except notion_client.NotionError:
            counts["failed"] += 1
            if counts["failed"] >= 3:
                break
            continue
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE v7_jobs SET status='PURGED', updated_at=%s WHERE id=%s", (now, job_id))
            tombstone.write(cursor, key, fuzzy, now)
        counts["retired"] += 1
    return counts


def run(live, environ=None):
    now = _now()
    with store.connect() as connection:
        store.ensure_schema(connection)
    return {"database": purge_database(live, now), "notion": purge_notion(live, now, environ)}
