"""Publish READY jobs to the Notion Job Ledger (free official API, <= 2.5 req/s, counts-only logs).

Hostinger is the dedupe brain: each job is created once and its page id stored. The ONE cold-start read of the Ledger
(to avoid duplicating rows V2 already wrote) is remembered in v7_ledger_urls and never repeated.
Properties written (all exist in the Ledger): Job, Company, Apply URL, Source Provider, Source Types, Stable Job Key,
First Surfaced, Posting Date, Freshness Status, Liveness, Visible Lane, Admission Status. LIFE OS Fit / Why It Fits are written when the fit stage scored the job; with V7_FIT_GATE=true only jobs the lane policy admits (or sends to Review) publish.
The job description goes in the page BODY (contract v7.jd.1, docs/PLAN.md).
"""
from datetime import datetime, timezone
import os

from lifeos.jobs import lanes, store
from lifeos.jobs.identity import url_key
from lifeos.platform import limits
from lifeos.platform.notion_client import Client, NotionError, rich_text

SECTIONS = (("summary", "Summary"), ("responsibilities", "Responsibilities"), ("requirements", "Requirements"),
            ("qualifications", "Qualifications"))


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _block(kind, content):
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(content)}}


def body_blocks(key, description):
    """Contract v7.jd.1: marker paragraph, then heading_2 + bullets/paragraphs per non-empty section."""
    blocks = [_block("paragraph", f"v7-jd:1 | key={key}")]
    for field, heading in SECTIONS:
        text = (description.get(field) or "").strip()
        if not text:
            continue
        blocks.append(_block("heading_2", heading))
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("•"):
                blocks.append(_block("bulleted_list_item", line.lstrip("• ").strip() or "-"))
            else:
                blocks.append(_block("paragraph", line))
    return blocks


def properties(row):
    """row: dict with title, company, url, provider (or source), lane, key, first_seen (date), posted (date|None)."""
    provider = row.get("provider") or row["source"]
    props = {
        "Job": {"title": rich_text(row["title"] or "Untitled")},
        "Company": {"rich_text": rich_text(row["company"] or "")},
        "Apply URL": {"url": row["url"]},
        "Source Provider": {"rich_text": rich_text(provider)},
        "Source Types": {"multi_select": [{"name": provider}]},
        "Stable Job Key": {"rich_text": rich_text(row["key"])},
        "First Surfaced": {"date": {"start": row["first_seen"].isoformat()}},
        "Freshness Status": {"select": {"name": "Fresh"}},
        "Liveness": {"select": {"name": "Live"}},
        "Visible Lane": {"select": {"name": lanes.lane_for(row["lane"])}},
        "Eligible Lanes": {"multi_select": [{"name": lanes.lane_for(row["lane"])}]},
        "Admission Status": {"select": {"name": lanes.ADMISSION_LABEL.get(row.get("admission"), "Passed / Review")}},
    }
    if row.get("admission") == lanes.REVIEW and row.get("admission_reason"):
        props["Review Reason"] = {"rich_text": rich_text(row["admission_reason"])}
    if row.get("work_mode") in ("remote", "hybrid", "onsite", "unknown"):
        props["Work Mode"] = {"select": {"name": row["work_mode"].capitalize()}}
    if row.get("salary"):
        props["Compensation"] = {"rich_text": rich_text(row["salary"])}
    if row.get("posted"):
        props["Posting Date"] = {"date": {"start": row["posted"].isoformat()}}
    if row.get("fit") is not None:                                    # Phase 2: the deterministic fit score and its line
        props["LIFE OS Fit"] = {"number": row["fit"]}
        props["Why It Fits"] = {"rich_text": rich_text(row.get("fit_line") or "")}
        props["Fit Authority"] = {"select": {"name": "Authoritative"}}
    return props


def ledger_urls(client):
    """Every Apply URL already in the Ledger (paged, 100 per request)."""
    urls, cursor = set(), None
    while True:
        body = {"page_size": limits.NOTION_PAGE_SIZE}
        if cursor:
            body["start_cursor"] = cursor
        data = client.call("POST", f"/data_sources/{client.source}/query", body)
        for page in data.get("results", []):
            value = ((page.get("properties") or {}).get("Apply URL") or {}).get("url")
            if value:
                urls.add(url_key(value))
        if not data.get("has_more"):
            return urls
        cursor = data.get("next_cursor")


def _seeded(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM v7_ledger_urls WHERE source='seed'")
        return cursor.fetchone()[0] > 0


def _store_seed(connection, urls):
    """Remember every URL already in the Ledger (plus a marker row so the read is never repeated)."""
    rows = [("0" * 64, "seed", _now())] + [(key, "seed", _now()) for key in urls]
    with connection.cursor() as cursor:
        for start in range(0, len(rows), 500):
            cursor.executemany("INSERT IGNORE INTO v7_ledger_urls (url_hash, source, seen_at) VALUES (%s,%s,%s)",
                               rows[start:start + 500])


def _pick(connection, limit, gated=False):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT j.id, j.dedupe_key, j.title, j.company, j.final_apply_url, j.source, j.provider, j.lane, j.first_seen, j.posted_date,"
            " d.summary, d.responsibilities, d.requirements, d.qualifications, d.full_text, f.score, f.line, f.admission, f.admission_reason, f.work_mode, j.salary_text"
            " FROM v7_jobs j JOIN v7_job_descriptions d ON d.job_id=j.id LEFT JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status='READY'"
            " AND j.notion_page_id IS NULL" + (" AND f.admission IN ('ADMIT', 'REVIEW')" if gated else "") +
            " ORDER BY j.first_seen LIMIT %s", (min(limit, limits.NOTION_PER_RUN),))
        rows = cursor.fetchall()
        known = set()
        for row in rows:
            cursor.execute("SELECT 1 FROM v7_ledger_urls WHERE url_hash=%s", (url_key(row[4]),))
            if cursor.fetchone():
                known.add(row[0])
    return rows, known


def run(limit, live, environ=os.environ):
    gated = environ.get("V7_FIT_GATE") == "true"       # shadow mode: the lane decision is stored but not shown as Admission Status
    """No database connection is ever held across slow work (Notion calls): read, close, call, reconnect, write."""
    counts = {"picked": 0, "created": 0, "duplicate": 0, "failed": 0, "seeded": 0, "calls": 0}
    client = Client(environ)
    with store.connect() as connection:
        store.ensure_schema(connection)
        need_seed = live and not _seeded(connection)
    if need_seed:
        urls = ledger_urls(client)                                   # slow: no connection open
        with store.connect() as connection:
            _store_seed(connection, urls)
        counts["seeded"] = len(urls)
    with store.connect() as connection:
        rows, known = _pick(connection, limit, gated)
    counts["picked"] = len(rows)
    for (job_id, key, title, company, url, source, provider, lane, first_seen, posted, summary, resp, req, qual, full, fit, fit_line, admission, admission_reason, work_mode, salary) in rows:
        if job_id in known:
            counts["duplicate"] += 1
            if live:
                with store.connect() as connection, connection.cursor() as cursor:
                    cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', unresolved_reason='in_ledger', "
                                   "updated_at=%s WHERE id=%s", (_now(), job_id))
            continue
        if not live:
            counts["created"] += 1
            continue
        desc = {"summary": summary, "responsibilities": resp, "requirements": req, "qualifications": qual}
        if not any((summary, resp, req, qual)):
            desc["summary"] = (full or "")[:6000]
        row = {"title": title, "company": company, "url": url, "source": source, "provider": provider, "lane": lane,
               "key": key,
               "first_seen": first_seen.date() if hasattr(first_seen, "date") else first_seen, "posted": posted,
               "fit": fit, "fit_line": fit_line,
               "admission": admission if gated else None, "admission_reason": admission_reason if gated else None,
               "work_mode": work_mode, "salary": salary}
        try:
            page_id = client.create(properties(row), body_blocks(key, desc))   # slow: no connection open
        except NotionError:
            counts["failed"] += 1
            if counts["failed"] >= 3:          # systemic (auth/schema/limit): stop instead of hammering
                break
            continue
        with store.connect() as connection, connection.cursor() as cursor:  # record at once: a crash cannot duplicate
            cursor.execute("UPDATE v7_jobs SET status='PUBLISHED', notion_page_id=%s, updated_at=%s WHERE id=%s",
                           (page_id, _now(), job_id))
            cursor.execute("INSERT IGNORE INTO v7_ledger_urls (url_hash, source, seen_at) VALUES (%s,'v7',%s)",
                           (url_key(url), _now()))
        counts["created"] += 1
    counts["calls"] = client.calls
    return counts
