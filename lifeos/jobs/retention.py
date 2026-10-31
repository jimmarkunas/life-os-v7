"""Retention (weekly), per the Jobs canon plus Jim's rules (docs/LANES.md).

Hostinger: job descriptions go at 90 days; job rows that were never published or are already PURGED go at 365; suppression tombstones expire at 90 days.
Notion, OUR pages only (matched by stored page id; pre-existing ledger rows are never touched):
  an UNAPPLIED job with no Saturn Decision whose age is 30+ days (employer Posting Date, else First Surfaced; never Created At)
  and for which progression.resolve says NOT_PROTECTED  ->  trashed (recoverable 30 days), marked PURGED here, and a 90-day tombstone is written.
A job read off its employer's own board (source web:<board>) is open while the board lists it (D111): it is retired only once a complete listing of that board no longer has it (D167).
LIFE-212 (Jim, Oct 31 2026): a lane whose policy judges employer-board jobs by age (US Remote 14 days, Scale-Up 30) retires a job older than that age, listed on its board or not.
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
PURGE_EVERY = timedelta(days=6, hours=12)       # weekly, with slack so a late tick does not skip a week
RETRY_AFTER = timedelta(hours=6)                # a failed attempt is tried again after this, until one succeeds
OK_KEY, TRY_KEY = "purge_ok", "purge_try"       # marker rows in v7_alerts (status RESOLVED, so the alerts stage never reads them): last success, last attempt
KNOWN_LANES = ("Newsletter", "US Web", "US Remote", "Scale-Up", "Scale-up", "Skilled Worker")
WRITES_PER_RUN = 150


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def query_filter(cutoff, lane):
    """Unapplied, no Saturn Decision, our lane, and old by the Posting Date or the First Surfaced date. The legacy Lifecycle field is not read.
    Notion allows a compound filter two levels deep (an `and` holding one `or`); the earlier shape nested a third level and Notion refused every
    weekly query with HTTP 400. The two date tests are therefore one flat `or`, which can also return a row with a recent Posting Date and an old
    First Surfaced; `action` re-checks each candidate against the employer Posting Date (else First Surfaced) before anything is retired."""
    day = cutoff.date().isoformat()
    return {"and": [
        {"property": "Applied", "checkbox": {"equals": False}},
        {"property": "Applied On", "date": {"is_empty": True}},            # an Applied On date is human state even if the checkbox is clear
        {"property": "Saturn Decision", "select": {"is_empty": True}},
        {"property": "Visible Lane", "select": {"equals": lane}},
        {"or": [{"property": "Posting Date", "date": {"before": day}},
                {"property": "First Surfaced", "date": {"before": day}}]},
    ]}


def age_days(posted, first_surfaced, today):
    """Whole days since the Posting Date, else since First Surfaced; None when neither is known (never retire on a guess)."""
    anchor = posted or first_surfaced
    return (today - anchor).days if anchor else None


def _lane_age(lane):
    """The Ledger lane's own first-party max age (LIFE-212), or None. Only the lane itself: the Newsletter lane keeps RETIRE_DAYS and D167."""
    return None if lane in lanes.LANE_ALIAS else lanes.first_party_age(lane)


def retire_days(lane):
    """(query cutoff in days, least age in days to retire) for a lane. LIFE-212 (Jim, Oct 31 2026): a lane that judges employer-board jobs by age (US Remote 14,
    Scale-Up 30) retires a job OLDER than that age, as admission bounces it ("older than N days"); every other lane keeps RETIRE_DAYS."""
    days = _lane_age(lane)
    return (days, days + 1) if days else (RETIRE_DAYS, RETIRE_DAYS)


def action(posted, first_surfaced, applied, saturn, today, days=RETIRE_DAYS):
    """'retire' | None. Retire only a stale, unprotected, unapplied job."""
    age = age_days(posted, first_surfaced, today)
    if age is None or age < days:
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
    """(page id, Posting Date, First Surfaced, lane) of one lane from the filtered query, up to MAX_PAGES pages of 100."""
    found, cursor = [], None
    for _ in range(MAX_PAGES):
        body = {"page_size": limits.NOTION_PAGE_SIZE, "filter": query_filter(now - timedelta(days=retire_days(lane)[0]), lane)}     # LIFE-212: the lane's own age
        if cursor:
            body["start_cursor"] = cursor
        try:
            data = client.call("POST", f"/data_sources/{client.source}/query", body)
        except notion_client.NotionError as error:                    # D165: a bare HTTP 400 hid which request Notion refused; name the lane and what Notion named
            tag = lane if lane in KNOWN_LANES else "other"
            raise notion_client.NotionError(" ".join(part for part in (str(error), f"lane={tag}", error.detail) if part)) from None
        for page in data.get("results", []):
            props = page.get("properties") or {}
            found.append((page["id"], _date(props.get("Posting Date")), _date(props.get("First Surfaced")), lane))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return found


def purge_notion(live, now, environ=None):
    counts = {"candidates": 0, "ours": 0, "retired": 0, "failed": 0, "protected": 0, "kept_open": 0, "lanes": 0, "handoff": "not_configured", "target": ledger.OK}
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
    options = {option.casefold(): option for option in ledger.select_options(client, "Visible Lane")}
    if not options:
        return {**counts, "target": "lane_options_unreadable"}              # cannot name a lane the Ledger knows: fail closed, ask nothing
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT lane FROM v7_jobs WHERE notion_page_id IS NOT NULL")
        wanted = {name for (raw,) in cursor.fetchall() for name in (raw, lanes.lane_for(raw))}    # rows carry the lane they were published under
    # D165: the Ledger spells the option `Scale-up` while the database says `Scale-Up`, and Notion refuses a filter naming an option it does not have
    # (HTTP 400, validation_error). Ask only for the Ledger's own spelling; a lane with no such option cannot have rows there.
    names = {options[name.casefold()] for name in wanted if name and name.casefold() in options}
    counts["lanes"] = len(names)
    pages = [page for lane in sorted(names) for page in candidates(client, now, lane)]            # slow: no DB connection open
    counts["candidates"] = len(pages)
    with store.connect() as connection, connection.cursor() as cursor:
        ours, open_on_board = {}, set()
        for page_id, posted, first, lane in pages:
            cursor.execute("SELECT id, dedupe_key, fuzzy_key, company, title, source FROM v7_jobs WHERE notion_page_id=%s", (page_id,))
            row = cursor.fetchone()
            if not row:
                continue
            ours[page_id] = (row[:5], posted, first, lane)
            if lanes.first_party_source(row[5]) and _lane_age(lane) is None:      # LIFE-212 (Jim, Oct 31 2026): not in US Remote or Scale-Up, which retire it by age
                # D167 (D111): a role on its employer's own board is open, so its posting age does not retire it. It is retired only once a complete
                # listing of that board no longer has it (the web pass marks it REMOVED). No board record at all cannot prove it closed: keep it.
                cursor.execute("SELECT SUM(state='CURRENT'), SUM(state='REMOVED') FROM v7_source_items WHERE job_id=%s", (row[0],))
                current, removed = cursor.fetchone() or (0, 0)
                if int(current or 0) > 0 or int(removed or 0) == 0:
                    open_on_board.add(page_id)
    counts["ours"] = len(ours)
    counts["kept_open"] = len(open_on_board)
    if not live:
        return counts
    writes, today = 0, now.date()
    for page_id, ((job_id, key, fuzzy, company, title), posted, first, lane) in ours.items():
        # the filter already excluded Applied and Saturn Decision rows; the handoff can still protect an unapplied pursuit
        if page_id in open_on_board:
            continue
        if hiring_pipeline.handoff_for(company, title, opportunities).protected:
            counts["protected"] += 1
            continue
        if action(posted, first, False, None, today, retire_days(lane)[1]) != "retire" or writes >= WRITES_PER_RUN:
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


def due(now, last_ok, last_try, force=False):
    """The purge runs about weekly and catches up: due when there is no success in the last 6.5 days and no attempt in the last 6 hours."""
    if force:
        return True
    if last_ok is not None and now - last_ok < PURGE_EVERY:
        return False
    return last_try is None or now - last_try >= RETRY_AFTER


def _markers(cursor):
    cursor.execute("SELECT alert_key, last_seen FROM v7_alerts WHERE alert_key IN (%s, %s)", (OK_KEY, TRY_KEY))
    return {key: seen for key, seen in cursor.fetchall()}


def _mark(cursor, key, now):
    cursor.execute("INSERT INTO v7_alerts (alert_key, severity, status, first_seen, last_seen, last_notified) VALUES (%s,'info','RESOLVED',%s,%s,NULL) "
                   "ON DUPLICATE KEY UPDATE status='RESOLVED', last_seen=VALUES(last_seen)", (key, now, now))


def incomplete(notion):
    """A purge that fail-closed (wrong target, unreadable hand-off or lane options) did nothing; that is a failure, never a quiet success."""
    return notion.get("target") != ledger.OK or notion.get("handoff") not in ("ok", "not_configured")


def run(live, environ=None, now=None, force=None):
    now = now or _now()
    force = (os.environ.get("PURGE_FORCE") == "true") if force is None else force          # a manual dispatch with Maintenance ticked
    if not live and not force:
        return {"purge": "skipped_dry_run"}
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            markers = _markers(cursor)
            last_ok, last_try = markers.get(OK_KEY), markers.get(TRY_KEY)
            if not due(now, last_ok, last_try, force):
                return {"purge": "not_due", "days_since_ok": None if last_ok is None else (now - last_ok).days}
            if live:
                _mark(cursor, TRY_KEY, now)                                                  # throttle: a failing purge retries every 6 hours, not every tick
    result = {"database": purge_database(live, now), "notion": purge_notion(live, now, environ)}
    if incomplete(result["notion"]):
        raise notion_client.NotionError(f"PURGE_INCOMPLETE target={result['notion'].get('target')} handoff={result['notion'].get('handoff')}")
    if live:
        with store.connect() as connection, connection.cursor() as cursor:
            _mark(cursor, OK_KEY, now)
    return result
