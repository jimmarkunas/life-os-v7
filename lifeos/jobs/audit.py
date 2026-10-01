"""Precision audit: no-network pass over READY and PUBLISHED rows with the quality guards.

A failing row goes back to NEW (final link cleared, reason audit_*) so it is resolved again with the stricter rules.
A failing PUBLISHED row also has its Notion page trashed and its ledger hash removed, UNLESS a human pursuit may exist: lifeos.jobs.guard
protects a page that is Applied, has an Applied On date or a Saturn Decision, matches an active hiring-pipeline opportunity, or cannot be read.
A protected row is left exactly as it is (PUBLISHED, page untouched) and only counted. Logs: counts only.
"""
from datetime import datetime, timezone
import os

from lifeos.jobs import guard, hiring_pipeline, ledger, quality, store
from lifeos.jobs.identity import url_key
from lifeos.platform import notion_client


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def judge(url, full_text):
    """None when the row is sound, else a fixed reason code."""
    problem = quality.url_problem(url)
    if problem:
        return "audit_url_" + problem
    problem = quality.jd_problem(full_text)
    return "audit_jd_" + problem if problem else None


def run(limit, live, environ=os.environ):
    counts = {"checked": 0, "failed": 0, "demoted": 0, "trashed": 0, "trash_errors": 0, "by_reason": {}, "protected": {}}
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT j.id, j.status, j.final_apply_url, j.notion_page_id, d.full_text, j.company, j.title FROM v7_jobs j"
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
    client = notion_client.Client(environ) if any(r[3] for r, _ in bad) else None
    target = ledger.verify(client) if client else ledger.OK
    if target != ledger.OK:                                              # the Job Ledger target check comes before any read of a page or a trash
        counts["protected"][target] = len([1 for r, _ in bad if r[3]])
        bad = [(r, why) for r, why in bad if not r[3]]
    opportunities = []
    pipeline_id = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    if client and pipeline_id:
        opportunities, state = hiring_pipeline.snapshot(client, pipeline_id)
        if state != "ok":
            counts["protected"]["handoff_unreadable"] = len([1 for r, _ in bad if r[3]])
            bad = [(r, why) for r, why in bad if not r[3]]            # cannot rule out a pursuit: leave every published row alone
    for (job_id, status, url, page_id, _, company, title), why in bad:
        if page_id and client:
            held = guard.protection(client, page_id, company, title, opportunities)
            if held:
                counts["protected"][held] = counts["protected"].get(held, 0) + 1
                continue
            try:
                client.call("PATCH", f"/pages/{page_id}", {"in_trash": True})
                counts["trashed"] += 1
            except notion_client.NotionError:
                counts["trash_errors"] += 1
                continue                                   # keep the row PUBLISHED so the next audit retries
        with store.connect() as connection, connection.cursor() as cursor:
            if page_id and url:
                cursor.execute("DELETE FROM v7_ledger_urls WHERE url_hash=%s AND source='v7'", (url_key(url),))
            cursor.execute("UPDATE v7_jobs SET status='NEW', final_apply_url=NULL, notion_page_id=NULL,"
                           " unresolved_reason=%s, resolve_attempts=0, updated_at=%s WHERE id=%s", (why, _now(), job_id))
            cursor.execute("DELETE FROM v7_job_descriptions WHERE job_id=%s", (job_id,))
        counts["demoted"] += 1
    return counts
