"""Resolve stage: NEW jobs -> final apply link, saved to Hostinger (D1: nothing without a final link is ever published).

Per source: read a batch of NEW rows, close the connection, run the (slow) free-Chromium link chain, reconnect, save.
Outcome per job: RESOLVED (final_apply_url + apply_kind), DUPLICATE (same final link already stored), or left NEW with a
fixed unresolved_reason so the next run retries. Logs: counts only.
"""
import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from lifeos.platform import limits, runtime
from lifeos.jobs import quality, store


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def url_key(url):
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()


def pick(connection, source, limit):
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, source_url FROM v7_jobs WHERE status='NEW' AND source=%s "
                       "AND (unresolved_reason IS NULL OR unresolved_reason NOT LIKE 'closed%%') AND resolve_attempts < %s "
                       "ORDER BY resolve_attempts, last_seen DESC LIMIT %s", (source, limits.RESOLVE_MAX_ATTEMPTS, limit))
        return [(row[0], row[1]) for row in cursor.fetchall()]


def pick_rows(connection, source, limit):
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, source_url, company, title, location_text FROM v7_jobs WHERE status='NEW' AND source=%s "
                       "AND (unresolved_reason IS NULL OR unresolved_reason NOT LIKE 'closed%%') AND resolve_attempts < %s "
                       "ORDER BY resolve_attempts, last_seen DESC LIMIT %s", (source, limits.RESOLVE_MAX_ATTEMPTS, limit))
        return [tuple(row) for row in cursor.fetchall()]


DIRECT_LINK = ("original_post", "page_data")     # the link was read off the job's own page (Jobright's "Original Job Post"), not reached by clicking around


def unproven(result):
    """True for a landing whose shape is ambiguous but whose provenance is direct: it may be RESOLVED, and Enrich must then prove it by the page title."""
    url = result.get("url")
    return bool(result.get("outcome") == "landed" and url and result.get("via") in DIRECT_LINK and quality.link_problem(url) in quality.AMBIGUOUS)


def apply_result(connection, job_id, result):
    """Write one outcome. Returns 'resolved' | 'duplicate' | 'pending'."""
    now = _now()
    url, kind = result.get("url"), result.get("kind")
    proof = "unproven" if unproven(result) else None
    with connection.cursor() as cursor:
        if result.get("outcome") == "landed" and url and kind and quality.link_problem(url) and not proof:
            result = {"outcome": "bad_link_" + quality.link_problem(url)}        # not a single-vacancy link: pending, never RESOLVED (Enrich would reject it)
            url = kind = None
        if result.get("outcome") == "landed" and url and kind:
            cursor.execute("SELECT id FROM v7_jobs WHERE final_apply_url=%s AND id<>%s LIMIT 1", (url, job_id))
            if cursor.fetchone():
                cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', final_apply_url=NULL, unresolved_reason=%s, "
                               "updated_at=%s WHERE id=%s", ("dup_of_final_url", now, job_id))
                return "duplicate"
            cursor.execute("UPDATE v7_jobs SET status='RESOLVED', final_apply_url=%s, apply_kind=%s, link_proof=%s, "
                           "unresolved_reason=NULL, updated_at=%s WHERE id=%s", (url, kind, proof, now, job_id))
            return "resolved"
        reason = (result.get("outcome") or "unknown")[:100]
        bump = 0 if reason in ("deferred", "rate_limited") else 1        # not tried yet = not an attempt
        cursor.execute("UPDATE v7_jobs SET unresolved_reason=%s, resolve_attempts=resolve_attempts+%s, "
                       "status=IF(resolve_attempts>=%s, 'HOLD', status), updated_at=%s WHERE id=%s",   # later SET sees the bump
                       (reason, bump, limits.RESOLVE_MAX_ATTEMPTS, now, job_id))
        return "pending"


def why(result):
    """The fixed reason a result is counted under: what landed, or why it did not (detail after the first colon is dropped)."""
    if result.get("outcome") == "landed":
        problem = result.get("url") and quality.link_problem(result["url"])
        if problem and unproven(result):
            return "landed_unproven"                                        # accepted on provenance; Enrich proves it by title
        return "bad_link_" + problem if problem else "landed:" + str(result.get("kind"))
    return str(result.get("outcome") or "unknown").split(":")[0]


def note_page_keys(counts, result, top=25):
    """Counts of the liveness-like field names a resolver saw on the aggregator's own page (diagnostic; names only)."""
    keys = result.get("page_keys")
    if not keys:
        return
    seen = counts.setdefault("page_keys", {})
    for key in keys:
        seen[key] = seen.get(key, 0) + 1
    counts["page_keys"] = dict(sorted(seen.items(), key=lambda kv: -kv[1])[:top])


def note_refusal(counts, result, top=10):
    """Where refused landings come from, for diagnosis: the host and the NAMES of the query parameters (never paths or values)."""
    url = result.get("url")
    if result.get("outcome") != "landed" or not url or not quality.link_problem(url) or unproven(result):
        return
    parts = urlsplit(url)
    refused = counts.setdefault("refused", {"hosts": {}, "query_keys": {}})
    host = (parts.hostname or "?").lower().removeprefix("www.")
    refused["hosts"][host] = refused["hosts"].get(host, 0) + 1
    for key in {q.split("=")[0] for q in parts.query.split("&") if q}:
        refused["query_keys"][key[:30]] = refused["query_keys"].get(key[:30], 0) + 1
    for name in refused:
        refused[name] = dict(sorted(refused[name].items(), key=lambda kv: -kv[1])[:top])


BATCH_ROWS = 50
DEADLINE_MINUTES = 28          # stop starting batches well before the 40-minute job limit; unfinished rows stay NEW


def run_rows(source, limit, live, resolver, batch=BATCH_ROWS, deadline_minutes=DEADLINE_MINUTES, clock=time.monotonic):
    """Like run(), but the resolver gets full rows [(id, source_url, company, title, location)].
    Rows go in batches that are saved as they finish, so a slow or killed run keeps its work."""
    with store.connect() as connection:
        store.ensure_schema(connection)
        rows = pick_rows(connection, source, limit)
    counts = {"picked": len(rows), "resolved": 0, "duplicate": 0, "pending": 0, "closed": 0, "kind": {}, "why": {},
              "batches": 0, "not_reached": 0}
    context = runtime.RunContext.start(deadline_minutes * 60, clock=clock)         # the shared deadline primitive (second consumer: Interview OS)
    for start in range(0, len(rows), batch):
        if context.expired():
            counts["not_reached"] = len(rows) - start
            break
        part = rows[start:start + batch]
        results = resolver(part)                                      # slow: no database connection open
        counts["batches"] += 1
        if not live:
            counts["dry_run"] = True
        _save(counts, part, results, live)
        print(f"{source} batch {counts['batches']}: rows {start + len(part)}/{len(rows)} resolved={counts['resolved']} "
              f"pending={counts['pending']} duplicate={counts['duplicate']} closed={counts['closed']}", flush=True)
    return counts


def _save(counts, rows, results, live):
    with store.connect() as connection:
        for row, result in zip(rows, results):
            key = result.get("outcome") if result.get("outcome") != "landed" else "landed:" + str(result.get("kind"))
            counts["why"][key] = counts["why"].get(key, 0) + 1
            if not live:
                continue
            if result.get("outcome") == "closed":
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE v7_jobs SET status='CLOSED', unresolved_reason='closed', updated_at=%s "
                                   "WHERE id=%s", (_now(), row[0]))
                counts["closed"] += 1
                continue
            verdict = apply_result(connection, row[0], result)
            counts[verdict] += 1
            if verdict == "resolved":
                counts["kind"][result["kind"]] = counts["kind"].get(result["kind"], 0) + 1


def run(source, limit, live, resolver):
    """resolver(urls) -> list of result dicts aligned with urls."""
    with store.connect() as connection:
        store.ensure_schema(connection)
        rows = pick(connection, source, limit)
    counts = {"picked": len(rows), "resolved": 0, "duplicate": 0, "pending": 0, "kind": {}, "why": {}}
    if not rows:
        return counts
    results = resolver([url for _, url in rows])
    if not live:
        for result in results:
            key = result["outcome"] if result.get("outcome") != "landed" else "landed:" + str(result.get("kind"))
            counts["kind"][key] = counts["kind"].get(key, 0) + 1
            counts["why"][why(result)] = counts["why"].get(why(result), 0) + 1       # what a live run would do with each, by the same test
            note_refusal(counts, result)
            note_page_keys(counts, result)
        counts["dry_run"] = True
        return counts
    with store.connect() as connection:
        for (job_id, _), result in zip(rows, results):
            verdict = apply_result(connection, job_id, result)
            counts[verdict] += 1
            counts["why"][why(result)] = counts["why"].get(why(result), 0) + 1          # why the pending ones are pending, counts only
            note_refusal(counts, result)
            note_page_keys(counts, result)
            if verdict == "resolved":
                counts["kind"][result["kind"]] = counts["kind"].get(result["kind"], 0) + 1
    return counts
