"""Fit sync: put the Fit on pages that were published BEFORE (or without) a score. Publish only creates; this updates a published page's machine-owned
Fit fields when its stored score is newer than the last sync: LIFE OS Fit, Why It Fits, Fit Authority and, with V7_FIT_GATE, the lane-policy fields
(Admission Status, Review Reason, Work Mode, Visible Lane, Eligible Lanes). Never touches human state (Applied, Applied On, Saturn Decision) or the title,
and runs only after the Job Ledger target check. Counts only."""
from datetime import datetime, timezone

from lifeos.jobs import lanes, store
from lifeos.platform.notion_client import NotionError, rich_text

PICK = ("SELECT j.id, j.notion_page_id, f.score, f.line, f.admission, f.admission_reason, f.work_mode, f.lane, f.eligible FROM v7_jobs j"
        " JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status='PUBLISHED' AND j.notion_page_id IS NOT NULL AND f.score IS NOT NULL"
        " AND (j.fit_synced_at IS NULL OR j.fit_synced_at < f.scored_at) ORDER BY f.scored_at LIMIT %s")
VISIBLE_LANE_LABEL = {"Scale-Up": "Scale-up"}          # same spelling rule as publish (the Ledger's existing option)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def properties(score, line, admission, reason, work_mode, lane, eligible, gated):
    props = {"LIFE OS Fit": {"number": score}, "Why It Fits": {"rich_text": rich_text(line or "")},
             "Fit Authority": {"select": {"name": "Authoritative"}}}
    if gated and admission in lanes.ADMISSION_LABEL:
        props["Admission Status"] = {"select": {"name": lanes.ADMISSION_LABEL[admission]}}
        props["Review Reason"] = {"rich_text": rich_text(reason if admission == lanes.REVIEW and reason else "")}
        if lane:
            props["Visible Lane"] = {"select": {"name": VISIBLE_LANE_LABEL.get(lane, lane)}}
        names = [n for n in (eligible or "").split(",") if n]
        if names:
            props["Eligible Lanes"] = {"multi_select": [{"name": n} for n in names]}
    if work_mode in ("remote", "hybrid", "onsite", "unknown"):
        props["Work Mode"] = {"select": {"name": work_mode.capitalize()}}
    return props


def run(client, live, gated=False, limit=100):
    counts = {"due": 0, "synced": 0, "failed": 0}
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute(PICK, (limit,))
        rows = cursor.fetchall()
    counts["due"] = len(rows)
    if not live:
        return counts
    for job_id, page_id, score, line, admission, reason, work_mode, lane, eligible in rows:
        try:
            client.call("PATCH", f"/pages/{page_id}", {"properties": properties(score, line, admission, reason, work_mode, lane, eligible, gated)})
        except NotionError:
            counts["failed"] += 1
            if counts["failed"] >= 3:
                break
            continue
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE v7_jobs SET fit_synced_at=%s WHERE id=%s", (_now(), job_id))
        counts["synced"] += 1
    return counts
