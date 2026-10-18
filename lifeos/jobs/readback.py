"""Ledger read-back: a PUBLISHED job is only finished once the page Notion holds matches what was sent. Checks the Apply URL,
the Stable Job Key, a title, the Fit being present when one was scored, and the v7-jd marker that opens the description body. Fixed reason codes only.

D212 (Jim, LIFE-76 10451): every page is verified by reading Notion. The properties come from ONE data-source query per KEY_CHUNK Stable Job Keys (an OR of
exact-key filters) instead of a GET per page; the body marker is still read per page. A page the query does not return is `page_gone`; two pages with one key is
`key_ambiguous`. A create response is never a read-back."""
from datetime import datetime, timezone
import os

from lifeos.jobs import ledger, store
from lifeos.jobs.identity import url_key
from lifeos.platform.notion_client import Client, NotionError

BATCH = 60
KEY_CHUNK = 25          # exact-key filters per Ledger query (one OR); a batch of 60 is three queries instead of 60 page GETs


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _text(prop, kind):
    return "".join(t.get("plain_text", "") for t in (prop or {}).get(kind) or [])


def properties_match(page, key, url, fit):
    """-> None when the page's stored properties match what was sent, else a fixed reason. Shared by the read-back and publish's check of the create response (D211)."""
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
    return None


def check(client, page_id, key, url, fit):
    """-> None when the page matches, else a fixed reason."""
    why = properties_match(client.call("GET", f"/pages/{page_id}"), key, url, fit)
    if why:
        return why
    children = client.call("GET", f"/blocks/{page_id}/children?page_size=3").get("results") or []
    first = _text((children[0].get("paragraph") if children else None) or {}, "rich_text")
    if not first.startswith("v7-jd:1") or len(children) < 2:
        return "no_description"
    return None


def _bare(page_id):
    return str(page_id or "").replace("-", "").lower()


def ledger_pages(client, keys):
    """{key: [page, ...]} as the Ledger holds them now: one data-source query per KEY_CHUNK keys, paged. An unreadable answer raises (never read as `no page`)."""
    found = {key: [] for key in keys}
    for start in range(0, len(keys), KEY_CHUNK):
        body = {"filter": {"or": [{"property": "Stable Job Key", "rich_text": {"equals": key}} for key in keys[start:start + KEY_CHUNK]]}, "page_size": 100}
        while True:
            data = client.call("POST", f"/data_sources/{client.source}/query", body)
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list) or any(not isinstance(page, dict) or not page.get("id") for page in results):
                raise NotionError("ledger_lookup_unreadable")
            for page in results:
                key = _text((page.get("properties") or {}).get("Stable Job Key"), "rich_text")
                if key in found:
                    found[key].append(page)
            if not data.get("has_more"):
                break
            body = {**body, "start_cursor": data.get("next_cursor")}
    return found


def check_listed(client, page_id, key, url, fit, pages):
    """check() for a page whose properties came from ledger_pages(): -> None when it matches, else a fixed reason."""
    mine = [page for page in pages if _bare(page.get("id")) == _bare(page_id)]
    if not mine:
        return "page_gone"                                                  # trashed, archived or moved out of the Ledger: the query does not return it
    if len(pages) > 1:
        return "key_ambiguous"
    why = properties_match(mine[0], key, url, fit)
    if why:
        return why
    children = client.call("GET", f"/blocks/{page_id}/children?page_size=3").get("results") or []
    first = _text((children[0].get("paragraph") if children else None) or {}, "rich_text")
    if not first.startswith("v7-jd:1") or len(children) < 2:
        return "no_description"
    return None


def run(client, live, limit=BATCH, job_ids=None):
    """Read back PUBLISHED jobs not yet verified (oldest first), or exactly `job_ids`."""
    counts = {"checked": 0, "verified": 0, "failed": 0, "why": {}}
    with store.connect() as connection, connection.cursor() as cursor:
        which = " AND j.id IN (" + ",".join(["%s"] * len(job_ids)) + ")" if job_ids else " AND j.verified_at IS NULL"
        cursor.execute("SELECT j.id, j.dedupe_key, j.final_apply_url, j.notion_page_id, f.score FROM v7_jobs j"
                       " LEFT JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status='PUBLISHED'"
                       " AND j.notion_page_id IS NOT NULL" + which + " ORDER BY j.updated_at LIMIT %s", (*(job_ids or ()), limit))
        rows = cursor.fetchall()
    unreadable = None
    try:
        listed = ledger_pages(client, sorted({row[1] for row in rows}))
    except NotionError as error:
        listed, unreadable = None, str(error).lower()
    counts["results"] = {}
    for job_id, key, url, page_id, fit in rows:
        counts["checked"] += 1
        try:
            why = unreadable if listed is None else check_listed(client, page_id, key, url, fit, listed.get(key, []))
        except NotionError as error:
            why = str(error).lower()
        if job_ids:
            counts["results"][str(job_id)] = why or "PASS"
        if why:
            counts["failed"] += 1
            counts["why"][why] = counts["why"].get(why, 0) + 1
            continue
        counts["verified"] += 1
        if live:
            with store.connect() as connection, connection.cursor() as cursor:
                cursor.execute("UPDATE v7_jobs SET verified_at=%s WHERE id=%s", (_now(), job_id))
    if not job_ids:
        counts.pop("results")
    return counts


# D212, one time: #336 (D211) marked a page verified from Notion's create response. Only manual proof run 39174107353 ran that code (its publish wrote 10 pages,
# 22:56-22:59 UTC on Oct 18; the 18:00 CDT tick ran 843859a, before #336). Those pages are found by verified_at set in the same write as their publish, inside that
# window, and read back from Notion. Nothing else is selected, cleared or written; any other count is refused before a write.
RECHECK_WINDOW = ("2026-10-18 22:56:00", "2026-10-18 22:59:30")
RECHECK_EXPECTED = 10


def create_verified(cursor):
    """The job ids whose verified_at came from the create response: inside RECHECK_WINDOW and within 2 s of the publish write's updated_at."""
    cursor.execute("SELECT id, updated_at, verified_at FROM v7_jobs WHERE status='PUBLISHED' AND notion_page_id IS NOT NULL AND verified_at BETWEEN %s AND %s",
                   RECHECK_WINDOW)
    return sorted(row[0] for row in cursor.fetchall() if row[1] and abs((row[2] - row[1]).total_seconds()) <= 2)


def recheck(limit, live, environ=os.environ):
    """`python -m lifeos.run readback-recheck [--live]`: read back exactly the pages D211 verified from a create response. Writes only their verified_at
    (cleared, then set again only when Notion's page matches). No page is created, no Fit, Gmail, audit, board or source work. Counts and job ids only."""
    client = Client(environ)
    counts = {"target": ledger.OK if not live else ledger.verify(client)}
    if counts["target"] != ledger.OK:
        return counts
    with store.connect() as connection, connection.cursor() as cursor:
        ids = create_verified(cursor)
    counts["found"] = len(ids)
    if len(ids) != RECHECK_EXPECTED:
        counts["refused"], counts["failed"] = "RECHECK_COUNT_MISMATCH", 1          # never a partial or a wider correction
        return counts
    if live:
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE v7_jobs SET verified_at=NULL WHERE id IN (" + ",".join(["%s"] * len(ids)) + ") AND verified_at BETWEEN %s AND %s",
                           (*ids, *RECHECK_WINDOW))
            counts["cleared"] = int(cursor.rowcount or 0)
    counts["readback"] = run(client, live, limit=RECHECK_EXPECTED, job_ids=ids)
    counts["failed"] = counts["readback"]["failed"]
    counts["calls"] = client.calls
    return counts
