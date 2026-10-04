"""US Remote web acquisition, one bounded run: pick the due boards, list them (no database connection held), classify against the
accepted state, drop unequivocal misses cheaply, and admit the rest into Jobs OS through intake.add_job. Counts only in the log.
The accepted state only advances on a COMPLETE listing; a FAILED board keeps its state and can never imply a removal.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha1

from lifeos.jobs import enrich, intake, store
from lifeos.platform import limits
from lifeos.sources.web import diff, lister, registry, suppress

DEFAULT_LANE = "US Remote"
PROVIDER = {"greenhouse": "Greenhouse", "ashby": "Ashby", "lever": "Lever", "smartrecruiters": "SmartRecruiters", "workable": "Workable",
            "pinpoint": "Pinpoint", "workday": "Workday", "jibe": "Jibe", "teamtailor": "Teamtailor"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class Outcome:
    source: dict
    now: datetime
    status: str
    reason: str | None = None
    frontier: str | None = None
    items: list = field(default_factory=list)        # (job, hash, ingest, suppress_reason, is_new) to upsert
    unchanged: list = field(default_factory=list)    # provider job ids to touch
    removed: list = field(default_factory=list)
    relocate: dict = field(default_factory=dict)     # D104: provider job id -> full location, for unchanged jobs whose stored place was cut at 200 characters
    admit: list = field(default_factory=list)        # jobs to admit into Jobs OS now
    pending: int = 0
    due_at: datetime | None = None
    counts: dict = field(default_factory=dict)


def pick_due(sources, states, now, limit):
    """Sources never run, or due, oldest first, at most `limit`."""
    due = [s for s in sources if s["id"] not in states or states[s["id"]]["due_at"] <= now]
    return sorted(due, key=lambda s: (states.get(s["id"], {}).get("due_at") or datetime.min, s["id"]))[:limit]


def unforced(states, sources, force):
    """D104: a dispatch may name a company (3+ letters, the Hourly `report` input) whose board is read now whether or not it is due: the states of the sources that match are dropped."""
    force = (force or "").strip().lower()
    if len(force) < 3:
        return states
    hit = {s["id"] for s in sources if force in s["id"].lower() or force in (s.get("company") or "").lower()}
    return {k: v for k, v in states.items() if k not in hit}


def next_due(source_id, now, complete, failures, pending):
    if pending:
        return now                                                      # a backlog drains on the next run
    if complete:
        slot = int(sha1(source_id.encode()).hexdigest()[:6], 16) % 60   # spread boards across the hour
        return now + timedelta(hours=limits.WEB_REFRESH_HOURS, minutes=slot)
    return now + timedelta(hours=min(limits.WEB_RETRY_HOURS * 2 ** failures, limits.WEB_REFRESH_HOURS))


def plan(source, listing, previous, now, budget, lane=DEFAULT_LANE):
    """Pure: the outcome of one board. previous: {provider job id: (material hash, ingest state)}."""
    if listing.status != lister.COMPLETE:
        return Outcome(source, now, "FAILED", listing.reason)
    new, changed, unchanged, removed = diff.classify({i: h for i, (h, _) in previous.items()}, listing.jobs, True)
    by_id = {j["id"]: j for j in listing.jobs}
    relocate = {i: by_id[i]["location"] for i in unchanged if len(by_id[i].get("location") or "") > 200}
    first_party = source.get("tier") == "employer"
    backlog = [by_id[i] for i in unchanged if previous[i][1] == "PENDING"]       # seen before, not yet admitted
    backlog += [by_id[i] for i in unchanged if previous[i][1] == "SUPPRESSED" and not suppress.reason(by_id[i], now.date(), lane, first_party)]   # D111: a rule that no longer drops it (age) lets it in
    out = Outcome(source, now, "COMPLETE", frontier=diff.frontier({j["id"]: diff.material_hash(j) for j in listing.jobs}),
                  unchanged=[i for i in unchanged if previous[i][1] != "PENDING"], removed=removed, relocate=relocate)
    suppressed = {}
    for job, digest, is_new in [(j, h, True) for j, h in new] + [(j, h, False) for j, h in changed]:
        why = suppress.reason(job, now.date(), lane, first_party)
        if why:
            suppressed[why] = suppressed.get(why, 0) + 1
            out.items.append((job, digest, "SUPPRESSED", why, is_new))
        elif budget > len(out.admit):
            out.admit.append(job)
            out.items.append((job, digest, "INGESTED", None, is_new))
        else:
            out.items.append((job, digest, "PENDING", None, is_new))
    for job in backlog:
        if budget > len(out.admit):
            out.admit.append(job)
            out.items.append((job, previous[job["id"]][0], "INGESTED", None, False))
        else:
            out.pending += 1
    out.pending += sum(1 for _, _, ingest, _, _ in out.items if ingest == "PENDING")
    out.counts = {"added": len(new), "changed": len(changed), "unchanged": len(unchanged), "removed": len(removed),
                  "suppressed": sum(suppressed.values()), "relocated": len(relocate), "admit": len(out.admit), "pending": out.pending, "why": suppressed}
    return out


class SqlRepo:
    """Short connections only: nothing is held across network calls."""

    def states(self, ids):
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT source_id, due_at, failures FROM v7_sources")
            return {r[0]: {"due_at": r[1], "failures": r[2]} for r in cursor.fetchall() if r[0] in ids}

    def items(self, source_id):
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT provider_job_id, material_hash, ingest FROM v7_source_items WHERE source_id=%s AND state='CURRENT'", (source_id,))
            return {r[0]: (r[1], r[2]) for r in cursor.fetchall()}

    def commit(self, o, failures, lane=DEFAULT_LANE):
        now, sid = o.now, o.source["id"]
        with store.connect() as connection:
            if o.status == "COMPLETE":
                job_ids = {}
                for job in o.admit:
                    job_ids[job["id"]] = self._admit(connection, o.source, job, now, lane)
                with connection.cursor() as cursor:
                    for job, digest, ingest, why, is_new in o.items:
                        cursor.execute(
                            "INSERT INTO v7_source_items (source_id, provider_job_id, material_hash, state, ingest, suppress_reason,"
                            " first_seen, last_seen, job_id) VALUES (%s,%s,%s,'CURRENT',%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE"
                            " material_hash=VALUES(material_hash), state='CURRENT', ingest=VALUES(ingest),"
                            " suppress_reason=VALUES(suppress_reason), last_seen=VALUES(last_seen),"
                            " job_id=COALESCE(VALUES(job_id), job_id)",
                            (sid, job["id"], digest, ingest, why, now, now, job_ids.get(job["id"])))
                    if o.unchanged:
                        cursor.executemany("UPDATE v7_source_items SET last_seen=%s WHERE source_id=%s AND provider_job_id=%s",
                                           [(now, sid, i) for i in o.unchanged])
                    if o.relocate:                                   # D104: one pass until every stored place carries all its offices
                        cursor.executemany("UPDATE v7_jobs j JOIN v7_source_items i ON i.job_id = j.id SET j.location_text=%s"
                                           " WHERE i.source_id=%s AND i.provider_job_id=%s AND CHAR_LENGTH(j.location_text) < %s",
                                           [(loc[:2000], sid, i, min(len(loc), 2000)) for i, loc in o.relocate.items()])
                    if o.removed:
                        cursor.executemany("UPDATE v7_source_items SET state='REMOVED', last_seen=%s WHERE source_id=%s AND provider_job_id=%s",
                                           [(now, sid, i) for i in o.removed])
            with connection.cursor() as cursor:
                complete = o.status == "COMPLETE"
                cursor.execute(
                    "INSERT INTO v7_sources (source_id, kind, due_at, last_status, last_reason, last_complete_at, frontier_hash, failures)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE due_at=VALUES(due_at), last_status=VALUES(last_status),"
                    " last_reason=VALUES(last_reason), last_complete_at=COALESCE(VALUES(last_complete_at), last_complete_at),"
                    " frontier_hash=COALESCE(VALUES(frontier_hash), frontier_hash), failures=VALUES(failures)",
                    (sid, o.source["kind"], o.due_at, o.status, o.reason, now if complete else None, o.frontier,
                     0 if complete else failures + 1))
                c = o.counts
                cursor.execute(
                    "INSERT INTO v7_source_runs (source_id, ran_at, status, reason, added, changed, unchanged, removed, suppressed, ingested)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sid, now, o.status, o.reason, c.get("added", 0), c.get("changed", 0), c.get("unchanged", 0), c.get("removed", 0),
                     c.get("suppressed", 0), c.get("admit", 0)))

    @staticmethod
    def _admit(connection, source, job, now, lane):
        """One posting into Jobs OS: the employer's own URL is the final link; a description in the list is guarded by enrich.finish."""
        provider = PROVIDER.get(source["kind"], "Open Jobs")
        with connection.cursor() as cursor:
            key, is_new = intake.add_job(cursor, {
                "url": job["url"], "status": "RESOLVED", "title": job["title"], "company": source["company"],
                "location": job["location"], "salary": None, "source": "web:" + source["id"], "provider": provider, "lane": lane,
                "age_days": (now.date() - job["posted"]).days if job["posted"] else None, "received": now, "provider_score": None,
                "posted": job["posted"]}, now)
            cursor.execute("SELECT id FROM v7_jobs WHERE dedupe_key=%s", (key,))
            found = cursor.fetchone()
            if not found:
                return None                                              # a 90-day tombstone kept it out
            job_id = found[0]
            if is_new:
                cursor.execute("UPDATE v7_jobs SET final_apply_url=%s, apply_kind='ats', route_evidence=%s, updated_at=%s WHERE id=%s",
                               (job["url"], source.get("route_evidence"), now, job_id))
            else:                                                       # D111: a job already stored whose listed title or place changed (the reader learned to split Stream's label) is put right,
                title, place = job["title"][:300], (job["location"] or "")[:2000]      # judged again, and an exclusion that rested on the wrong place is lifted
                cursor.execute("UPDATE v7_jobs SET title=%s, location_text=%s, status=IF(status='EXCLUDED_FIT' AND unresolved_reason='lane_exclude', 'READY', status),"
                               " updated_at=%s WHERE id=%s AND notion_page_id IS NULL AND (title<>%s OR COALESCE(location_text, '')<>%s)", (title, place, now, job_id, title, place))
                if cursor.rowcount:
                    cursor.execute("DELETE FROM v7_job_fit WHERE job_id=%s", (job_id,))
        if is_new and job["content"]:
            from lifeos.jobs import jd                                              # noqa: PLC0415
            desc = jd.describe(job["content"], is_html=False)
            result = enrich.finish(job["title"], desc, job["title"], job["posted"], "ats_list", None, lane)
            if result["outcome"] in ("ready", "stale"):
                result["final_url"] = job["url"]
                enrich.save(connection, job_id, result)
        return job_id


def run(limit, live, now=None, lister_fn=lister.list_source, repo=None, sources=None, lane=DEFAULT_LANE):
    now = now or _now()
    sources = sources if sources is not None else registry.for_lane(lane)
    if lane == "Scale-Up":
        sources = [{**s, "route_evidence": "Scale-up:POSITIVE"} for s in sources]      # membership of the curated sponsor universe is the route evidence
    sources = [s for s in sources if s["kind"] in lister.READERS]
    repo = repo or SqlRepo()
    if isinstance(repo, SqlRepo):                     # a dry run reads the state tables too, so they must exist (CREATE IF NOT EXISTS; no rows written)
        with store.connect() as connection:
            store.ensure_schema(connection)
    states = repo.states({s["id"] for s in sources})
    states = unforced(states, sources, os.environ.get("WEB_FORCE"))
    due = pick_due(sources, states, now, min(limit, limits.WEB_BOARDS_PER_RUN))
    counts = {"lane": lane, "sources": len(sources), "due": len(due), "complete": 0, "failed": 0, "added": 0, "changed": 0, "unchanged": 0,
              "removed": 0, "suppressed": 0, "admit": 0, "pending": 0, "why": {}, "failed_why": {}, "failed_kind": {}, "failed_ids": []}
    with ThreadPoolExecutor(max_workers=limits.ATS_WORKERS) as pool:               # different hosts: no shared limit
        listings = list(pool.map(lister_fn, due))
    budget = limits.WEB_INGEST_PER_RUN
    for source, listing in zip(due, listings):
        previous = repo.items(source["id"])
        outcome = plan(source, listing, previous, now, budget, lane)
        failures = states.get(source["id"], {}).get("failures", 0)
        outcome.due_at = next_due(source["id"], now, outcome.status == "COMPLETE", failures, outcome.pending)
        if outcome.status == "FAILED":
            counts["failed"] += 1
            counts["failed_why"][outcome.reason] = counts["failed_why"].get(outcome.reason, 0) + 1
            kind = str(source.get("kind") or "unknown")
            counts["failed_kind"][kind] = counts["failed_kind"].get(kind, 0) + 1          # the board's ATS family
            if len(counts["failed_ids"]) < 10:
                counts["failed_ids"].append(source["id"])                                 # a public registry id (the source list is committed), never a job
        else:
            counts["complete"] += 1
            budget -= len(outcome.admit)
            for k in ("added", "changed", "unchanged", "removed", "suppressed", "admit", "pending"):
                counts[k] += outcome.counts[k]
            for why, n in outcome.counts["why"].items():
                counts["why"][why] = counts["why"].get(why, 0) + n
        if live:
            repo.commit(outcome, failures, lane)
    counts["saved"] = bool(live)
    return counts
