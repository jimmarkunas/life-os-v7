"""Publish READY jobs to the Notion Job Ledger (free official API, <= 2.5 req/s, counts-only logs).

Hostinger is the dedupe brain: each job is created once and its page id stored. The ONE cold-start read of the Ledger
(to avoid duplicating rows V2 already wrote) is remembered in v7_ledger_urls and never repeated.
Properties written (all exist in the Ledger): Job, Company, Apply URL, Source Provider, Source Types, Stable Job Key,
First Surfaced, Posting Date, Freshness Status, Liveness, Visible Lane, Admission Status. LIFE OS Fit / Why It Fits are written when the fit stage scored the job; with V7_FIT_GATE=true only jobs the lane policy admits (or sends to Review) publish.
The job description goes in the page BODY (contract v7.jd.1, docs/PLAN.md).
"""
from datetime import datetime, timezone
import os

from lifeos.jobs import meta, lanes, ledger, store
from lifeos.jobs.identity import norm, url_key, vacancy_id
from lifeos.platform import limits
from lifeos.platform.notion_client import Client, NotionError, rich_text

SECTIONS = (("summary", "Summary"), ("responsibilities", "Responsibilities"), ("requirements", "Requirements"),
            ("qualifications", "Qualifications"))


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _block(kind, content):
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(content)}}


def same_opening(cursor, job_id, company, title, url=None):
    """D90: the id of a PUBLISHED job with the same company and title (three words or more, so a generic title is never merged), else None.
    One opening posted under several locations or links is one page. LIFE-59: two jobs whose links name different ATS vacancies
    (identity.vacancy_id) are two openings, whatever their company and title."""
    if len(norm(title).split()) < 3 or not norm(company):
        return None
    cursor.execute("SELECT id, final_apply_url FROM v7_jobs WHERE status='PUBLISHED' AND id<>%s AND LOWER(TRIM(company))=LOWER(TRIM(%s))"
                   " AND LOWER(TRIM(title))=LOWER(TRIM(%s)) ORDER BY id", (job_id, company, title))
    mine = vacancy_id(url)
    return next((row[0] for row in cursor.fetchall() if not (mine and vacancy_id(row[1]) and vacancy_id(row[1]) != mine)), None)


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


VISIBLE_LANE_LABEL = {"Scale-Up": "Scale-up"}        # the Ledger's Visible Lane option is spelled "Scale-up" (Eligible Lanes uses "Scale-Up")


def properties(row):
    """row: dict with title, company, url, provider (or source), lane, key, first_seen (date), posted (date|None)."""
    provider = row.get("provider") or row["source"]
    props = {
        "Job": {"title": rich_text(row["title"] or "Untitled")},
        "Company": {"rich_text": rich_text(row["company"] or "")},
        "Apply URL": {"url": row["url"]},
        "Source Provider": {"rich_text": rich_text(provider)},
        "Source Types": {"multi_select": [{"name": provider}] + [{"name": flag} for kind, flag in (("easy_apply", "Easy Apply"), ("aggregator", "Aggregator Link")) if row.get("apply_kind") == kind]},
        "Stable Job Key": {"rich_text": rich_text(row["key"])},
        "First Surfaced": {"date": {"start": row["first_seen"].isoformat()}},
        "Freshness Status": {"select": {"name": "Fresh"}},
        "Liveness": {"select": {"name": "Live"}},
        "Visible Lane": {"select": {"name": VISIBLE_LANE_LABEL.get(lanes.lane_for(row["lane"]), lanes.lane_for(row["lane"]))}},
        "Eligible Lanes": {"multi_select": [{"name": n} for n in (row.get("eligible") or [lanes.lane_for(row["lane"])])]},
        "Admission Status": {"select": {"name": lanes.ADMISSION_LABEL.get(row.get("admission"), "Passed / Review")}},
    }
    if row.get("admission") == lanes.REVIEW and row.get("admission_reason"):
        props["Review Reason"] = {"rich_text": rich_text(row["admission_reason"])}
    if row.get("work_mode") in ("remote", "hybrid", "onsite", "unknown"):
        props["Work Mode"] = {"select": {"name": row["work_mode"].capitalize()}}
    props.update(meta.properties(row.get("location"), row.get("salary"), row.get("text"), row.get("route"),
                                 ",".join(row.get("eligible") or []), lanes.lane_for(row["lane"])))
    if row.get("posted"):
        props["Posting Date"] = {"date": {"start": row["posted"].isoformat()}}
    if row.get("fit") is not None:                                    # Phase 2: the deterministic fit score and its line
        props["LIFE OS Fit"] = {"number": row["fit"]}
        props["Why It Fits"] = {"rich_text": rich_text(row.get("fit_line") or "")}
        props["Fit Authority"] = {"select": {"name": "Authoritative"}}
    return props


def existing_page(client, key):
    """The Ledger page that already holds this Stable Job Key, or None: a create Notion accepted but the database never recorded is adopted on the next run, never repeated.
    Two matches or an unreadable answer is refused (a fixed NotionError), never resolved by picking one."""
    found = client.call("POST", f"/data_sources/{client.source}/query", {"filter": {"property": "Stable Job Key", "rich_text": {"equals": key}}, "page_size": 2}).get("results")
    if found is None or not isinstance(found, list) or len(found) > 1 or any(not isinstance(page, dict) or not page.get("id") for page in found):
        raise NotionError("ledger_key_ambiguous" if isinstance(found, list) and len(found) > 1 else "ledger_lookup_unreadable")
    return found[0]["id"] if found else None


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


SEED_MARKER = "0" * 63 + "3"        # D112: the Ledger read is repeated once per marker: 2 after Jim's 9/30 purge; 3 when url_key began naming the ATS vacancy (LIFE-59)


def _seeded(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM v7_ledger_urls WHERE source='seed' AND url_hash=%s", (SEED_MARKER,))
        return cursor.fetchone()[0] > 0


def _store_seed(connection, urls):
    """Replace the remembered Ledger URLs with the ones in the Ledger now (plus a marker row so the read is never repeated), then release the jobs the old list was
    holding back: a role V1 or V2 once published and a purge archived is not in the Ledger any more, so it is no longer a duplicate of it."""
    now = _now()
    rows = [("0" * 64, "seed", now), (SEED_MARKER, "seed", now)] + [(key, "seed", now) for key in urls]
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM v7_ledger_urls WHERE source='seed'")
        for start in range(0, len(rows), 500):
            cursor.executemany("INSERT IGNORE INTO v7_ledger_urls (url_hash, source, seen_at) VALUES (%s,%s,%s)",
                               rows[start:start + 500])
    return release_in_ledger(connection)


def release_in_ledger(connection):
    """Jobs set aside as `in_ledger` whose URL is not in the remembered Ledger URLs any more go back to READY. Returns how many."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, final_apply_url FROM v7_jobs WHERE status='DUPLICATE' AND unresolved_reason='in_ledger' AND notion_page_id IS NULL AND final_apply_url IS NOT NULL")
        held = cursor.fetchall()
        cursor.execute("SELECT url_hash FROM v7_ledger_urls")
        known = {row[0] for row in cursor.fetchall()}
        free = [row[0] for row in held if url_key(row[1]) not in known]
        for job_id in free:
            cursor.execute("UPDATE v7_jobs SET status='READY', unresolved_reason='released_ledger', updated_at=%s WHERE id=%s AND status='DUPLICATE'", (_now(), job_id))
    return len(free)


FAIR_SHARE = 4          # D186: one slot in four goes to the oldest waiting jobs, whatever they score, so a valid job is never starved by newer, stronger ones


def order(rows, n, hour=0):
    """D186: which READY jobs a run publishes, as ids. rows: (id, admission, fit score, posted date, first surfaced). Strongest first: admitted before Review (Review is
    never raised by missing evidence: no score or no date sorts last), then the higher Fit, then the newer posting; ties by first surfaced, then id. The last n // FAIR_SHARE
    places are the oldest of the rest by first surfaced, so every valid job is reached in finite time. A run of fewer than four places has no whole quarter, so it reserves its one place for the oldest only when the clock hour is a multiple of four (hourly runs: one in four).
    The set of jobs eligible to publish is decided by the caller, not here."""
    ranked = sorted(rows, key=lambda r: ({"ADMIT": 0, "REVIEW": 1}.get(r[1], 2), -(r[2] if r[2] is not None else -1), -(r[3] or r[4].date()).toordinal(), r[4], r[0]))
    fair = n // FAIR_SHARE or int(n > 0 and hour % FAIR_SHARE == 0)
    first = ranked[:n - fair]
    rest = sorted(ranked[len(first):], key=lambda r: (r[4], r[0]))
    return [r[0] for r in first + rest[:n - len(first)]]


def _pick(connection, limit, gated=False, hour=None):
    base = (" FROM v7_jobs j JOIN v7_job_descriptions d ON d.job_id=j.id LEFT JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status='READY'"
            " AND j.notion_page_id IS NULL" + (" AND f.admission IN ('ADMIT', 'REVIEW')" if gated else ""))
    with connection.cursor() as cursor:
        cursor.execute("SELECT j.id, f.admission, f.score, j.posted_date, j.first_seen" + base)          # ordering keys only; the long texts are read for the chosen jobs
        ids = order([tuple(r) for r in cursor.fetchall()], min(limit, limits.NOTION_PER_RUN), _now().hour if hour is None else hour)
        if not ids:
            return [], set()
        cursor.execute(
            "SELECT j.id, j.dedupe_key, j.title, j.company, j.final_apply_url, j.source, j.provider, j.lane, j.first_seen, j.posted_date,"
            " d.summary, d.responsibilities, d.requirements, d.qualifications, d.full_text, f.score, f.line, f.admission, f.admission_reason, f.work_mode, j.salary_text, j.apply_kind, f.lane, f.eligible, j.location_text, j.route_evidence"
            + base + " AND j.id IN (" + ",".join(["%s"] * len(ids)) + ")", tuple(ids))
        by_id = {row[0]: row for row in cursor.fetchall()}
        rows = [by_id[i] for i in ids if i in by_id]
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
    counts["target"] = ledger.OK if not live else ledger.verify(client)           # a live run never writes to a data source that is not the Job Ledger
    if counts["target"] != ledger.OK:
        return counts
    with store.connect() as connection:
        store.ensure_schema(connection)
        need_seed = live and not _seeded(connection)
    if need_seed:
        urls = ledger_urls(client)                                   # slow: no connection open
        with store.connect() as connection:
            counts["released"] = _store_seed(connection, urls)
        counts["seeded"] = len(urls)
    with store.connect() as connection:
        rows, known = _pick(connection, limit, gated)
    counts["picked"] = len(rows)
    for (job_id, key, title, company, url, source, provider, lane, first_seen, posted, summary, resp, req, qual, full, fit, fit_line, admission, admission_reason, work_mode, salary, apply_kind, fit_lane, eligible, location, route) in rows:
        if job_id in known:
            counts["duplicate"] += 1
            if live:
                with store.connect() as connection, connection.cursor() as cursor:
                    cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', unresolved_reason='in_ledger', "
                                   "updated_at=%s WHERE id=%s", (_now(), job_id))
            continue
        if live:
            with store.connect() as connection, connection.cursor() as cursor:
                original = same_opening(cursor, job_id, company, title, url)
                if original:
                    cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', repost_of=%s, unresolved_reason='same_opening', updated_at=%s WHERE id=%s",
                                   (original, _now(), job_id))
                    counts["duplicate"] += 1
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
               "work_mode": work_mode, "salary": salary, "apply_kind": apply_kind,
               "location": location, "route": route, "text": full}
        if gated and fit_lane:                                           # the lane policy chose the visible lane (a UK job at a sponsor is Skilled Worker)
            row["lane"], row["eligible"] = fit_lane, [n for n in (eligible or "").split(",") if n]
        try:
            page_id = existing_page(client, key) or client.create(properties(row), body_blocks(key, desc))   # slow: no connection open
        except NotionError as error:
            counts["failed"] += 1
            counts["error"] = f"{error} {getattr(error, 'detail', '')}".strip()[:80]       # a fixed code (and Notion's own error code), so a job that fails every run says why
            if counts["failed"] >= 3:          # systemic (auth/schema/limit): stop instead of hammering
                break
            continue
        with store.connect() as connection, connection.cursor() as cursor:  # record at once: a crash cannot duplicate
            cursor.execute("UPDATE v7_jobs SET status='PUBLISHED', notion_page_id=%s, updated_at=%s WHERE id=%s",
                           (page_id, _now(), job_id))
            cursor.execute("INSERT IGNORE INTO v7_ledger_urls (url_hash, source, seen_at) VALUES (%s,'v7',%s)",
                           (url_key(url), _now()))
        counts["created"] += 1
    from lifeos.jobs import fit_sync                                                 # noqa: PLC0415
    counts["fit_sync"] = fit_sync.run(client, live, gated)                          # pages published before they were scored get their Fit
    if live:
        from lifeos.jobs import readback                                              # noqa: PLC0415
        counts["readback"] = readback.run(client, live)
    counts["calls"] = client.calls
    return counts
