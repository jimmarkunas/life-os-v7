"""Fit sync: put the Fit on pages that were published BEFORE (or without) a score. Publish only creates; this updates a published page's machine-owned
Fit fields when its stored score is newer than the last sync: LIFE OS Fit, Why It Fits, Fit Authority and, with V7_FIT_GATE, the lane-policy fields
(Admission Status, Review Reason, Work Mode, Visible Lane, Eligible Lanes). Never touches human state (Applied, Applied On, Saturn Decision) or the title,
and runs only after the Job Ledger target check. Counts only."""
from datetime import datetime, timezone

from lifeos.jobs import lanes, ledger, meta, store
from lifeos.platform.notion_client import NotionError, rich_text

PICK = ("SELECT j.id, j.notion_page_id, f.score, f.line, f.admission, f.admission_reason, f.work_mode, f.lane, f.eligible,"
        " j.lane, j.location_text, j.salary_text, j.route_evidence, d.full_text, j.title FROM v7_jobs j"
        " JOIN v7_job_fit f ON f.job_id=j.id LEFT JOIN v7_job_descriptions d ON d.job_id=j.id"
        " WHERE j.status='PUBLISHED' AND j.notion_page_id IS NOT NULL AND f.score IS NOT NULL"
        " AND (j.fit_synced_at IS NULL OR j.fit_synced_at < f.scored_at OR j.meta_v IS NULL OR j.meta_v < %s) ORDER BY f.scored_at DESC, j.id LIMIT %s")
# LIFE-212: newest score first. A lane-policy re-score marks every published page due at once; oldest-first then left the pages the Cloud Fit batch had just
# promoted at the back, and its check failed the run (LEDGER_SYNC_INCOMPLETE) until the whole wave had synced. The wave is finite, so nothing waits forever.
ROLE_PAGES, ROLE_BATCH = 5, 25                         # Role-only backfill: at most 5 query pages (500 empty Roles) read and 25 pages written per run
VISIBLE_LANE_LABEL = {"Scale-Up": "Scale-up"}          # same spelling rule as publish (the Ledger's existing option)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def properties(score, line, admission, reason, work_mode, lane, eligible, gated, row_meta=None, title=None):
    props = {"LIFE OS Fit": {"number": score}, "Why It Fits": {"rich_text": rich_text(line or "")},
             "Fit Authority": {"select": {"name": "Authoritative"}}}
    if title:
        props["Role"] = {"rich_text": rich_text(title)}                     # the Ledger's Role column: the job's own title (it was never filled before META_VERSION 2)
    if gated and admission in lanes.ADMISSION_LABEL:
        props["Admission Status"] = {"select": {"name": lanes.ADMISSION_LABEL[admission]}}
        props["Review Reason"] = {"rich_text": rich_text(reason if admission == lanes.REVIEW and reason else "")}
        if lane:
            props["Visible Lane"] = {"select": {"name": VISIBLE_LANE_LABEL.get(lane, lane)}}
        names = [n for n in (eligible or "").split(",") if n] or ([lanes.lane_for(row_meta["lane"])] if row_meta else [])
        if names:
            props["Eligible Lanes"] = {"multi_select": [{"name": n} for n in names]}
    if work_mode in ("remote", "hybrid", "onsite", "unknown"):
        props["Work Mode"] = {"select": {"name": work_mode.capitalize()}}
    if row_meta:                                                       # Location / Work Mode, Market, Visa Route, Compensation
        props.update(meta.properties(row_meta["location"], row_meta["salary"], row_meta["text"], row_meta["route"], eligible, lane or row_meta["lane"]))
    return props


def _mark_synced(job_id):
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE v7_jobs SET fit_synced_at=%s, meta_v=%s WHERE id=%s", (_now(), meta.META_VERSION, job_id))


def _plain(page_id):
    return str(page_id or "").replace("-", "").lower()


def _text(page, name):
    return "".join(part.get("plain_text", "") for part in ((page.get("properties") or {}).get(name) or {}).get("rich_text") or []).strip()


def _empty_roles(client, counts):
    """{page id: Stable Job Key} of every Ledger page whose Role is empty, or None when the Ledger could not be read in full (counted, so an incomplete read is never a zero)."""
    found, cursor = {}, None
    for _ in range(ROLE_PAGES):
        body = {"page_size": 100, "filter": {"property": "Role", "rich_text": {"is_empty": True}}, **({"start_cursor": cursor} if cursor else {})}
        try:
            answer = client.call("POST", f"/data_sources/{client.source}/query", body)
        except NotionError:
            counts["role_failed"] += 1                                         # an unreadable Ledger leaves every page as it was; the next run asks again
            return None
        results = answer.get("results") if isinstance(answer, dict) else None
        if not isinstance(results, list) or not all(isinstance(page, dict) and page.get("id") for page in results):
            counts["role_failed"] += 1                                         # an answer that is not a page list is never read as "nothing to fill"
            return None
        found.update({page["id"]: _text(page, "Stable Job Key") for page in results})
        cursor = answer.get("next_cursor") if answer.get("has_more") else None
        if not cursor:
            return found
    counts["role_incomplete"] = 1                                              # more empty pages than this run reads: say so, never report the part read as the whole
    return found


def backfill_roles(client, live, limit=ROLE_BATCH):
    """Fill an EMPTY Role on a Ledger page from the job's own title, whether or not the job has a Fit score or a stored page id. The pages are selected by what Notion actually holds (an empty Role), then matched to
    v7_jobs by the page's id or its Stable Job Key, so no stored flag stands in for a write and a row whose page id was cleared still finds its page. Once a page reads back with its Role it is never selected again.
    Writes the Role property only; Fit, Fit Authority, admission, lanes, human state and an existing Role are never touched. Counts say why an empty page was left: no job row, no title, or not a job page."""
    counts = {"role_empty": 0, "role_due": 0, "role_filled": 0, "role_failed": 0, "role_no_row": 0, "role_no_title": 0, "role_not_job": 0}
    empty = _empty_roles(client, counts)
    if not empty:
        return counts
    counts["role_empty"] = len(empty)
    pages = {_plain(page_id): page_id for page_id in empty}
    keys = {key: page_id for page_id, key in empty.items() if key}
    counts["role_not_job"] = len([1 for key in empty.values() if not key])
    with store.connect() as connection, connection.cursor() as cursor_:
        marks = lambda n: ", ".join(["%s"] * n)                               # noqa: E731
        ids = list(empty) + [_plain(page_id) for page_id in empty]
        cursor_.execute(f"SELECT notion_page_id, dedupe_key, title FROM v7_jobs WHERE notion_page_id IN ({marks(len(ids))}) OR dedupe_key IN ({marks(len(keys) or 1)})", (*ids, *(keys or ("",))))
        rows = cursor_.fetchall()
    titles, seen = {}, set()
    for page_id, key, title in rows:
        target = pages.get(_plain(page_id)) or keys.get(key)
        if not target:
            continue
        seen.add(target)
        if (title or "").strip():
            titles[target] = title.strip()
        else:
            counts["role_no_title"] += 1
    counts["role_no_row"] = len([1 for page_id, key in empty.items() if key and page_id not in seen])
    counts["role_due"] = len(titles)
    if not live:
        return counts
    for page_id, title in sorted(titles.items())[:limit]:
        try:
            client.call_once("PATCH", f"/pages/{page_id}", {"properties": {"Role": {"rich_text": rich_text(title)}}})      # one write, never retried blind: the read-back decides
            ok = _text(client.call("GET", f"/pages/{page_id}"), "Role") == title
        except NotionError:
            ok = False
        counts["role_filled" if ok else "role_failed"] += 1
        if counts["role_failed"] >= 3:
            break
    return counts


def run(client, live, gated=False, limit=150):
    counts = {"due": 0, "synced": 0, "failed": 0}
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute(PICK, (meta.META_VERSION, limit))
        rows = cursor.fetchall()
    counts["due"] = len(rows)
    if not live:
        return counts
    for job_id, page_id, score, line, admission, reason, work_mode, lane, eligible, job_lane, location, salary, route, text, title in rows:
        row_meta = {"lane": job_lane, "location": location, "salary": salary, "route": route, "text": text}
        try:
            client.call("PATCH", f"/pages/{page_id}", {"properties": properties(score, line, admission, reason, work_mode, lane, eligible, gated, row_meta, title)})
        except NotionError as error:
            state = ledger.page_state(client, page_id)
            if state == "gone":                                  # the page is in Notion's trash (deleted by hand, or trashed before the store was updated): it refuses every write and will never take this one,
                counts["page_gone"] = counts.get("page_gone", 0) + 1       # so it is not a failure and is not asked again until the job is re-scored. The audit's trash pass clears the row.
                _mark_synced(job_id)
                continue
            counts["failed"] += 1
            counts["failed_why"] = f"{error} {getattr(error, 'detail', '')}".strip()[:80]            # a fixed code (and Notion's own error code), as publish reports it: the failure is diagnosable from the log
            counts["failed_page"] = state                        # open | unreadable: the page is there (or cannot be read), so the refusal is about the request, not the page
            if counts["failed"] >= 3:
                break
            continue
        _mark_synced(job_id)
        counts["synced"] += 1
    counts.update(backfill_roles(client, live))                              # after the main pass, so a scored page's Role arrives with its Fit and this finds only what is left
    return counts
