"""Jobs on HOLD for a week are excluded (EXCLUDED_UNRESOLVED), so the newsletter mail that holds them open can close.

HOLD means retries ran out. After HOLD_DAYS the job becomes EXCLUDED_UNRESOLVED (terminal). A worthwhile HOLD job gets
one more try from day 6; one still unresolved RECOVERY_HOURS later is excluded as `unresolved_after_recovery`. Counts
only.
"""
from datetime import datetime, timedelta

from lifeos.jobs import lanes, store
from lifeos.platform import limits

HOLD_DAYS = 7
RECOVER_DAYS = 6
RECOVERY_HOURS = 12
RECOVER_PER_RUN = 25
EXCLUDED = "EXCLUDED_UNRESOLVED"
REASON = "hold_expired"
FAILED_RECOVERY = "unresolved_after_recovery"
# the sources a resolve stage reads
RESOLVED_BY = ("jobright", "linkedin-alerts", "linkedin-search", "dice")
# a retired source's NEW or HOLD jobs end EXCLUDED_UNRESOLVED with this reason
RETIRED = {"lensa": "source_retired_lensa"}
RETIRED["web:openjobs"] = "source_retired_openjobs"


def worthwhile(lane, posted, first_seen, reason, now):
    if str(reason or "").startswith(("closed", "dup")):
        return False
    policy = lanes.POLICIES.get(lanes.lane_for(lane))
    return bool(policy) and (now.date() - (posted or first_seen.date())).days <= policy.max_age_days


def recover(cursor, now, live):
    """Requeue the worthwhile HOLD jobs of day 6, once each (a dry run only counts). -> (candidates, requeued)."""
    cursor.execute("SELECT id, lane, posted_date, first_seen, unresolved_reason, final_apply_url IS NOT NULL, source, dedupe_key FROM v7_jobs WHERE status='HOLD' AND recovered_at IS NULL AND source_url IS NOT NULL "
                   "AND updated_at < %s AND updated_at >= %s ORDER BY updated_at, id", (now - timedelta(days=RECOVER_DAYS), now - timedelta(days=RECOVER_DAYS, hours=RECOVERY_HOURS)))
    picked = [(row[0], row[7]) for row in cursor.fetchall() if worthwhile(row[1], row[2], row[3], row[4], now) and (row[5] or row[6] in RESOLVED_BY)]
    done = []
    for job_id, key in picked[:RECOVER_PER_RUN] if live else ():
        cursor.execute("UPDATE v7_jobs SET status=CASE WHEN final_apply_url IS NULL THEN 'NEW' ELSE 'RESOLVED' END, unresolved_reason='recovery_retry', recovered_at=%s, updated_at=%s, "
                       "resolve_attempts=%s, enrich_attempts=%s WHERE id=%s AND status='HOLD' AND recovered_at IS NULL",
                       (now, now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1, job_id))
        if cursor.rowcount:
            done.append((job_id, key))
    if done:  # read-back
        cursor.execute("SELECT id, dedupe_key FROM v7_jobs WHERE id IN (" + ",".join(["%s"] * len(done)) + ") AND status=CASE WHEN final_apply_url IS NULL THEN 'NEW' ELSE 'RESOLVED' END AND unresolved_reason='recovery_retry' "
                       "AND recovered_at=%s AND resolve_attempts=%s AND enrich_attempts=%s", (*[d[0] for d in done], now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1))
        if sorted(tuple(r) for r in cursor.fetchall()) != sorted(done):
            raise store.StoreError("HOLD_READBACK_MISMATCH")
    return len(picked), len(done)


RELEASE_AT = datetime(2026, 10, 26, 0, 5)  # UTC
EARLY_AT = datetime(2026, 10, 25, 23, 25)  # UTC
NEEDS_READER = ("jd_thin", "description_empty", "description_empty_tf")


def release(cursor, now, live):
    """Give worthwhile jobs on HOLD before RELEASE_AT their recovery retry now, once each. -> (candidates, released).

    An employer-board job (web:*) with no final link goes back to RESOLVED on its own page.
    """
    if now < EARLY_AT:
        return 0, 0
    cursor.execute("SELECT id, lane, posted_date, first_seen, unresolved_reason, final_apply_url IS NOT NULL, source, dedupe_key FROM v7_jobs WHERE status='HOLD' AND recovered_at IS NULL "
                   "AND source_url IS NOT NULL AND updated_at < %s ORDER BY updated_at, id", (RELEASE_AT,))
    picked = [(row[0], row[7]) for row in cursor.fetchall() if worthwhile(row[1], row[2], row[3], row[4], now) and (now >= RELEASE_AT or row[4] not in NEEDS_READER)
              and (row[5] or row[6] in RESOLVED_BY or (str(row[6]).startswith("web:") and row[6] != "web:openjobs"))]
    done = []
    for job_id, key in picked if live else ():
        cursor.execute("UPDATE v7_jobs SET status=CASE WHEN final_apply_url IS NOT NULL OR source LIKE 'web:%%' THEN 'RESOLVED' ELSE 'NEW' END, "
                       "final_apply_url=CASE WHEN final_apply_url IS NULL AND source LIKE 'web:%%' THEN source_url ELSE final_apply_url END, unresolved_reason='recovery_retry', recovered_at=%s, updated_at=%s, "
                       "resolve_attempts=%s, enrich_attempts=%s WHERE id=%s AND status='HOLD' AND recovered_at IS NULL",
                       (now, now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1, job_id))
        if cursor.rowcount:
            done.append((job_id, key))
    if done:  # read-back
        cursor.execute("SELECT id, dedupe_key FROM v7_jobs WHERE id IN (" + ",".join(["%s"] * len(done)) + ") AND status IN ('NEW', 'RESOLVED') AND unresolved_reason='recovery_retry' "
                       "AND recovered_at=%s AND resolve_attempts=%s AND enrich_attempts=%s", (*[d[0] for d in done], now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1))
        if sorted(tuple(r) for r in cursor.fetchall()) != sorted(done):
            raise store.StoreError("HOLD_READBACK_MISMATCH")
    return len(picked), len(done)


MISMATCH_AT = datetime(2026, 10, 31, 10, 0)  # holds for link_mismatch before this get one retry
LINKEDIN_VIEW = "https://www.linkedin.com/jobs/view/"


def mismatch_release(cursor, now, live):
    """Give worthwhile jobs held for link_mismatch before MISMATCH_AT one recovery retry. -> (candidates, released).

    A LinkedIn row takes its own LinkedIn posting as an aggregator link; another row a resolve stage
    reads goes back to NEW.
    """
    from lifeos.jobs.resolve.aggregators import li_apply                     # noqa: PLC0415
    cursor.execute("SELECT id, lane, posted_date, first_seen, source, source_url, dedupe_key FROM v7_jobs WHERE status='HOLD' AND recovered_at IS NULL "
                   "AND unresolved_reason='link_mismatch' AND updated_at < %s ORDER BY updated_at, id", (MISMATCH_AT,))
    picked = []
    for job_id, lane, posted, first_seen, source, source_url, key in cursor.fetchall():
        if not worthwhile(lane, posted, first_seen, "link_mismatch", now):
            continue
        own = li_apply.job_id(source_url) if str(source or "").startswith("linkedin") and li_apply.is_linkedin(source_url or "") else None
        if own:
            cursor.execute("SELECT id FROM v7_jobs WHERE final_apply_url=%s AND id<>%s LIMIT 1", (LINKEDIN_VIEW + own, job_id))
            if not cursor.fetchone():
                picked.append((job_id, key, LINKEDIN_VIEW + own))
        elif source in RESOLVED_BY:
            picked.append((job_id, key, None))
    done = []
    for job_id, key, link in picked if live else ():
        if link:
            cursor.execute("UPDATE v7_jobs SET status='RESOLVED', final_apply_url=%s, apply_kind='aggregator', link_proof=NULL, unresolved_reason='recovery_retry', recovered_at=%s, "
                           "updated_at=%s, resolve_attempts=%s, enrich_attempts=%s WHERE id=%s AND status='HOLD' AND recovered_at IS NULL",
                           (link, now, now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1, job_id))
        else:
            cursor.execute("UPDATE v7_jobs SET status='NEW', final_apply_url=NULL, apply_kind=NULL, link_proof=NULL, unresolved_reason='recovery_retry', recovered_at=%s, updated_at=%s, "
                           "resolve_attempts=%s, enrich_attempts=%s WHERE id=%s AND status='HOLD' AND recovered_at IS NULL",
                           (now, now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1, job_id))
        if cursor.rowcount:
            done.append((job_id, key))
    if done:
        cursor.execute("SELECT id, dedupe_key FROM v7_jobs WHERE id IN (" + ",".join(["%s"] * len(done)) + ") AND status IN ('NEW', 'RESOLVED') AND unresolved_reason='recovery_retry' "
                       "AND recovered_at=%s AND resolve_attempts=%s AND enrich_attempts=%s", (*[d[0] for d in done], now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1))
        if sorted(tuple(r) for r in cursor.fetchall()) != sorted(done):
            raise store.StoreError("HOLD_READBACK_MISMATCH")
    return len(picked), len(done)


DUE = "((status='HOLD' AND (updated_at < %s OR recovered_at IS NOT NULL)) OR (status IN ('NEW', 'RESOLVED') AND recovered_at < %s))"


def run(limit, live, now=None):
    from lifeos.jobs.retention import _now                                  # noqa: PLC0415
    # DATETIME keeps whole seconds
    now = (now or _now()).replace(microsecond=0)
    due_at = (now - timedelta(days=HOLD_DAYS), now - timedelta(hours=RECOVERY_HOURS))
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            candidates, requeued = recover(cursor, now, live)
            release_candidates, released = release(cursor, now, live)
            mismatch_candidates, mismatch_released = mismatch_release(cursor, now, live)
            cursor.execute("SELECT COUNT(*), COALESCE(SUM(unresolved_reason IS NULL), 0), COALESCE(SUM(recovered_at IS NOT NULL), 0) FROM v7_jobs WHERE " + DUE, due_at)
            due, no_reason, failed_due = (int(x or 0) for x in cursor.fetchone())
            cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE status='HOLD'")
            holding = int(cursor.fetchone()[0] or 0)
            cursor.execute("SELECT COALESCE(unresolved_reason, 'none'), COALESCE(source, 'unknown'), COUNT(*) FROM v7_jobs WHERE status='HOLD' GROUP BY 1, 2")
            by_reason, by_source, matrix = {}, {}, {}
            for reason, source, n in cursor.fetchall():
                by_reason[reason] = by_reason.get(reason, 0) + int(n)
                by_source[source] = by_source.get(source, 0) + int(n)
                matrix.setdefault(source, {})[reason] = int(n)
            retired = {}
            for source, reason in RETIRED.items():
                cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE source=%s AND status IN ('NEW', 'HOLD')", (source,))
                open_rows = int(cursor.fetchone()[0] or 0)
                retired[reason] = open_rows
                if live and open_rows:
                    cursor.execute("UPDATE v7_jobs SET status=%s, unresolved_reason=%s, updated_at=%s WHERE source=%s AND status IN ('NEW', 'HOLD')", (EXCLUDED, reason, now, source))
                    cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE source=%s AND status IN ('NEW', 'HOLD')", (source,))
                    if int(cursor.fetchone()[0] or 0):
                        raise store.StoreError("HOLD_RETIRE_READBACK_MISMATCH")
            excluded = 0
            if live and due:
                cursor.execute("UPDATE v7_jobs SET status=%s, unresolved_reason=CASE WHEN recovered_at IS NULL THEN %s ELSE %s END, updated_at=%s WHERE " + DUE,
                               (EXCLUDED, REASON, FAILED_RECOVERY, now, *due_at))
                excluded = int(cursor.rowcount or 0)
                cursor.execute("SELECT COUNT(*), COALESCE(SUM(unresolved_reason=%s), 0) FROM v7_jobs WHERE status=%s AND updated_at=%s AND unresolved_reason IN (%s, %s)", (FAILED_RECOVERY, EXCLUDED, now, REASON, FAILED_RECOVERY))
                if tuple(int(x or 0) for x in cursor.fetchone()) != (due, failed_due) or excluded != due:
                    raise store.StoreError("HOLD_READBACK_MISMATCH")
    return {"on_hold": holding, "due": due, "excluded": excluded, "by_reason": by_reason, "by_source": by_source, "by_source_reason": matrix, "saved": bool(live),
            "recovery_candidates": candidates, "recovered": requeued, "retired": retired, "release_candidates": release_candidates, "released": released,
            "mismatch_candidates": mismatch_candidates, "mismatch_released": mismatch_released}
