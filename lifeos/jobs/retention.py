"""Weekly retention (docs/LANES.md): description, row and tombstone expiry, and trashing our stale Notion pages.

Database: descriptions go at 90 days; rows never published or PURGED at 365; tombstones expire at 90. Notion, our
pages only: an unapplied job with no Saturn Decision, 30+ days old and NOT_PROTECTED, is trashed, marked PURGED and
tombstoned. A job on its employer's own board is retired only once a complete board listing lacks it. Counts only.

"""
from datetime import date, timedelta
import os

from lifeos.jobs import hiring_pipeline, lanes, ledger, progression, store, tombstone
from lifeos.platform import limits, notion_client

RETIRE_DAYS = 30
DESC_DAYS = 90
JOB_ROW_DAYS = 365
MAX_PAGES = 20
PURGE_EVERY = timedelta(days=6, hours=12)
RETRY_AFTER = timedelta(hours=6)
# marker rows in v7_alerts: last success, last attempt
OK_KEY, TRY_KEY = "purge_ok", "purge_try"
KNOWN_LANES = ("Newsletter", "US Web", "US Remote", "Scale-Up", "Scale-up", "Skilled Worker")
WRITES_PER_RUN = 150


_now = store.now


def query_filter(cutoff, lane):
    """Unapplied, no Saturn Decision, our lane, old by Posting Date or First Surfaced (`action` re-checks)."""
    day = cutoff.date().isoformat()
    return {"and": [
        {"property": "Applied", "checkbox": {"equals": False}},
        {"property": "Applied On", "date": {"is_empty": True}},
        {"property": "Saturn Decision", "select": {"is_empty": True}},
        {"property": "Visible Lane", "select": {"equals": lane}},
        {"or": [{"property": "Posting Date", "date": {"before": day}},
                {"property": "First Surfaced", "date": {"before": day}}]},
    ]}


def age_days(posted, first_surfaced, today):
    anchor = posted or first_surfaced
    return (today - anchor).days if anchor else None


def _lane_age(lane):
    return None if lane in lanes.LANE_ALIAS else lanes.first_party_age(lane)


def retire_days(lane):
    """(query cutoff in days, least age in days to retire) for a lane."""
    days = _lane_age(lane)
    return (days, days + 1) if days else (RETIRE_DAYS, RETIRE_DAYS)


def action(posted, first_surfaced, applied, saturn, today, days=RETIRE_DAYS):
    """'retire' for a stale, unprotected, unapplied job, else None."""
    age = age_days(posted, first_surfaced, today)
    if age is None or age < days:
        return None
    return "retire" if progression.resolve(applied, saturn) == progression.NOT_PROTECTED else None


def purge_database(live, now):
    counts = {"descriptions": 0, "job_rows": 0, "tombstones_expired": 0}
    old_descriptions = "FROM v7_job_descriptions d JOIN v7_jobs j ON j.id=d.job_id WHERE j.first_seen < %s"
    old_jobs = "first_seen < %s AND (notion_page_id IS NULL OR status='PURGED')"
    descriptions, jobs = (now - timedelta(days=DESC_DAYS),), (now - timedelta(days=JOB_ROW_DAYS),)
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) " + old_descriptions, descriptions)
        counts["descriptions"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE " + old_jobs, jobs)
        counts["job_rows"] = cursor.fetchone()[0]
        if live:
            cursor.execute("DELETE d " + old_descriptions, descriptions)
            cursor.execute("DELETE s FROM v7_job_sources s JOIN v7_jobs j ON j.id=s.job_id WHERE j.first_seen < %s"
                           " AND (j.notion_page_id IS NULL OR j.status='PURGED')", jobs)
            cursor.execute("DELETE FROM v7_jobs WHERE " + old_jobs, jobs)
            counts["tombstones_expired"] = tombstone.expire(cursor, now)
    return counts


def _date(prop):
    value = ((prop or {}).get("date") or {}).get("start")
    return date.fromisoformat(value[:10]) if value else None


def candidates(client, now, lane):
    """(page id, Posting Date, First Surfaced, lane) of one lane from the filtered query, up to MAX_PAGES pages."""
    found, cursor = [], None
    for _ in range(MAX_PAGES):
        body = {"page_size": limits.NOTION_PAGE_SIZE, "filter": query_filter(now - timedelta(days=retire_days(lane)[0]), lane)}
        if cursor:
            body["start_cursor"] = cursor
        try:
            data = client.call("POST", f"/data_sources/{client.source}/query", body)
        # name the lane and detail: a bare HTTP 400 hides the request
        except notion_client.NotionError as error:
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
    counts["target"] = ledger.verify(client)
    if counts["target"] != ledger.OK:
        return counts
    pipeline_id = ((environ if environ is not None else os.environ).get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    opportunities = []
    if pipeline_id:
        opportunities, counts["handoff"] = hiring_pipeline.snapshot(client, pipeline_id, ledger=client)
        if counts["handoff"] != "ok":
            return counts if not live else {**counts, "retired": 0}  # unreadable handoff: fail closed
    options = {option.casefold(): option for option in ledger.select_options(client, "Visible Lane")}
    if not options:
        return {**counts, "target": "lane_options_unreadable"}
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT lane FROM v7_jobs WHERE notion_page_id IS NOT NULL")
        wanted = {name for (raw,) in cursor.fetchall() for name in (raw, lanes.lane_for(raw))}
    # filter only by the Ledger's own option spelling (`Scale-up`)
    names = {options[name.casefold()] for name in wanted if name and name.casefold() in options}
    counts["lanes"] = len(names)
    pages = [page for lane in sorted(names) for page in candidates(client, now, lane)]
    counts["candidates"] = len(pages)
    with store.connect() as connection, connection.cursor() as cursor:
        ours, open_on_board = {}, set()
        for page_id, posted, first, lane in pages:
            cursor.execute("SELECT id, dedupe_key, fuzzy_key, company, title, source FROM v7_jobs WHERE notion_page_id=%s", (page_id,))
            row = cursor.fetchone()
            if not row:
                continue
            ours[page_id] = (row[:5], posted, first, lane)
            if lanes.first_party_source(row[5]) and _lane_age(lane) is None:
                # open on its own board unless a complete listing marks it REMOVED
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
    """True when forced, or with no success in PURGE_EVERY and no attempt in RETRY_AFTER."""
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
    """True for a purge that failed closed (wrong target, unreadable hand-off or lane options)."""
    return notion.get("target") != ledger.OK or notion.get("handoff") not in ("ok", "not_configured")


def run(live, environ=None, now=None, force=None):
    now = now or _now()
    force = (os.environ.get("PURGE_FORCE") == "true") if force is None else force
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
                _mark(cursor, TRY_KEY, now)
    result = {"database": purge_database(live, now), "notion": purge_notion(live, now, environ)}
    if incomplete(result["notion"]):
        raise notion_client.NotionError(f"PURGE_INCOMPLETE target={result['notion'].get('target')} handoff={result['notion'].get('handoff')}")
    if live:
        with store.connect() as connection, connection.cursor() as cursor:
            _mark(cursor, OK_KEY, now)
    return result
