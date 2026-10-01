"""Retention (weekly). Hostinger: descriptions go at 90 days, tombstone rows (dedupe key, status, dates) at 365.
Ledger URL hashes are never purged. Notion, OUR pages only (matched by stored page id; the pre-existing ledger rows are
never touched) and only untouched ones (Applied unchecked, no Saturn Decision, Lifecycle not Application):
  14+ days old  -> Lifecycle = Expired, Expired At = today
  90+ days old  -> trashed (recoverable 30 days) and marked PURGED here.
One filtered Notion query per page of 100 (the approved weekly read). Counts only.
"""
from datetime import datetime, timedelta, timezone

from pipeline import limits, notion, store

EXPIRE_DAYS = 14
TRASH_DAYS = 90
DESC_DAYS = 90
TOMBSTONE_DAYS = 365
MAX_PAGES = 20
WRITES_PER_RUN = 150


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def query_filter(cutoff):
    return {"and": [
        {"property": "Applied", "checkbox": {"equals": False}},
        {"property": "Saturn Decision", "select": {"is_empty": True}},
        {"property": "Lifecycle", "select": {"does_not_equal": "Application"}},
        {"property": "Visible Lane", "select": {"equals": "Newsletter"}},
        {"property": "Created At", "created_time": {"before": cutoff.isoformat() + "Z"}},
    ]}


def action(created, lifecycle, now):
    """'trash' | 'expire' | None for a page created at `created` (naive UTC)."""
    age = (now - created).days
    if age >= TRASH_DAYS:
        return "trash"
    if age >= EXPIRE_DAYS and lifecycle != "Expired":
        return "expire"
    return None


def purge_database(live, now):
    counts = {"descriptions": 0, "tombstones": 0}
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM v7_job_descriptions d JOIN v7_jobs j ON j.id=d.job_id WHERE j.first_seen < %s",
                       (now - timedelta(days=DESC_DAYS),))
        counts["descriptions"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE first_seen < %s AND (notion_page_id IS NULL OR status='PURGED')",
                       (now - timedelta(days=TOMBSTONE_DAYS),))
        counts["tombstones"] = cursor.fetchone()[0]
        if live:
            cursor.execute("DELETE d FROM v7_job_descriptions d JOIN v7_jobs j ON j.id=d.job_id WHERE j.first_seen < %s",
                           (now - timedelta(days=DESC_DAYS),))
            cursor.execute("DELETE s FROM v7_job_sources s JOIN v7_jobs j ON j.id=s.job_id WHERE j.first_seen < %s"
                           " AND (j.notion_page_id IS NULL OR j.status='PURGED')", (now - timedelta(days=TOMBSTONE_DAYS),))
            cursor.execute("DELETE FROM v7_jobs WHERE first_seen < %s AND (notion_page_id IS NULL OR status='PURGED')",
                           (now - timedelta(days=TOMBSTONE_DAYS),))
    return counts


def candidates(client, now):
    """Pages (id, created, lifecycle) from the filtered query, up to MAX_PAGES pages of 100."""
    found, cursor = [], None
    for _ in range(MAX_PAGES):
        body = {"page_size": limits.NOTION_PAGE_SIZE, "filter": query_filter(now - timedelta(days=EXPIRE_DAYS))}
        if cursor:
            body["start_cursor"] = cursor
        data = client.call("POST", f"/data_sources/{client.source}/query", body)
        for page in data.get("results", []):
            props = page.get("properties") or {}
            life = ((props.get("Lifecycle") or {}).get("select") or {}).get("name")
            created = datetime.fromisoformat(page["created_time"].replace("Z", "+00:00")).replace(tzinfo=None)
            found.append((page["id"], created, life))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return found


def purge_notion(live, now, environ=None):
    counts = {"candidates": 0, "ours": 0, "expired": 0, "trashed": 0, "failed": 0}
    client = notion.Client(environ) if environ is not None else notion.Client()
    pages = candidates(client, now)                                   # slow: no DB connection open
    counts["candidates"] = len(pages)
    with store.connect() as connection, connection.cursor() as cursor:
        ours = {}
        for page_id, created, life in pages:
            cursor.execute("SELECT id FROM v7_jobs WHERE notion_page_id=%s", (page_id,))
            row = cursor.fetchone()
            if row:
                ours[page_id] = (row[0], created, life)
    counts["ours"] = len(ours)
    if not live:
        return counts
    writes = 0
    for page_id, (job_id, created, life) in ours.items():
        todo = action(created, life, now)
        if not todo or writes >= WRITES_PER_RUN:
            continue
        writes += 1
        try:
            if todo == "trash":
                client.call("PATCH", f"/pages/{page_id}", {"in_trash": True})
            else:
                client.call("PATCH", f"/pages/{page_id}", {"properties": {
                    "Lifecycle": {"select": {"name": "Expired"}},
                    "Expired At": {"date": {"start": now.date().isoformat()}}}})
        except notion.NotionError:
            counts["failed"] += 1
            if counts["failed"] >= 3:
                break
            continue
        with store.connect() as connection, connection.cursor() as cursor:
            if todo == "trash":
                cursor.execute("UPDATE v7_jobs SET status='PURGED', updated_at=%s WHERE id=%s", (now, job_id))
            else:
                cursor.execute("UPDATE v7_jobs SET notion_expired_at=%s, updated_at=%s WHERE id=%s", (now, now, job_id))
        counts["trashed" if todo == "trash" else "expired"] += 1
    return counts


def run(live, environ=None):
    now = _now()
    with store.connect() as connection:
        store.ensure_schema(connection)
    return {"database": purge_database(live, now), "notion": purge_notion(live, now, environ)}
