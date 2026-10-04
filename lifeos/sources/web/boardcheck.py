"""Board check (D110): for named Scale-Up employers, what does the LIVE board return, and what became of each job?

The question this answers is "this role is open at that employer, why is it not in the database?". It reads each named employer's board from the runner (the same
reader the Scale-Up web pass uses), applies the same early suppression, looks every job up by its URL key in `v7_jobs`, and writes ONE private page in the Job
Ledger: per employer, every job grouped as stored (with its status, Fit and reason), dropped before storing (with the suppression reason) or not stored.
The page has no row in v7_jobs, so no stage touches it. The log carries counts only.
Input: BOARD_COMPANY, comma separated, 3+ letters each, matched against the registry's source id and company ("board:" in front is ignored)."""
from datetime import datetime
import os
from zoneinfo import ZoneInfo

from lifeos.jobs import identity, ledger, store
from lifeos.jobs.review import _block
from lifeos.platform.notion_client import Client, NotionError, rich_text
from lifeos.sources.web import lister, registry, suppress

TZ = ZoneInfo("America/Chicago")


def names_from(raw):
    raw = (raw or "").strip()
    raw = raw[len("board:"):] if raw.lower().startswith("board:") else raw
    return [n.strip().lower() for n in raw.split(",") if len(n.strip()) >= 3][:12]


def matching_sources(names, sources):
    return [s for s in sources if any(n in s["id"].lower() or n in (s.get("company") or "").lower() for n in names)]


def outcome(job, today, stored):
    """(group, detail) for one live job: dropped before storing, stored (its status), or not stored. `stored`: the v7_jobs row (status, reason, score, admission) or None."""
    why = suppress.reason(job, today, "Scale-Up")
    if why:
        return "Dropped before storing: " + why, ""
    if stored:
        status, reason, score, admission = stored
        detail = (f"Fit {score}" if score is not None else "not scored") + (f" | {admission}" if admission else "") + (f" | {reason}" if reason else "")
        return "Stored: " + status, detail
    return "Returned by the board but not stored", ""


def blocks_for(company, jobs, today, stored_by_key):
    groups = {}
    for job in jobs:
        group, detail = outcome(job, today, stored_by_key.get(identity.url_hash(job["url"])))
        posted = job["posted"].isoformat() if job.get("posted") else "no date"
        groups.setdefault(group, []).append(f"{job['title']} | {(job['location'] or 'no place stated')[:160]} | {posted}" + (f" | {detail}" if detail else ""))
    blocks = [_block("heading_1", f"{company} ({len(jobs)} on the board now)")]
    for name in sorted(groups):
        blocks.append(_block("heading_3", f"{name} ({len(groups[name])})"))
        blocks += [_block("bulleted_list_item", line) for line in groups[name]]
    return blocks, {name: len(lines) for name, lines in groups.items()}


def run(limit, live, environ=os.environ, fetcher=None):
    names = names_from(environ.get("BOARD_COMPANY") or environ.get("REVIEW_COMPANY"))
    if not names:
        return {"error": "BOARD_COMPANY_missing_or_short"}
    today = datetime.now(TZ).date()
    chosen = matching_sources(names, registry.load(registry.PATHS["Scale-Up"]))
    if not chosen:
        return {"error": "no_matching_source"}
    counts, blocks = {"sources": {}, "written": False}, []
    for source in chosen:
        if source["status"] != "ready":
            counts["sources"][source["id"]] = {"not_readable": source["status"]}
            blocks += [_block("heading_1", f"{source.get('company') or source['id']} (no readable board: {source['status']})")]
            continue
        listing = lister.list_source(source, fetcher) if fetcher else lister.list_source(source)
        if listing.status != lister.COMPLETE:
            counts["sources"][source["id"]] = {"board": "failed:" + (listing.reason or "")}
            blocks += [_block("heading_1", f"{source.get('company') or source['id']} (board read failed: {listing.reason})")]
            continue
        keys = [identity.url_hash(j["url"]) for j in listing.jobs]
        stored = {}
        if keys:
            with store.connect() as connection, connection.cursor() as cursor:
                for start in range(0, len(keys), 100):
                    chunk = keys[start:start + 100]
                    cursor.execute("SELECT j.dedupe_key, j.status, j.unresolved_reason, f.score, f.admission_reason FROM v7_jobs j LEFT JOIN v7_job_fit f ON f.job_id = j.id"
                                   " WHERE j.dedupe_key IN (" + ",".join(["%s"] * len(chunk)) + ")", tuple(chunk))
                    stored.update({row[0]: tuple(row[1:]) for row in cursor.fetchall()})
        part, groups = blocks_for(source.get("company") or source["id"], listing.jobs, today, stored)
        blocks += part
        counts["sources"][source["id"]] = {"listed": len(listing.jobs), "groups": groups}
    if not live:
        return counts
    client = Client(environ)
    if ledger.verify(client) != ledger.OK:
        counts["target"] = "not_ok"
        return counts
    title = f"Board Check - {', '.join(names)[:80]} - {datetime.now(TZ).date().isoformat()}"
    try:
        client.create({"Job": {"title": rich_text(title)}}, blocks)
    except NotionError:
        counts["failed"] = True
        return counts
    counts["written"] = True
    return counts
