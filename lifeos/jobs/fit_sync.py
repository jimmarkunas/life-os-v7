"""Fit sync: put the Fit on pages that were published BEFORE (or without) a score. Publish only creates; this updates a published page's machine-owned
Fit fields when its stored score is newer than the last sync: LIFE OS Fit, Why It Fits, Fit Authority and, with V7_FIT_GATE, the lane-policy fields
(Admission Status, Review Reason, Work Mode, Visible Lane, Eligible Lanes). Never touches human state (Applied, Applied On, Saturn Decision) or the title,
and runs only after the Job Ledger target check. Counts only."""
from datetime import datetime, timezone

from lifeos.jobs import lanes, meta, store
from lifeos.platform.notion_client import NotionError, rich_text

PICK = ("SELECT j.id, j.notion_page_id, f.score, f.line, f.admission, f.admission_reason, f.work_mode, f.lane, f.eligible,"
        " j.lane, j.location_text, j.salary_text, j.route_evidence, d.full_text, j.title FROM v7_jobs j"
        " JOIN v7_job_fit f ON f.job_id=j.id LEFT JOIN v7_job_descriptions d ON d.job_id=j.id"
        " WHERE j.status='PUBLISHED' AND j.notion_page_id IS NOT NULL AND f.score IS NOT NULL"
        " AND (j.fit_synced_at IS NULL OR j.fit_synced_at < f.scored_at OR j.meta_v IS NULL OR j.meta_v < %s) ORDER BY f.scored_at LIMIT %s")
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


def _plain(page_id):
    return str(page_id or "").replace("-", "").lower()


def _role_text(page):
    return "".join(part.get("plain_text", "") for part in ((page.get("properties") or {}).get("Role") or {}).get("rich_text") or []).strip()


def backfill_roles(client, live, limit=ROLE_BATCH):
    """Fill an EMPTY Role on a published page from the job's own title, whether or not the job has a Fit score. Selected by what Notion actually holds (an empty Role), so no stored flag stands in for a write:
    once a page reads back with its Role it is never selected again. Writes the Role property only; Fit, Fit Authority, admission, lanes, human state and an existing Role are never touched."""
    counts = {"role_due": 0, "role_filled": 0, "role_failed": 0}
    empty, cursor = {}, None
    for _ in range(ROLE_PAGES):
        body = {"page_size": 100, "filter": {"property": "Role", "rich_text": {"is_empty": True}}, **({"start_cursor": cursor} if cursor else {})}
        try:
            found = client.call("POST", f"/data_sources/{client.source}/query", body)
        except NotionError:
            counts["role_failed"] += 1                                       # an unreadable Ledger leaves every page as it was; the next run asks again
            return counts
        results = found.get("results") if isinstance(found, dict) else None
        if not isinstance(results, list) or not all(isinstance(page, dict) and page.get("id") for page in results):
            counts["role_failed"] += 1                                       # an answer that is not a page list is never read as "nothing to fill"
            return counts
        empty.update({_plain(page["id"]): page["id"] for page in results})
        cursor = found.get("next_cursor") if found.get("has_more") else None
        if not cursor:
            break
    if not empty:
        return counts
    with store.connect() as connection, connection.cursor() as cursor_:
        cursor_.execute("SELECT notion_page_id, title FROM v7_jobs WHERE status='PUBLISHED' AND notion_page_id IS NOT NULL AND title IS NOT NULL AND title <> ''")
        titles = {_plain(page_id): title.strip() for page_id, title in cursor_.fetchall() if _plain(page_id) in empty and title.strip()}
    counts["role_due"] = len(titles)
    if not live:
        return counts
    for key, title in sorted(titles.items())[:limit]:
        page_id = empty[key]
        try:
            client.call_once("PATCH", f"/pages/{page_id}", {"properties": {"Role": {"rich_text": rich_text(title)}}})      # one write, never retried blind: the read-back decides
            ok = _role_text(client.call("GET", f"/pages/{page_id}")) == title
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
        except NotionError:
            counts["failed"] += 1
            if counts["failed"] >= 3:
                break
            continue
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE v7_jobs SET fit_synced_at=%s, meta_v=%s WHERE id=%s", (_now(), meta.META_VERSION, job_id))
        counts["synced"] += 1
    counts.update(backfill_roles(client, live))                              # after the main pass, so a scored page's Role arrives with its Fit and this finds only what is left
    return counts
