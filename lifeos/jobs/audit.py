"""Precision audit: no-network pass over READY and PUBLISHED rows with the quality guards.

A failing row goes back to NEW (final link cleared, reason audit_*). A failing PUBLISHED row also has its Notion
page trashed and its ledger hash removed, UNLESS lifeos.jobs.guard finds a human pursuit may exist: that row is
left as it is and only counted. With V7_FIT_GATE=true it also trashes published pages whose lane decision is
EXCLUDE (recoverable 30 days) under the same guard; the job becomes EXCLUDED_FIT and keeps its ledger hash so it
is never published again. Logs: counts only.
"""
import os

from lifeos.jobs import guard, hiring_pipeline, lanes, ledger, quality, store
from lifeos.jobs.identity import aggregator_twin, norm, url_key, vacancy_id
from lifeos.jobs.publish import AGGREGATOR
from lifeos.platform import limits, notion_client


_now = store.now


def judge(url, full_text, proven=False, title_only=False, thin_ok=False):
    """None when the row is sound, else a fixed reason code. proven: Enrich proved the page by title, so an ambiguous
    link shape is not a reason. title_only: a title-search find has only a title and snippet, so a thin
    description is not.
    """
    problem = quality.url_problem(url)
    if problem and not (proven and problem in quality.AMBIGUOUS):
        return "audit_url_" + problem
    if title_only:
        return None
    problem = quality.jd_problem(full_text)
    if problem == "thin" and thin_ok:
        return None
    return "audit_jd_" + problem if problem else None


def twins(groups, places, skip):
    """[(page row, the ATS page it repeats)] for each aggregator page with no vacancy id whose place is an ATS
    vacancy's place, in a group of pages with one company and title. The aggregator page goes; a page already
    going is never kept.
    """
    out, going = [], set(skip)
    for group in groups.values():
        for r in group:
            if r[0] in going:
                continue
            twin = next((p for p in group if p[0] != r[0] and p[0] not in going and aggregator_twin(r[2], places.get(r[0]), p[2], places.get(p[0]), AGGREGATOR)), None)
            if twin:
                out.append((r, twin[0]))
                going.add(r[0])
    return out


def _ids(cursor, where, params=()):
    cursor.execute("SELECT id FROM v7_jobs WHERE (" + where + ") AND status IN ('READY','PUBLISHED')", params)
    return {r[0] for r in cursor.fetchall()}


def run(limit, live, environ=os.environ, ready_only=False):
    """ready_only: the gate before publish, judging only the READY rows about to be published with the same guards;
    no page is read or trashed. The full pass runs after publish.
    """
    counts = {"checked": 0, "failed": 0, "demoted": 0, "trashed": 0, "trash_errors": 0, "by_reason": {}, "protected": {}}
    gated = environ.get("V7_FIT_GATE") == "true" and not ready_only
    statuses = "('READY')" if ready_only else "('READY','PUBLISHED')"
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT j.id, j.status, j.final_apply_url, j.notion_page_id, d.full_text, j.company, j.title FROM v7_jobs j"
                           f" LEFT JOIN v7_job_descriptions d ON d.job_id=j.id WHERE j.status IN {statuses}"
                           " ORDER BY j.id LIMIT %s", (limit,))
            rows = cursor.fetchall()
            excluded = []
            if gated:
                cursor.execute("SELECT j.id, j.status, j.final_apply_url, j.notion_page_id, NULL, j.company, j.title FROM v7_jobs j"
                               " JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status='PUBLISHED' AND j.notion_page_id IS NOT NULL"
                               " AND f.admission='EXCLUDE' ORDER BY j.id LIMIT %s", (limit,))
                excluded = cursor.fetchall()
            proven = _ids(cursor, "link_proof='title'")
            watched = _ids(cursor, "source LIKE %s", ("web:titlewatch:%",))
            thin_ok = _ids(cursor, " OR ".join(["source LIKE %s"] * len(lanes.THIN_REVIEW_PREFIXES)), tuple(p + "%" for p in lanes.THIN_REVIEW_PREFIXES))
            watched |= _ids(cursor, "link_proof=%s", (lanes.LISTING_PROOF,))
            cursor.execute("SELECT id, company, title FROM v7_jobs WHERE status IN ('READY','PUBLISHED')")
            watched |= {r[0] for r in cursor.fetchall() if lanes.on_keep_list(r[1], r[2])}
            pages, places = [], {}
            if not ready_only:
                cursor.execute("SELECT id, status, final_apply_url, notion_page_id, NULL, company, title FROM v7_jobs"
                               " WHERE status='PUBLISHED' AND notion_page_id IS NOT NULL ORDER BY id")
                pages = cursor.fetchall()
                cursor.execute("SELECT id, location_text FROM v7_jobs WHERE status='PUBLISHED' AND notion_page_id IS NOT NULL")
                places = dict(cursor.fetchall())
    counts["checked"], counts["pages"], counts["excluded_waiting"] = len(rows), len(pages), len(excluded)
    bad = [(r, judge(r[2], r[4], r[0] in proven, r[0] in watched, r[0] in thin_ok)) for r in rows]
    bad = [(r, why) for r, why in bad if why]
    already = {r[0] for r, _ in bad}
    dupe_of = {}
    bad += [(r, "excluded_fit") for r in excluded if r[0] not in already]
    first_page = {}
    for r in pages:
        # Trashing needs identity shown: the same ATS vacancy (or no id on either link). An id on one link only, or
        # two different ids, keeps both pages.
        key = (norm(r[5]), norm(r[6]))
        if len(key[1].split()) < 3:
            continue
        earlier = next((p for p in first_page.get(key, []) if vacancy_id(p[2]) == vacancy_id(r[2])), None)
        if earlier and r[0] not in already:
            bad.append((r, "duplicate_page"))
            dupe_of[r[0]] = earlier[0]
        first_page.setdefault(key, []).append(r)
    for r, twin in twins(first_page, places, already | {r[0] for r in excluded} | set(dupe_of)):
        bad.append((r, "duplicate_page"))
        dupe_of[r[0]] = twin
    counts["failed"] = len(bad)
    for _, why in bad:
        counts["by_reason"][why] = counts["by_reason"].get(why, 0) + 1
    if not live or not bad:
        return counts
    client = notion_client.Client(environ) if any(r[3] for r, _ in bad) else None
    target = ledger.verify(client) if client else ledger.OK
    if target != ledger.OK:
        counts["protected"][target] = len([1 for r, _ in bad if r[3]])
        bad = [(r, why) for r, why in bad if not r[3]]
    opportunities = []
    pipeline_id = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    if client and pipeline_id:
        opportunities, state = hiring_pipeline.snapshot(client, pipeline_id, ledger=client)
        if state != "ok":
            counts["protected"]["handoff_unreadable"] = len([1 for r, _ in bad if r[3]])
            bad = [(r, why) for r, why in bad if not r[3]]
    with_page = [i for i, (r, _) in enumerate(bad) if r[3] and client]
    later = set(with_page[limits.AUDIT_PAGES_PER_RUN:])
    counts["deferred"] = len(later)
    bad = [b for i, b in enumerate(bad) if i not in later]
    for (job_id, status, url, page_id, _, company, title), why in bad:
        if page_id and client:
            held = guard.protection(client, page_id, company, title, opportunities)
            if held:
                counts["protected"][held] = counts["protected"].get(held, 0) + 1
                continue
            try:
                client.call("PATCH", f"/pages/{page_id}", {"in_trash": True})
                counts["trashed"] += 1
            except notion_client.NotionError:
                if ledger.page_state(client, page_id) == "gone":
                    counts["already_trashed"] = counts.get("already_trashed", 0) + 1
                else:
                    counts["trash_errors"] += 1
                    continue
        if why == "duplicate_page":
            with store.connect() as connection, connection.cursor() as cursor:
                cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', repost_of=%s, notion_page_id=NULL, unresolved_reason='same_opening', updated_at=%s WHERE id=%s",
                               (dupe_of.get(job_id), _now(), job_id))
            counts["duplicates_cleared"] = counts.get("duplicates_cleared", 0) + 1
            continue
        if why == "excluded_fit":
            with store.connect() as connection, connection.cursor() as cursor:
                cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT', notion_page_id=NULL, unresolved_reason=%s, updated_at=%s WHERE id=%s",
                               ("excluded_fit", _now(), job_id))
            counts["excluded_cleared"] = counts.get("excluded_cleared", 0) + 1
            continue
        with store.connect() as connection, connection.cursor() as cursor:
            if page_id and url:
                cursor.execute("DELETE FROM v7_ledger_urls WHERE url_hash=%s AND source='v7'", (url_key(url),))
            cursor.execute("UPDATE v7_jobs SET status='NEW', final_apply_url=NULL, notion_page_id=NULL,"
                           " unresolved_reason=%s, resolve_attempts=0, updated_at=%s WHERE id=%s", (why, _now(), job_id))
            cursor.execute("DELETE FROM v7_job_descriptions WHERE job_id=%s", (job_id,))
        counts["demoted"] += 1
    return counts
