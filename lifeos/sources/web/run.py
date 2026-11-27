"""Web acquisition, one bounded run: list due boards, classify against the accepted state, admit the rest.

The accepted state advances only on a COMPLETE listing; a FAILED board keeps its state and never implies a removal."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
from contextlib import ExitStack, contextmanager
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha1

from lifeos.jobs import enrich, intake, quality, store
from lifeos.platform import limits, read_slots
from lifeos.platform.db import _lost as db_lost
from lifeos.sources.web import diff, lister, registry, suppress

DEFAULT_LANE = "US Remote"
SAVE_SECONDS = 20
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
    items: list = field(default_factory=list)
    refreshed: int = 0
    unchanged: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    relocate: dict = field(default_factory=dict)
    admit: list = field(default_factory=list)
    pending: int = 0
    due_at: datetime | None = None
    counts: dict = field(default_factory=dict)
    validators: dict = field(default_factory=dict)
    unchanged_runs: int = 0


def pick_due(sources, states, now, limit, select=None, forced=()):
    """Never-run or due sources, oldest first, at most `limit`. `select`: None reads both; "known" only boards read
    before (plus curated extras); "unread" only never-read ones. A forced board skips only the due check and goes
    first."""
    if select == "known":
        sources = [s for s in sources if s["id"] in states or s.get("extra")]
    elif select == "unread":
        sources = [s for s in sources if s["id"] not in states]
    due = [s for s in sources if s["id"] in forced or s["id"] not in states or states[s["id"]]["due_at"] <= now]
    return sorted(due, key=lambda s: (datetime.min if s["id"] in forced else states.get(s["id"], {}).get("due_at") or datetime.min, s["id"]))[:limit]


def forced_ids(sources, force):
    force = (force or "").strip().lower()
    force = force[len("board:"):] if force.startswith("board:") else force
    names = [n.strip() for n in force.split(",") if len(n.strip()) >= 3 and n.strip() != "funnel"]
    return {s["id"] for s in sources if any(n in s["id"].lower() or n in (s.get("company") or "").lower() for n in names)}


def skip_allowed(state):
    runs = (state or {}).get("unchanged_runs") or 0
    return runs >= 1 and runs % limits.WEB_FULL_EVERY != 0


SLOT_EPOCH = datetime(2026, 1, 1)


def slot_due(source_id, now, hours):
    period = max(int(hours * 60), 1)
    phase = int(sha1(source_id.encode()).hexdigest()[:8], 16) % period
    since = (now - SLOT_EPOCH).total_seconds() / 60 + period / 2 - phase
    return SLOT_EPOCH + timedelta(minutes=phase + -(-since // period) * period)


def next_due(source_id, now, complete, failures, pending, hours=None, quiet_runs=0, dead=False):
    hours = (hours or limits.WEB_REFRESH_HOURS) * min(1 + max(quiet_runs - 1, 0), limits.WEB_BACKOFF_MAX)  # a seed board carries its own refresh; a quiet board backs off
    if pending:
        return now
    if complete:
        return slot_due(source_id, now, hours)
    if dead:
        return now + timedelta(days=limits.BOARDS_DEAD_RETRY_DAYS)  # repeated not_found: a slow retry, never a removal
    return now + timedelta(hours=min(limits.WEB_RETRY_HOURS * 2 ** failures, hours))

def plan(source, listing, previous, now, budget, lane=DEFAULT_LANE, listed=None):
    if listing.status != lister.COMPLETE:
        return Outcome(source, now, "FAILED", listing.reason)
    new, changed, unchanged, removed = diff.classify({i: h for i, (h, _) in previous.items()}, listing.jobs, True)
    still = [i for i in removed if listed is not None and i in listed]
    removed = [i for i in removed if i not in still]
    by_id = {j["id"]: j for j in listing.jobs}
    relocate = {i: by_id[i]["location"] for i in unchanged if len(by_id[i].get("location") or "") > 200}
    first_party = source.get("tier") == "employer"
    backlog = [by_id[i] for i in unchanged if previous[i][1] == "PENDING"]
    backlog += [by_id[i] for i in unchanged if previous[i][1] == "SUPPRESSED" and not suppress.reason(by_id[i], now.date(), lane, first_party)]
    out = Outcome(source, now, "COMPLETE", frontier=diff.frontier({j["id"]: diff.material_hash(j) for j in listing.jobs}),
                  unchanged=[i for i in unchanged if previous[i][1] != "PENDING"] + still, removed=removed, relocate=relocate)
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
                  "suppressed": sum(suppressed.values()), "relocated": len(relocate), "admit": len(out.admit), "pending": out.pending, "why": suppressed, "still_listed": len(still)}
    return out


def update_in_chunks(cursor, head, lead, ids, size=500):
    for start in range(0, len(ids), size):
        part = ids[start:start + size]
        cursor.execute(f"{head} IN ({','.join(['%s'] * len(part))})", (*lead, *part))


class SqlRepo:
    """Short connections, one per read or save; inside `holding()` one connection is reused (pinged when idle, dropped
    on failure, detached after a timed-out save)."""
    HELD_IDLE_SECONDS = 3
    _holding = False
    _live = None

    @contextmanager
    def holding(self):
        self._holding, self._live = True, None
        try:
            yield self
        finally:
            self._holding = False
            self.release()

    def release(self):
        live, self._live = self._live, None
        if live:
            with contextlib.suppress(Exception):
                live[0].__exit__(None, None, None)

    def detach(self):
        self._live = None

    @contextmanager
    def _connection(self):
        if not self._holding:
            with store.connect() as connection:
                yield connection
            return
        if self._live and time.monotonic() - self._live[2] > self.HELD_IDLE_SECONDS:
            ping = getattr(self._live[1], "ping", None)
            try:
                if ping:
                    ping(reconnect=True)
            except Exception:                                                            # noqa: BLE001
                self.release()
        if not self._live:
            manager = store.connect()
            self._live = (manager, manager.__enter__(), time.monotonic())
        manager, connection = self._live[0], self._live[1]
        try:
            yield connection
        except BaseException:
            if self._live and self._live[1] is connection:
                self.release()
            raise
        if self._live and self._live[1] is connection:
            self._live = (manager, connection, time.monotonic())

    def states(self, ids):
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT source_id, due_at, failures, etag, last_modified, frontier_hash, unchanged_runs FROM v7_sources")
            return {r[0]: {"due_at": r[1], "failures": r[2], "etag": r[3], "last_modified": r[4], "frontier": r[5], "unchanged_runs": r[6] or 0}
                    for r in cursor.fetchall() if r[0] in ids}

    def items(self, source_id):
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT provider_job_id, material_hash, ingest FROM v7_source_items WHERE source_id=%s AND state='CURRENT'", (source_id,))
            return {r[0]: (r[1], r[2]) for r in cursor.fetchall()}

    def hot_ids(self, ids):
        ids, hot = list(ids), set()
        with self._connection() as connection, connection.cursor() as cursor:
            for start in range(0, len(ids), 500):
                part = ids[start:start + 500]
                cursor.execute(f"SELECT DISTINCT source_id FROM v7_source_items WHERE state='CURRENT' AND source_id IN ({','.join(['%s'] * len(part))})", part)
                hot.update(r[0] for r in cursor.fetchall())
        return hot

    def commit(self, o, failures, lane=DEFAULT_LANE):
        now, sid = o.now, o.source["id"]
        with self._connection() as connection:
            if o.status == "COMPLETE":
                job_ids, refreshed = {}, []
                for job in o.admit:
                    job_ids[job["id"]] = self._admit(connection, o.source, job, now, lane, refreshed)
                o.refreshed = len(refreshed)
                with connection.cursor() as cursor:
                    for job, digest, ingest, why, is_new in o.items:
                        cursor.execute(
                            "INSERT INTO v7_source_items (source_id, provider_job_id, material_hash, state, ingest, suppress_reason,"
                            " first_seen, last_seen, job_id) VALUES (%s,%s,%s,'CURRENT',%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE"
                            " material_hash=VALUES(material_hash), state='CURRENT', ingest=VALUES(ingest),"
                            " suppress_reason=VALUES(suppress_reason), last_seen=VALUES(last_seen),"
                            " job_id=COALESCE(VALUES(job_id), job_id)",
                            (sid, job["id"], digest, ingest, why, now, now, job_ids.get(job["id"])))
                    update_in_chunks(cursor, "UPDATE v7_source_items SET last_seen=%s WHERE source_id=%s AND provider_job_id", (now, sid), o.unchanged)
                    if o.relocate:
                        cursor.executemany("UPDATE v7_jobs j JOIN v7_source_items i ON i.job_id = j.id SET j.location_text=%s"
                                           " WHERE i.source_id=%s AND i.provider_job_id=%s AND CHAR_LENGTH(j.location_text) < %s",
                                           [(loc[:2000], sid, i, min(len(loc), 2000)) for i, loc in o.relocate.items()])
                    update_in_chunks(cursor, "UPDATE v7_source_items SET state='REMOVED', last_seen=%s WHERE source_id=%s AND provider_job_id", (now, sid), o.removed)
            if o.status == "UNCHANGED":
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE v7_sources SET due_at=%s, last_status='COMPLETE', last_reason=%s, last_complete_at=%s, failures=0, unchanged_runs=%s,"
                                   " etag=COALESCE(%s, etag), last_modified=COALESCE(%s, last_modified) WHERE source_id=%s",
                                   (o.due_at, o.reason, now, o.unchanged_runs, o.validators.get("etag"), o.validators.get("last_modified"), sid))
                return
            with connection.cursor() as cursor:
                complete = o.status == "COMPLETE"
                c = o.counts
                quiet = complete and o.source.get("quiet") and not any(c.get(k) for k in ("added", "changed", "removed", "admit"))
                cursor.execute(
                    "INSERT INTO v7_sources (source_id, kind, due_at, last_status, last_reason, last_complete_at, frontier_hash, failures)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE due_at=VALUES(due_at), last_status=VALUES(last_status),"
                    " last_reason=VALUES(last_reason), last_complete_at=COALESCE(VALUES(last_complete_at), last_complete_at),"
                    " frontier_hash=COALESCE(VALUES(frontier_hash), frontier_hash), failures=VALUES(failures)",
                    (sid, o.source["kind"], o.due_at, o.status, o.reason, now if complete else None, o.frontier,
                     0 if complete else failures + 1))
                cursor.execute("UPDATE v7_sources SET unchanged_runs=%s, etag=%s, last_modified=%s WHERE source_id=%s",
                               (o.unchanged_runs if complete else 0, o.validators.get("etag"), o.validators.get("last_modified"), sid))
                if quiet:
                    return
                cursor.execute(
                    "INSERT INTO v7_source_runs (source_id, ran_at, status, reason, added, changed, unchanged, removed, suppressed, ingested)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sid, now, o.status, o.reason, c.get("added", 0), c.get("changed", 0), c.get("unchanged", 0), c.get("removed", 0),
                     c.get("suppressed", 0), c.get("admit", 0)))

    @staticmethod
    def _admit(connection, source, job, now, lane, refreshed=None):
        provider = PROVIDER.get(source["kind"], "Open Jobs")
        with connection.cursor() as cursor:
            key, is_new = intake.add_job(cursor, {
                "url": job["url"], "status": "RESOLVED", "title": job["title"], "company": job.get("company") or source["company"],
                "location": job["location"], "salary": None, "source": "web:" + source["id"], "provider": provider, "lane": lane,
                "age_days": (now.date() - job["posted"]).days if job["posted"] else None, "received": now, "provider_score": None,
                "posted": job["posted"]}, now)
            cursor.execute("SELECT id FROM v7_jobs WHERE dedupe_key=%s", (key,))
            found = cursor.fetchone()
            if not found:
                return None
            job_id = found[0]
            if is_new:
                proof = "unproven" if source.get("tier") == "employer" and quality.link_problem(job["url"]) in quality.AMBIGUOUS else None
                cursor.execute("UPDATE v7_jobs SET final_apply_url=%s, apply_kind='ats', route_evidence=%s, link_proof=%s, updated_at=%s WHERE id=%s",
                               (job["url"], source.get("route_evidence"), proof, now, job_id))
            else:  # a stored job whose listed title or place changed is put right and judged again
                title, place = job["title"][:300], (job["location"] or "")[:2000]
                cursor.execute("UPDATE v7_jobs SET title=%s, location_text=%s, status=IF(status='EXCLUDED_FIT' AND unresolved_reason='lane_exclude', 'READY', status),"
                               " updated_at=%s WHERE id=%s AND notion_page_id IS NULL AND (title<>%s OR COALESCE(location_text, '')<>%s)", (title, place, now, job_id, title, place))
                if cursor.rowcount:
                    cursor.execute("DELETE FROM v7_job_fit WHERE job_id=%s", (job_id,))
                if job["content"] and refresh_description(cursor, job_id, job, lane):
                    if refreshed is not None:
                        refreshed.append(job_id)
        if is_new and job["content"]:
            from lifeos.jobs import jd                                              # noqa: PLC0415
            desc = jd.describe(job["content"], is_html=False)
            result = enrich.finish(job["title"], desc, job["title"], job["posted"], "ats_list", None, lane)
            if result["outcome"] in ("ready", "stale"):
                result["final_url"] = job["url"]
                enrich.save(connection, job_id, result)
        return job_id


def refresh_description(cursor, job_id, job, lane):
    """Rewrite a held posting's stored description only when its fingerprint differs; True when rewritten. Status,
    link, posting date and Notion fields are untouched."""
    from lifeos.jobs import jd                                                      # noqa: PLC0415
    result = enrich.finish(job["title"], jd.describe(job["content"], is_html=False), job["title"], job["posted"], "ats_list", None, lane)
    if result["outcome"] not in ("ready", "stale"):
        return False
    cursor.execute("SELECT fingerprint FROM v7_job_descriptions WHERE job_id=%s", (job_id,))
    held = cursor.fetchone()
    if held and held[0] == result["description"]["fingerprint"]:
        return False
    enrich.save_description(cursor, job_id, result)
    return True


def save_bounded(commit, seconds, stuck=None):
    """Run `commit` under a wall-clock limit -> (saved, seconds taken). Past `seconds` the write keeps running on a
    daemon thread and the board fails `save_timeout`; an error inside the write is raised. `stuck` (kept by the
    caller) holds a timed-out write the next save waits for."""
    box = {}
    began = time.monotonic()
    if stuck:
        stuck[0].join(seconds)
        if stuck[0].is_alive():
            return False, round(time.monotonic() - began, 1)
        stuck.clear()

    def work():
        try:
            commit()
        except BaseException as error:                                           # noqa: BLE001
            box["error"] = error
        else:
            box["ok"] = True
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(max(seconds - (time.monotonic() - began), 0))
    took = round(time.monotonic() - began, 1)
    if thread.is_alive():
        if stuck is not None:
            stuck[:] = [thread]
        return False, took
    if "error" in box:
        raise box["error"]
    return True, took


def timing(rows, waited, wall):
    def spread(values):
        values = sorted(values)
        return [round(values[len(values) // 2], 2), round(values[min(len(values) - 1, int(len(values) * 0.95))], 2)]
    return {"boards": len(rows), "wall_s": round(wall), "waiting_for_reads_s": round(waited), "saving_s": round(sum(r[3] for r in rows)),
            "read_s_p50_p95": spread([r[2] for r in rows]), "save_s_p50_p95": spread([r[3] for r in rows])}


def summary_markdown(lane, rows, limit=10):
    shown = sorted(rows, key=lambda r: -(r[2] + r[3]))[:limit]
    shown += [r for r in rows if r[1] not in ("COMPLETE", "UNCHANGED") and r not in shown][:20]
    lines = [f"### Web boards, {lane}: read and save seconds", "", "| Board | Result | Read s | Save s |", "|---|---|---:|---:|"]
    lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |" for r in shown]
    return "\n".join(lines) + "\n"


def bounded(lister_fn, seconds, timings=None):
    """Limit one board read to `seconds`: past it the board is FAILED "board_timeout" and the read abandoned (the
    request timeout only bounds the wait for the next bytes)."""
    def call(source):
        with read_slots.slot("ats"):
            return read(source)

    def read(source):
        box = {}
        began = time.monotonic()

        def work():
            try:
                box["listing"] = lister_fn(source)
            except BaseException as error:                                            # noqa: BLE001
                box["error"] = error
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(seconds)
        if timings is not None:
            timings[source["id"]] = round(time.monotonic() - began, 1)
        if thread.is_alive():
            return lister.Listing(lister.FAILED, reason="board_timeout")
        if "error" in box:
            raise box["error"]
        return box["listing"]
    return call


def overdue_hours(source, states, now):
    due_at = (states.get(source["id"]) or {}).get("due_at")
    return round(max((now - due_at).total_seconds(), 0) / 3600, 1) if due_at else 0.0


def run(limit, live, now=None, lister_fn=lister.list_source, repo=None, sources=None, lane=DEFAULT_LANE, cap=None, budget=None, screen=None, workers=None, select=None,
        board_seconds=None, save_seconds=None, stop=None):
    """Run one acquisition pass and return its counts. `screen` (job to reason) is applied to a COMPLETE listing
    before the diff: only "kept_" jobs are stored, and a stored job screened out while still listed stays CURRENT.
    Board failures never fail the stage or the tick."""
    now = now or _now()
    sources = sources if sources is not None else registry.for_lane(lane)
    if lane == "Scale-Up":
        sources = [{**s, "route_evidence": "Scale-up:POSITIVE"} for s in sources]
    sources = [s for s in sources if s["kind"] in lister.READERS]
    repo = repo or SqlRepo()
    if isinstance(repo, SqlRepo):
        with store.connect() as connection:
            store.ensure_schema(connection)
    states = repo.states({s["id"] for s in sources})
    forced = forced_ids(sources, os.environ.get("WEB_FORCE"))
    everyone = pick_due(sources, states, now, None, select, forced)
    due = everyone[:min(limit, cap or limits.WEB_BOARDS_PER_RUN)]
    unread = sum(s["id"] not in states for s in sources)
    states = {k: v for k, v in states.items() if k not in forced}
    counts = {"lane": lane, "sources": len(sources), "due": len(due), "complete": 0, "failed": 0, "added": 0, "changed": 0, "unchanged": 0,
              "removed": 0, "suppressed": 0, "admit": 0, "pending": 0, "why": {}, "failed_why": {}, "failed_kind": {}, "failed_ids": [], "dead_ids": [], "not_modified": 0, "same_frontier": 0, "save_timeout": 0, "save_blocked": 0}
    stuck_saves = []
    if select:
        counts["unread"] = unread
        counts["due_total"], counts["oldest_due_hours"] = len(everyone), overdue_hours(everyone[0], states, now) if everyone else 0.0
    attempted, waited, began = 0, 0.0, time.monotonic()
    asked = [{**s, "if_etag": states[s["id"]].get("etag"), "if_modified": states[s["id"]].get("last_modified")}
             if skip_allowed(states.get(s["id"])) else s for s in due]
    batch_hot = getattr(repo, "hot_ids", None)
    hot_now = set(batch_hot([s["id"] for s in due if skip_allowed(states.get(s["id"]))])) if batch_hot else None
    waiting = None
    read_seconds, rows = {}, []

    def note_failure(source, reason, failures):
        counts["failed"] += 1
        counts["failed_why"][reason] = counts["failed_why"].get(reason, 0) + 1
        kind = str(source.get("kind") or "unknown")
        counts["failed_kind"][kind] = counts["failed_kind"].get(kind, 0) + 1
        if len(counts["failed_ids"]) < 10:
            counts["failed_ids"].append(source["id"])
        if reason == "not_found" and failures + 1 >= limits.BOARDS_DEAD_AFTER and len(counts["dead_ids"]) < limits.BOARDS_DEAD_IDS_MAX:
            counts["dead_ids"].append(source["id"])

    def save(source, outcome, failures, result):
        saved, took, before, lost = True, 0.0, list(stuck_saves), False
        if live:
            try:
                saved, took = save_bounded(lambda: repo.commit(outcome, failures, lane), save_seconds or SAVE_SECONDS, stuck_saves)
            except Exception as error:                                                   # noqa: BLE001
                if not (getattr(repo, "_holding", False) and db_lost(error)):
                    raise
                repo.release()  # the held session dropped between boards: this board is not advanced and the pass goes on
                saved, lost = False, True
        reason = None if saved else ("save_lost" if lost else "save_blocked" if before and stuck_saves == before else "save_timeout")
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
            if not lost:
                getattr(repo, "detach", lambda: None)()
        rows.append((source["id"], reason or result, read_seconds.get(source["id"], 0.0), took))
        return reason

    held = ExitStack()
    if hasattr(repo, "holding"):
        held.enter_context(repo.holding())
    pool = ThreadPoolExecutor(max_workers=workers or limits.ATS_WORKERS)
    try:
        listings = iter(pool.map(bounded(lister_fn, board_seconds or limits.WEB_BOARD_SECONDS, read_seconds), asked))
        budget = limits.WEB_INGEST_PER_RUN if budget is None else budget
        if screen:
            counts["screen"], counts["still_listed"] = {}, 0
        for source in due:
            if stop and stop():  # soft deadline: no new board starts, so the receipt is always written
                counts["stopped_at_deadline"] = True
                break
            waiting = source["id"]
            asking = time.monotonic()
            listing = next(listings)
            waited += time.monotonic() - asking
            attempted += 1
            listed = None
            raw = diff.frontier({j["id"]: diff.material_hash(j) for j in listing.jobs}) if listing.status == lister.COMPLETE else None
            if screen and listing.status == lister.COMPLETE:
                listed, kept = {j["id"] for j in listing.jobs}, []
                for job in listing.jobs:
                    why = screen(job)
                    counts["screen"][why] = counts["screen"].get(why, 0) + 1
                    if why.startswith("kept_"):
                        kept.append(job)
                listing = lister.Listing(lister.COMPLETE, kept, validators=listing.validators)
            state = states.get(source["id"]) or {}
            failures = state.get("failures", 0)
            skip = None
            if listing.status == lister.UNCHANGED:
                skip = "not_modified"
            elif listing.status == lister.COMPLETE and skip_allowed(state) and state.get("frontier") and raw == state["frontier"]:
                skip = "same_frontier"
            if skip:
                outcome = Outcome(source, now, "UNCHANGED", skip, frontier=state.get("frontier"), validators=listing.validators,
                                  unchanged_runs=state.get("unchanged_runs", 0) + 1)
                is_hot = source["id"] in hot_now if hot_now is not None else bool(repo.items(source["id"]))
                outcome.due_at = next_due(source["id"], now, True, 0, 0, (source.get("hot_hours") if is_hot else None) or source.get("refresh_hours"), outcome.unchanged_runs)
                failed = save(source, outcome, failures, "UNCHANGED")
                if failed:
                    note_failure(source, failed, failures)
                    continue
                counts["complete"] += 1
                counts[skip] += 1
                continue
            previous = repo.items(source["id"])
            outcome = plan(source, listing, previous, now, budget, lane, listed)
            if raw:
                outcome.frontier = raw  # stored as the whole-board fingerprint, so a screened-out job leaving the board is never skipped
            hot = previous or outcome.items
            quiet = outcome.status == "COMPLETE" and not any(outcome.counts.get(k) for k in ("added", "changed", "removed", "admit", "pending"))
            outcome.unchanged_runs = state.get("unchanged_runs", 0) + 1 if quiet else 0
            outcome.validators = listing.validators
            dead = outcome.status == "FAILED" and outcome.reason == "not_found" and failures + 1 >= limits.BOARDS_DEAD_AFTER
            outcome.due_at = next_due(source["id"], now, outcome.status == "COMPLETE", failures, outcome.pending,
                                      (source.get("hot_hours") if hot else None) or source.get("refresh_hours"), outcome.unchanged_runs, dead)
            failed = save(source, outcome, failures, outcome.reason if outcome.status == "FAILED" else "COMPLETE")
            if outcome.status == "FAILED":
                note_failure(source, outcome.reason, failures)
            elif failed:
                note_failure(source, failed, failures)
            else:
                counts["complete"] += 1
                counts["jd_reread"] = counts.get("jd_reread", 0) + outcome.refreshed
                budget -= len(outcome.admit)
                for k in ("added", "changed", "unchanged", "removed", "suppressed", "admit", "pending"):
                    counts[k] += outcome.counts[k]
                if screen:
                    counts["still_listed"] += outcome.counts["still_listed"]
                for why, n in outcome.counts["why"].items():
                    counts["why"][why] = counts["why"].get(why, 0) + n
    except BaseException:
        if waiting:
            print(f"web: stopped while waiting for board {waiting}", file=sys.stderr)
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        held.close()
    counts["saved"] = bool(live)
    if select:
        left = everyone[attempted:]
        counts["deferred"], counts["oldest_deferred_hours"] = len(left), overdue_hours(left[0], states, now) if left else 0.0
    if select:
        counts["jd_reread"] = counts.get("jd_reread", 0)
        counts["fit_rescore_due"] = counts["jd_reread"]
        counts["cache_skips"] = counts["not_modified"] + counts["same_frontier"]
    if rows:
        counts["timing"] = timing(rows, waited, time.monotonic() - began)
    counts["slowest"] = [[r[0], r[2], r[3]] for r in sorted(rows, key=lambda r: -(r[2] + r[3]))[:10]]
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and rows:
        try:
            with open(path, "a") as handle:
                handle.write(summary_markdown(lane, rows))
        except OSError:
            pass
    return counts
