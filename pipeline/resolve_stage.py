"""Resolve stage: NEW jobs -> final apply link, saved to Hostinger (D1: nothing without a final link is ever published).

Per source: read a batch of NEW rows, close the connection, run the (slow) free-Chromium link chain, reconnect, save.
Outcome per job: RESOLVED (final_apply_url + apply_kind), DUPLICATE (same final link already stored), or left NEW with a
fixed unresolved_reason so the next run retries. Logs: counts only.
"""
import hashlib
from datetime import datetime, timezone

from pipeline import store


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def url_key(url):
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()


def pick(connection, source, limit):
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, source_url FROM v7_jobs WHERE status='NEW' AND source=%s "
                       "AND (unresolved_reason IS NULL OR unresolved_reason NOT LIKE 'closed%%') "
                       "ORDER BY last_seen DESC LIMIT %s", (source, limit))
        return [(row[0], row[1]) for row in cursor.fetchall()]


def pick_rows(connection, source, limit):
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, source_url, company, title, location_text FROM v7_jobs WHERE status='NEW' AND source=%s "
                       "AND (unresolved_reason IS NULL OR unresolved_reason NOT LIKE 'closed%%') "
                       "ORDER BY last_seen DESC LIMIT %s", (source, limit))
        return [tuple(row) for row in cursor.fetchall()]


def apply_result(connection, job_id, result):
    """Write one outcome. Returns 'resolved' | 'duplicate' | 'pending'."""
    now = _now()
    url, kind = result.get("url"), result.get("kind")
    with connection.cursor() as cursor:
        if result.get("outcome") == "landed" and url and kind:
            cursor.execute("SELECT id FROM v7_jobs WHERE final_apply_url=%s AND id<>%s LIMIT 1", (url, job_id))
            if cursor.fetchone():
                cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', final_apply_url=NULL, unresolved_reason=%s, "
                               "updated_at=%s WHERE id=%s", ("dup_of_final_url", now, job_id))
                return "duplicate"
            cursor.execute("UPDATE v7_jobs SET status='RESOLVED', final_apply_url=%s, apply_kind=%s, "
                           "unresolved_reason=NULL, updated_at=%s WHERE id=%s", (url, kind, now, job_id))
            return "resolved"
        reason = (result.get("outcome") or "unknown")[:100]
        cursor.execute("UPDATE v7_jobs SET unresolved_reason=%s, updated_at=%s WHERE id=%s", (reason, now, job_id))
        return "pending"


def run_rows(source, limit, live, resolver):
    """Like run(), but the resolver gets full rows [(id, source_url, company, title, location)]."""
    with store.connect() as connection:
        store.ensure_schema(connection)
        rows = pick_rows(connection, source, limit)
    counts = {"picked": len(rows), "resolved": 0, "duplicate": 0, "pending": 0, "closed": 0, "kind": {}, "why": {}}
    if not rows:
        return counts
    results = resolver(rows)
    if not live:
        counts["dry_run"] = True
    with store.connect() as connection:
        for row, result in zip(rows, results):
            key = result.get("outcome") if result.get("outcome") != "landed" else "landed:" + str(result.get("kind"))
            counts["why"][key] = counts["why"].get(key, 0) + 1
            if not live:
                continue
            if result.get("outcome") == "closed":
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE v7_jobs SET status='CLOSED', unresolved_reason='closed', updated_at=%s "
                                   "WHERE id=%s", (_now(), row[0]))
                counts["closed"] += 1
                continue
            verdict = apply_result(connection, row[0], result)
            counts[verdict] += 1
            if verdict == "resolved":
                counts["kind"][result["kind"]] = counts["kind"].get(result["kind"], 0) + 1
    return counts


def run(source, limit, live, resolver):
    """resolver(urls) -> list of result dicts aligned with urls."""
    with store.connect() as connection:
        store.ensure_schema(connection)
        rows = pick(connection, source, limit)
    counts = {"picked": len(rows), "resolved": 0, "duplicate": 0, "pending": 0, "kind": {}}
    if not rows:
        return counts
    results = resolver([url for _, url in rows])
    if not live:
        for result in results:
            key = result["outcome"] if result.get("outcome") != "landed" else "landed:" + str(result.get("kind"))
            counts["kind"][key] = counts["kind"].get(key, 0) + 1
        counts["dry_run"] = True
        return counts
    with store.connect() as connection:
        for (job_id, _), result in zip(rows, results):
            verdict = apply_result(connection, job_id, result)
            counts[verdict] += 1
            if verdict == "resolved":
                counts["kind"][result["kind"]] = counts["kind"].get(result["kind"], 0) + 1
    return counts
