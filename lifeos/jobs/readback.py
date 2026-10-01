"""Ledger read-back: a PUBLISHED job is only finished once the page Notion holds matches what was sent. Checks the Apply URL,
the Stable Job Key, a title, the Fit being present when one was scored, and the v7-jd marker that opens the description body. Fixed reason codes only."""
from datetime import datetime, timezone

from lifeos.jobs import store
from lifeos.jobs.identity import url_key
from lifeos.platform.notion_client import NotionError

BATCH = 60


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _text(prop, kind):
    return "".join(t.get("plain_text", "") for t in (prop or {}).get(kind) or [])


def check(client, page_id, key, url, fit):
    """-> None when the page matches, else a fixed reason."""
    page = client.call("GET", f"/pages/{page_id}")
    if page.get("archived") or page.get("in_trash"):
        return "page_gone"
    props = page.get("properties") or {}
    if _text(props.get("Stable Job Key"), "rich_text") != key:
        return "key_mismatch"
    if url_key((props.get("Apply URL") or {}).get("url") or "") != url_key(url):
        return "url_mismatch"
    if not _text(props.get("Job"), "title"):
        return "no_title"
    if fit is not None and (props.get("LIFE OS Fit") or {}).get("number") is None:
        return "fit_missing"                                                  # a later re-score may change the number; it must be present
    children = client.call("GET", f"/blocks/{page_id}/children?page_size=3").get("results") or []
    first = _text((children[0].get("paragraph") if children else None) or {}, "rich_text")
    if not first.startswith("v7-jd:1") or len(children) < 2:
        return "no_description"
    return None


def run(client, live, limit=BATCH):
    counts = {"checked": 0, "verified": 0, "failed": 0, "why": {}}
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT j.id, j.dedupe_key, j.final_apply_url, j.notion_page_id, f.score FROM v7_jobs j"
                       " LEFT JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status='PUBLISHED' AND j.verified_at IS NULL"
                       " AND j.notion_page_id IS NOT NULL ORDER BY j.updated_at LIMIT %s", (limit,))
        rows = cursor.fetchall()
    for job_id, key, url, page_id, fit in rows:
        counts["checked"] += 1
        try:
            why = check(client, page_id, key, url, fit)
        except NotionError as error:
            why = str(error).lower()
        if why:
            counts["failed"] += 1
            counts["why"][why] = counts["why"].get(why, 0) + 1
            continue
        counts["verified"] += 1
        if live:
            with store.connect() as connection, connection.cursor() as cursor:
                cursor.execute("UPDATE v7_jobs SET verified_at=%s WHERE id=%s", (_now(), job_id))
    return counts
