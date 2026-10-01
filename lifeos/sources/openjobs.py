"""Open Jobs change feed (CC0, public) as a US Remote discovery channel.

This is a TAIL consumer, not a mirror: it follows the published change generations forward, applies each one atomically (the posting
rows it admits and the checkpoint commit together), and keeps only the postings that pass the cheap US Remote suppression. It never
bootstraps the multi-million-row corpus. A broken chain (a missing or corrupt manifest/page, or a checkpoint the chain no longer reaches)
is DEGRADED: nothing is applied, the checkpoint stays, and a gap is never skipped. The first run applies the newest delta only.
Counts only in the log; the User-Agent carries the contact from OPEN_JOBS_CONTACT (kept out of the public repo).
"""
from datetime import date, datetime, timezone
import hashlib
import json
import os
import re

from lifeos.jobs import lanes, store
from lifeos.platform.http import fetch
from lifeos.sources.web import suppress
from lifeos.sources.web.run import SqlRepo

BASE = "https://backend.dehnbostele.workers.dev/data/changes/"
FEED_ID = "openjobs"
SOURCE = {"id": FEED_ID, "kind": FEED_ID, "route_evidence": None}
LANE = "US Remote"
MAX_GENERATIONS = 3          # applied per run, oldest first
MAX_ADMIT = 3000             # per generation: a safety valve, counted when it trips (never silent)
PAGE_BYTES = 5 * 1024 * 1024
HTML = re.compile(r"</?(p|div|ul|li|br|h\d|strong|em|span)\b", re.I)


class Degraded(Exception):
    """The chain cannot be verified: apply nothing, keep the checkpoint."""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def headers():
    contact = os.environ.get("OPEN_JOBS_CONTACT", "").strip()
    return {"User-Agent": f"life-os-v7 personal job feed reader ({contact})" if contact else "life-os-v7 personal job feed reader",
            "Accept": "application/json"}


def get(path, fetcher=fetch, fresh=False, limit=PAGE_BYTES):
    url = BASE + path + (f"?check={int(datetime.now(timezone.utc).timestamp())}" if fresh else "")
    page = fetcher(url, timeout=60, max_hops=2, max_bytes=limit, headers=headers())
    if page.status != 200:
        raise Degraded(f"http_{page.status}" if page.status else "network")
    return page.html


def manifest(path, fetcher=fetch, fresh=False):
    try:
        header = json.loads(get(path, fetcher, fresh))
        claimed = header["generation"]
        if _digest(_canonical({k: v for k, v in header.items() if k != "generation"})) != claimed:
            raise Degraded("manifest_checksum")
        if header.get("scope") != "crawler-export-v1" or header.get("kind") not in ("bootstrap", "delta"):
            raise Degraded("unsupported_manifest")
        return header
    except (ValueError, KeyError, TypeError):
        raise Degraded("bad_manifest") from None


def plan_chain(head, checkpoint, load):
    """Generations to apply, oldest first. First run (no checkpoint): the head delta only. Otherwise every delta after the checkpoint;
    a chain that does not reach the checkpoint (or hits a bootstrap first) is DEGRADED."""
    if checkpoint == head["generation"]:
        return []
    if checkpoint is None:
        if head["kind"] != "delta":
            raise Degraded("head_is_bootstrap")
        return [head]
    chain, node = [head], head
    while node["previous"] != checkpoint:
        if node["kind"] == "bootstrap" or node["previous"] is None or len(chain) > 60:
            raise Degraded("gap")
        node = load(f"{node['previous']}/manifest.json")
        chain.append(node)
    return list(reversed(chain))


def page_events(header, name, fetcher=fetch):
    """Verified events of one page."""
    meta = next((p for p in header["pages"] if p["file"] == name), None)
    if meta is None:
        raise Degraded("unknown_page")
    data = get(f"{header['generation']}/{name}", fetcher).encode()
    if len(data) != meta["bytes"] or _digest(data) != meta["sha256"]:
        raise Degraded("page_corrupt")
    lines = data.splitlines()
    if len(lines) != meta["rows"]:
        raise Degraded("page_rows")
    try:
        return [json.loads(line) for line in lines]
    except ValueError:
        raise Degraded("page_json") from None


def _day(value):
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def company_from(slug):
    return " ".join(w.capitalize() for w in re.split(r"[-_.]+", slug) if w)[:200] or "Unknown"


def candidate(event):
    """Upsert event -> (job dict for the web admit path, source suppression view) or None when it is not a US Remote candidate."""
    job = event["job"]
    view = {"title": job["title"] or "", "location": job["location"] or "", "posted": _day(job["published_at"]) or _day(job["first_seen_at"])}
    if not view["title"] or not job["url"]:
        return None, "no_title_or_url"
    if not suppress.TARGET.search(view["title"]):
        return None, "off_family"
    why = suppress.reason(view, date.today(), LANE)
    if why:
        return None, why
    content = job["content"] or ""
    if HTML.search(content):
        from lifeos.jobs import jd                                                      # noqa: PLC0415
        content = jd.html_to_text(content)
    # A bulk feed needs positive evidence: the US Remote lane is remote-only, so a posting that never says remote (in its place, title or the
    # description's explicit statements) is not a candidate here. (A newsletter or board job with an unknown mode still goes to Review.)
    if lanes.detect_work_mode(view["location"], view["title"], content) != "remote":
        return None, "no_remote_evidence"
    return {"id": event["key"], "title": view["title"], "location": view["location"], "url": job["url"], "posted": view["posted"],
            "content": content or None, "ats": job.get("ats")}, None


def process(header, fetcher=fetch):
    """One generation -> (admits, removed keys, counts). Pure of the database."""
    counts = {"events": 0, "upserts": 0, "removes": 0, "admit": 0, "over_cap": 0, "why": {}}
    admits, removes = [], []
    for meta in header["pages"]:
        for event in page_events(header, meta["file"], fetcher):
            counts["events"] += 1
            if event["op"] == "remove":
                counts["removes"] += 1
                removes.append(event["key"])
                continue
            counts["upserts"] += 1
            job, why = candidate(event)
            if why:
                counts["why"][why] = counts["why"].get(why, 0) + 1
            elif len(admits) >= MAX_ADMIT:
                counts["over_cap"] += 1
            else:
                admits.append((event["key"], job))
    counts["admit"] = len(admits)
    return admits, removes, counts


class Repo:
    def checkpoint(self):
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT generation FROM v7_feed WHERE feed_id=%s", (FEED_ID,))
            row = cursor.fetchone()
            return row[0] if row else None

    def held(self):
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT provider_job_id FROM v7_source_items WHERE source_id=%s AND state='CURRENT'", (FEED_ID,))
            return {r[0] for r in cursor.fetchall()}

    def commit(self, header, admits, removes, status, now):
        """Rows and checkpoint in one go: a failure leaves the checkpoint where it was, and replaying is idempotent."""
        with store.connect() as connection:
            for key, job in admits:
                src = {**SOURCE, "company": company_from(key.split("/")[1].split("#")[0]), "kind": (job.get("ats") or FEED_ID)}
                job_id = SqlRepo._admit(connection, src, {**job, "id": key}, now, LANE)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO v7_source_items (source_id, provider_job_id, material_hash, state, ingest, first_seen, last_seen, job_id)"
                        " VALUES (%s,%s,%s,'CURRENT','INGESTED',%s,%s,%s) ON DUPLICATE KEY UPDATE state='CURRENT', last_seen=VALUES(last_seen),"
                        " job_id=COALESCE(VALUES(job_id), job_id)", (FEED_ID, key, _digest(key.encode()), now, now, job_id))
            with connection.cursor() as cursor:
                for key in removes:
                    cursor.execute("UPDATE v7_jobs j JOIN v7_source_items i ON i.job_id=j.id SET j.status='CLOSED', j.updated_at=%s"
                                   " WHERE i.source_id=%s AND i.provider_job_id=%s AND j.status IN ('NEW','RESOLVED','READY')", (now, FEED_ID, key))
                    cursor.execute("UPDATE v7_source_items SET state='REMOVED', last_seen=%s WHERE source_id=%s AND provider_job_id=%s",
                                   (now, FEED_ID, key))
                cursor.execute(
                    "INSERT INTO v7_feed (feed_id, generation, cursor_date, applied_at, status) VALUES (%s,%s,%s,%s,%s)"
                    " ON DUPLICATE KEY UPDATE generation=VALUES(generation), cursor_date=VALUES(cursor_date), applied_at=VALUES(applied_at),"
                    " status=VALUES(status)", (FEED_ID, header["generation"], header["cursor"], now, status))

    def degrade(self, now):
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE v7_feed SET status='DEGRADED', applied_at=%s WHERE feed_id=%s", (now, FEED_ID))


def run(limit, live, now=None, fetcher=fetch, repo=None):
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    repo = repo or Repo()
    if isinstance(repo, Repo):                          # the checkpoint table must exist before a dry run reads it (no rows written)
        with store.connect() as connection:
            store.ensure_schema(connection)
    counts = {"generations": 0, "events": 0, "upserts": 0, "removes": 0, "admit": 0, "over_cap": 0, "removed_held": 0, "why": {}, "status": "OK"}
    try:
        head = manifest("latest.json", fetcher, fresh=True)
        checkpoint = repo.checkpoint()
        chain = plan_chain(head, checkpoint, lambda p: manifest(p, fetcher))[:MAX_GENERATIONS]
        held = repo.held() if chain else set()
        for header in chain:
            admits, removes, c = process(header, fetcher)
            removes = [k for k in removes if k in held]
            counts["generations"] += 1
            counts["removed_held"] += len(removes)
            for k in ("events", "upserts", "removes", "admit", "over_cap"):
                counts[k] += c[k]
            for why, n in c["why"].items():
                counts["why"][why] = counts["why"].get(why, 0) + n
            if live:
                repo.commit(header, admits, removes, "OK", now)
    except Degraded as error:
        counts["status"] = f"DEGRADED:{error}"
        if live:
            repo.degrade(now)
    counts["saved"] = bool(live)
    return counts

