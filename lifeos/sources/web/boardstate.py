"""Counts-only diagnostic: for every Scale-Up board in the registry, what did V7 see and what became of it? Reads the store, writes nothing, prints no title, link or text.

Per source id (the ids are in the public registry): the registry status and reader kind, the board's own state (last read result, how long ago it last completed, consecutive
failures, when it is next due), the vacancies it currently lists by what became of them (the ingest state and the suppress reason), and the jobs created from it by status.
It answers "is this board fed, and if nothing reaches the Ledger, where does it stop?" without a private Notion page."""
from lifeos.jobs import store
from lifeos.sources.web import registry


def _age(seconds):
    if seconds is None:
        return "never"
    days = seconds / 86400
    return "under_1d" if days < 1 else "1_to_3d" if days < 3 else "over_3d"


def run(limit, live):
    sources = registry.load(registry.PATHS["Scale-Up"])
    out = {"boards": {}, "totals": {"sources": len(sources), "never_read": 0, "failing": 0, "listing_jobs": 0, "no_jobs": 0, "with_jobs": 0, "published": 0}}
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT source_id, last_status, last_reason, TIMESTAMPDIFF(SECOND, last_complete_at, UTC_TIMESTAMP()), failures, due_at <= UTC_TIMESTAMP() FROM v7_sources")
        state = {row[0]: row[1:] for row in cursor.fetchall()}
        cursor.execute("SELECT source_id, ingest, COALESCE(suppress_reason, ''), COUNT(*) FROM v7_source_items WHERE state = 'CURRENT' AND source_id LIKE 'su-%%' "
                       "GROUP BY source_id, ingest, suppress_reason")
        items = {}
        for source_id, ingest, reason, n in cursor.fetchall():
            items.setdefault(source_id, {})[f"{ingest}/{reason}" if reason else ingest] = n
        cursor.execute("SELECT source, status, COUNT(*) FROM v7_jobs WHERE source LIKE 'web:su-%%' GROUP BY source, status")
        jobs = {}
        for source, status, n in cursor.fetchall():
            jobs.setdefault(source[len("web:"):], {})[status] = n
    for source in sources:
        sid = source["id"]
        last_status, last_reason, age, failures, due = state.get(sid, (None, None, None, 0, None))
        listed = sum(items.get(sid, {}).values())
        board = {"kind": source.get("kind"), "registry": source.get("status"), "read": last_status or "never", "reason": last_reason or "", "last_complete": _age(age),
                 "failures": int(failures or 0), "due_now": bool(due), "listing": listed, "items": items.get(sid, {}), "jobs": jobs.get(sid, {})}
        out["boards"][sid] = board
        out["totals"]["never_read"] += last_status is None
        out["totals"]["failing"] += bool(last_status and last_status != "COMPLETE") or bool(failures)
        out["totals"]["listing_jobs"] += listed
        out["totals"]["no_jobs"] += listed == 0
        out["totals"]["with_jobs"] += bool(jobs.get(sid))
        out["totals"]["published"] += jobs.get(sid, {}).get("PUBLISHED", 0)
    return out
