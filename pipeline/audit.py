"""Precision audit: no-network pass over READY and PUBLISHED rows with the quality guards.

A failing row goes back to NEW (final link cleared, reason audit_*) so it is resolved again with the stricter rules.
A failing PUBLISHED row also has its Notion page trashed and its ledger hash removed. Logs: counts only.
"""
from datetime import datetime, timezone

from pipeline import notion, quality, store


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def judge(url, full_text):
    """None when the row is sound, else a fixed reason code."""
    problem = quality.url_problem(url)
    if problem:
        return "audit_url_" + problem
    problem = quality.jd_problem(full_text)
    return "audit_jd_" + problem if problem else None


def run(limit, live, environ=None):
    counts = {"checked": 0, "failed": 0, "demoted": 0, "trashed": 0, "trash_errors": 0, "by_reason": {}}
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT j.id, j.status, j.final_apply_url, j.notion_page_id, d.full_text FROM v7_jobs j"
                           " LEFT JOIN v7_job_descriptions d ON d.job_id=j.id WHERE j.status IN ('READY','PUBLISHED')"
                           " ORDER BY j.id LIMIT %s", (limit,))
            rows = cursor.fetchall()
    counts["checked"] = len(rows)
    bad = [(r, judge(r[2], r[4])) for r in rows]
    bad = [(r, why) for r, why in bad if why]
    counts["failed"] = len(bad)
    for _, why in bad:
        counts["by_reason"][why] = counts["by_reason"].get(why, 0) + 1
    if not live or not bad:
        return counts
    client = notion.Client(environ) if any(r[3] for r, _ in bad) else None
    for (job_id, status, url, page_id, _), why in bad:
        if page_id and client:
            try:
                client.call("PATCH", f"/pages/{page_id}", {"in_trash": True})
                counts["trashed"] += 1
            except notion.NotionError:
                counts["trash_errors"] += 1
                continue                                   # keep the row PUBLISHED so the next audit retries
        with store.connect() as connection, connection.cursor() as cursor:
            if page_id and url:
                cursor.execute("DELETE FROM v7_ledger_urls WHERE url_hash=%s AND source='v7'", (notion.url_key(url),))
            cursor.execute("UPDATE v7_jobs SET status='NEW', final_apply_url=NULL, notion_page_id=NULL,"
                           " unresolved_reason=%s, resolve_attempts=0, updated_at=%s WHERE id=%s", (why, _now(), job_id))
            cursor.execute("DELETE FROM v7_job_descriptions WHERE job_id=%s", (job_id,))
        counts["demoted"] += 1
    return counts
