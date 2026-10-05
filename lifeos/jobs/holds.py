"""Counts-only diagnostic: why do parked (HOLD) roles on employers' own boards not move? Reads v7_jobs, writes nothing, prints no title, link or text.

Per source: status and reason, how long since the row last changed, how many attempts it has used, whether it has a link, and whether today's daily re-read
(`relink_first_party`) would select it right now."""
from datetime import timedelta

from lifeos.jobs import enrich, store
from lifeos.platform import limits

RETRY_REASONS = ("link_mismatch", "jd_listing", "jd_template")


def _age(days):
    return "under_1d" if days < 1 else "1_to_7d" if days < 7 else "over_7d"


def run(limit, live):
    now = enrich._now()
    out = {"by_source": {}, "eligible_now": 0, "total": 0}
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT source, status, unresolved_reason, resolve_attempts, enrich_attempts, updated_at, final_apply_url IS NOT NULL, source_url IS NOT NULL "
                       "FROM v7_jobs WHERE source LIKE 'web:su-%%' AND status IN ('HOLD', 'NEW', 'RESOLVED')")
        for source, status, reason, resolve, enrich_n, updated, has_final, has_source in cursor.fetchall():
            row = out["by_source"].setdefault(source, {})
            key = f"{status}/{reason or ''}/{_age((now - updated).total_seconds() / 86400 if updated else 99)}/resolve{resolve}/enrich{enrich_n}/final{int(bool(has_final))}"
            row[key] = row.get(key, 0) + 1
            out["total"] += 1
            old = bool(updated) and updated < now - timedelta(days=1)
            if status == "HOLD" and not has_final and has_source and old and (str(reason).startswith("http_40") or reason in RETRY_REASONS):
                out["eligible_now"] += 1
        cursor.execute("SELECT status, COUNT(*) FROM v7_jobs WHERE source LIKE 'web:su-%%' GROUP BY status")
        out["status_totals"] = {s: n for s, n in cursor.fetchall()}
        cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE status='RESOLVED' AND final_apply_url IS NOT NULL")
        out["resolved_queue"] = cursor.fetchone()[0]
    out["enrich_pick_limit"] = 150
    out["resolve_max_attempts"] = limits.RESOLVE_MAX_ATTEMPTS
    return out
