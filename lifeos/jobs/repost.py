"""D6 - reposts. One real opening is one row: a repost never creates a second row or resets the original age.

link(): a NEW row whose normalized company+title+location matches a live row seen in the last 60 days becomes a
DUPLICATE of it (repost_of) and bumps the original's seen_count/last_seen. Ghost risk is evidence, never a filter:
3+ sightings inside 60 days, or no real posting date for 21+ days, flags `ghost_flag` (Review Reason in Notion).
sync_last_seen(): daily Notion write per resighted/flagged page (no reads).
"""
from datetime import datetime, timedelta, timezone

from lifeos.jobs import store
from lifeos.platform import notion_client

WINDOW_DAYS = 60
GHOST_SEEN = 3
GHOST_UNDATED_DAYS = 21
SYNC_EVERY_HOURS = 20
SYNC_PER_RUN = 100
LIVE_STATES = ("NEW", "RESOLVED", "READY", "PUBLISHED", "HOLD")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def link(cursor, key, fuzzy, now):
    """Called right after a fresh insert (dedupe_key=key). Returns the original id when it was a repost, else None."""
    cursor.execute("SELECT id FROM v7_jobs WHERE fuzzy_key=%s AND dedupe_key<>%s AND repost_of IS NULL AND status IN %s"
                   " AND first_seen >= %s ORDER BY first_seen LIMIT 2",
                   (fuzzy, key, LIVE_STATES, now - timedelta(days=WINDOW_DAYS)))
    rows = cursor.fetchall()
    if not rows:
        return None
    if len(rows) > 1:                                  # ambiguous identity evidence: never pick one arbitrarily, keep both and mark it
        cursor.execute("UPDATE v7_jobs SET unresolved_reason='repost_ambiguous', updated_at=%s WHERE dedupe_key=%s", (now, key))
        return None
    row = rows[0]
    cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', repost_of=%s, unresolved_reason='repost', updated_at=%s"
                   " WHERE dedupe_key=%s", (row[0], now, key))
    cursor.execute("UPDATE v7_jobs SET seen_count=seen_count+1, last_seen=%s WHERE id=%s", (now, row[0]))
    return row[0]


def is_ghost(seen_count, first_seen, posted_date, now):
    """Evidence only. True for 3+ sightings inside the window, or an undated job older than 21 days."""
    age = (now - first_seen).days
    if seen_count >= GHOST_SEEN and age <= WINDOW_DAYS:
        return True
    return posted_date is None and age >= GHOST_UNDATED_DAYS


def sync_props(last_seen, ghost):
    props = {"Last Seen": {"date": {"start": last_seen.date().isoformat()}}}
    if ghost:
        props["Review Reason"] = {"rich_text": notion_client.rich_text("possible ghost: reposted or long-open without a real date")}
    return props


def sync_last_seen(limit=SYNC_PER_RUN, live=False, environ=None):
    counts = {"due": 0, "synced": 0, "ghost": 0, "failed": 0}
    now = _now()
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, notion_page_id, seen_count, first_seen, last_seen, posted_date FROM v7_jobs"
                           " WHERE status='PUBLISHED' AND notion_page_id IS NOT NULL AND (notion_synced_at IS NULL"
                           " OR (notion_synced_at < %s AND last_seen > notion_synced_at)) ORDER BY id LIMIT %s",
                           (now - timedelta(hours=SYNC_EVERY_HOURS), limit))
            rows = cursor.fetchall()
    counts["due"] = len(rows)
    if not live or not rows:
        return counts
    client = notion_client.Client(environ) if environ is not None else notion_client.Client()
    for job_id, page_id, seen, first_seen, last_seen, posted in rows:
        ghost = is_ghost(seen, first_seen, posted, now)
        try:
            client.call("PATCH", f"/pages/{page_id}", {"properties": sync_props(last_seen, ghost)})
        except notion_client.NotionError:
            counts["failed"] += 1
            if counts["failed"] >= 3:
                break
            continue
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE v7_jobs SET notion_synced_at=%s, ghost_flag=%s WHERE id=%s", (now, int(ghost), job_id))
        counts["synced"] += 1
        counts["ghost"] += int(ghost)
    return counts
