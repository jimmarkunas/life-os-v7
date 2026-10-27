"""Ledger read-back: a PUBLISHED job is only finished once the page Notion holds matches what was sent. Checks the Apply URL,
the Stable Job Key, a title, the Fit being present when one was scored, and the v7-jd marker that opens the description body. Fixed reason codes only.

D212 (Jim, LIFE-76 10451): every page is verified by reading Notion. The properties come from ONE data-source query per KEY_CHUNK Stable Job Keys (an OR of
exact-key filters) instead of a GET per page; the body marker is still read per page. A page the query does not return is `page_gone`; two pages with one key is
`key_ambiguous`. A create response is never a read-back."""
from datetime import datetime, timezone

from lifeos.jobs import store
from lifeos.platform import ledger_body as body
from lifeos.jobs.identity import url_key
from lifeos.platform.notion_client import NotionError

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
    if not children or not body.is_marker(children[0]) or len(children) < 2:
        return "no_description"                                                      # either layout: the hidden marker (v7.jd.2) or the visible line (v7.jd.1)
    if children[1].get("type") == "column_list" and not (body.find(client, page_id, children) or {}).get("toggle"):
        return "no_description"                                                      # the two-column layout must hold its Overview column and the Full job description toggle
    return None


def _bare(page_id):
    return str(page_id or "").replace("-", "").lower()


def ledger_pages(client, keys):
    """{key: [page, ...]} as the Ledger holds them now: one data-source query per KEY_CHUNK keys, paged. An unreadable answer raises (never read as `no page`)."""
    found = {key: [] for key in keys}
    for start in range(0, len(keys), KEY_CHUNK):
        body = {"filter": {"or": [{"property": "Stable Job Key", "rich_text": {"equals": key}} for key in keys[start:start + KEY_CHUNK]]}, "page_size": 100}
        seen = set()
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
            cursor = data.get("next_cursor")
            if not cursor or cursor in seen:                                 # a missing or repeated cursor would skip or loop pages: refused, never read as "no page"
                raise NotionError("ledger_lookup_unreadable")
            seen.add(cursor)
            body = {**body, "start_cursor": cursor}
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
    if not children or not body.is_marker(children[0]) or len(children) < 2:
        return "no_description"                                                      # either layout: the hidden marker (v7.jd.2) or the visible line (v7.jd.1)
    if children[1].get("type") == "column_list" and not (body.find(client, page_id, children) or {}).get("toggle"):
        return "no_description"                                                      # the two-column layout must hold its Overview column and the Full job description toggle
    return None


def run(client, live, limit=BATCH):
    """Read back PUBLISHED jobs not yet verified, oldest first."""
    counts = {"checked": 0, "verified": 0, "failed": 0, "why": {}}
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT j.id, j.dedupe_key, j.final_apply_url, j.notion_page_id, f.score FROM v7_jobs j"
                       " LEFT JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status='PUBLISHED' AND j.verified_at IS NULL"
                       " AND j.notion_page_id IS NOT NULL ORDER BY j.updated_at LIMIT %s", (limit,))
        rows = cursor.fetchall()
    unreadable = None
    try:
        listed = ledger_pages(client, sorted({row[1] for row in rows}))
    except NotionError as error:
        listed, unreadable = None, str(error).lower()
    for job_id, key, url, page_id, fit in rows:
        counts["checked"] += 1
        try:
            why = unreadable if listed is None else check_listed(client, page_id, key, url, fit, listed.get(key, []))
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

