"""Fit review (D97): one private Notion page that lists what happened to every job of one company or source, and why.

The logs of this public repository carry counts only, so a question like "which Revolut roles did we find and why were they not published" cannot
be answered from them. This stage writes the answer to a page in the Job Ledger (private) instead: each job with its title, place, Fit score and the
stored reason, grouped by what became of it. The page is not a job: it has no row in v7_jobs, so no other stage ever touches it, and Jim deletes it
when he has read it. The log carries counts only. Input: REVIEW_COMPANY (a substring of the company name or of the source id)."""
from datetime import datetime, timezone
import os

from lifeos.jobs import ledger, store
from lifeos.jobs.funnel import reason_code
from lifeos.platform.notion_client import Client, NotionError, rich_text

MAX_ROWS = 600


def _block(kind, text):
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(text[:1900])}}


def group_of(status, admission, reason):
    if status == "PUBLISHED":
        return "On the board"
    if admission == "REVIEW" and status in ("READY", "PUBLISHED"):
        return "Waiting as Review"
    if status == "EXCLUDED_FIT":
        return "Excluded by Fit or the lane policy: " + reason_code(reason)
    if status == "EXCLUDED_STALE":
        return "Too old"
    if status == "CLOSED":
        return "Closed by the employer"
    return f"Not finished: {status}" + (f" ({reason_code(reason)})" if reason else "")


def blocks_for(rows):
    groups = {}
    for title, location, status, source, score, admission, reason, stop, link, detail in rows:
        why = (reason or stop or "").strip()
        line = f"{title} | {(location or 'no place stated')[:260]} | " + (f"Fit {score}" if score is not None else "not scored") + (f" | {why}" if why else "") + f" | via {source or 'unknown'}" + (f" | {link[:140]}" if link and status in ("NEW", "HOLD", "RESOLVED") else "") + (f" | {detail[:420]}" if detail and score is not None else "")
        groups.setdefault(group_of(status, admission, why), []).append(line)
    blocks = []
    for name in sorted(groups, key=lambda g: (g != "On the board", g)):
        blocks.append(_block("heading_2", f"{name} ({len(groups[name])})"))
        blocks += [_block("bulleted_list_item", line) for line in groups[name]]
    return blocks, {name: len(lines) for name, lines in groups.items()}


def run(limit, live, environ=os.environ):
    needle = (environ.get("REVIEW_COMPANY") or "").strip()
    if len(needle) < 3:
        return {"error": "REVIEW_COMPANY_missing_or_short"}
    like = "%" + needle.replace("%", "").replace("_", "") + "%"
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT j.title, j.location_text, j.status, COALESCE(j.source, ''), f.score, f.admission, f.admission_reason, j.unresolved_reason, COALESCE(j.final_apply_url, j.source_url), CONCAT(COALESCE(f.line, ''), ' / ', COALESCE(f.buckets, ''))"
                           " FROM v7_jobs j LEFT JOIN v7_job_fit f ON f.job_id = j.id WHERE (j.company LIKE %s OR j.source LIKE %s)"
                           " AND j.status <> 'DUPLICATE' ORDER BY (j.status = 'PUBLISHED') DESC, f.score DESC LIMIT %s", (like, like, MAX_ROWS))
            rows = [tuple(r) for r in cursor.fetchall()]
    blocks, groups = blocks_for(rows)
    counts = {"matched": len(rows), "groups": groups, "written": False}
    if not live or not rows:
        return counts
    client = Client(environ)
    if ledger.verify(client) != ledger.OK:
        counts["target"] = "not_ok"
        return counts
    title = f"Fit Review - {needle} - {datetime.now(timezone.utc).date().isoformat()}"
    try:
        client.create({"Job": {"title": rich_text(title)}}, blocks)
    except NotionError:
        counts["failed"] = True
        return counts
    counts["written"] = True
    return counts
