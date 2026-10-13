"""Jobs that sit on HOLD for a week are excluded, so the newsletter mail that holds them open can close.
HOLD means retries ran out (a link that never resolves, a page that never reads, a link rejected again and again). Nothing retries a HOLD job, so
it never reaches an outcome by itself, and a newsletter mail closes only when every job from it has one (D-finalize). After HOLD_DAYS the job
becomes EXCLUDED_UNRESOLVED: terminal, never published, never retried. Counts only.

D185 recovery: a worthwhile HOLD job (still inside its lane's freshness window, link to retry, not closed) gets ONE more try on day 6, through the normal path:
back to NEW (no final link) or RESOLVED (link, unread page) with exactly one attempt left, so resolve/enrich/Fit/publish judge it as they judge any job. The try is
recorded in `recovered_at`; a recovered job that is on HOLD again is excluded at once (`unresolved_after_recovery`), so it never outlives the seven days."""
from datetime import timedelta

from lifeos.jobs import lanes, store
from lifeos.platform import limits

HOLD_DAYS = 7
RECOVER_DAYS = 6               # one day before the exclusion; the seven-day rule itself is unchanged
RECOVER_PER_RUN = 25
EXCLUDED = "EXCLUDED_UNRESOLVED"
REASON = "hold_expired"
FAILED_RECOVERY = "unresolved_after_recovery"      # the alert counts this reason


def worthwhile(lane, posted, first_seen, reason, now):
    """Still inside its lane's admission window (the age Fit would apply anyway) and not an explicit closed / duplicate outcome."""
    if str(reason or "").startswith(("closed", "dup")):
        return False
    policy = lanes.POLICIES.get(lanes.lane_for(lane))                          # an unknown lane is not worthwhile (fail closed)
    return bool(policy) and (now.date() - (posted or first_seen.date())).days <= policy.max_age_days


def recover(cursor, now, live):
    """Requeue the worthwhile HOLD jobs of day 6, once each (a dry run only counts). -> (candidates, requeued)."""
    cursor.execute("SELECT id, lane, posted_date, first_seen, unresolved_reason FROM v7_jobs WHERE status='HOLD' AND recovered_at IS NULL AND source_url IS NOT NULL "
                   "AND updated_at < %s AND updated_at >= %s ORDER BY updated_at, id", (now - timedelta(days=RECOVER_DAYS), now - timedelta(days=HOLD_DAYS)))
    picked = [row[0] for row in cursor.fetchall() if worthwhile(row[1], row[2], row[3], row[4], now)]
    requeued = 0
    for job_id in picked[:RECOVER_PER_RUN] if live else ():
        cursor.execute("UPDATE v7_jobs SET status=CASE WHEN final_apply_url IS NULL THEN 'NEW' ELSE 'RESOLVED' END, unresolved_reason='recovery_retry', recovered_at=%s, updated_at=%s, "
                       "resolve_attempts=%s, enrich_attempts=%s WHERE id=%s AND status='HOLD' AND recovered_at IS NULL",       # one attempt left on either counter
                       (now, now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1, job_id))
        requeued += int(cursor.rowcount or 0)
    return len(picked), requeued


def run(limit, live, now=None):
    from lifeos.jobs.retention import _now                                  # noqa: PLC0415 - one clock for the jobs stages
    now = now or _now()
    cutoff = now - timedelta(days=HOLD_DAYS)
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            candidates, requeued = recover(cursor, now, live)
            cursor.execute("SELECT COUNT(*), COALESCE(SUM(unresolved_reason IS NULL), 0) FROM v7_jobs WHERE status='HOLD' AND (updated_at < %s OR recovered_at IS NOT NULL)", (cutoff,))
            due, no_reason = (int(x or 0) for x in cursor.fetchone())
            cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE status='HOLD'")
            holding = int(cursor.fetchone()[0] or 0)
            cursor.execute("SELECT COALESCE(unresolved_reason, 'none'), COALESCE(source, 'unknown'), COUNT(*) FROM v7_jobs WHERE status='HOLD' GROUP BY 1, 2")
            by_reason, by_source, matrix = {}, {}, {}
            for reason, source, n in cursor.fetchall():                 # fixed reason codes and producer names, counts only
                by_reason[reason] = by_reason.get(reason, 0) + int(n)
                by_source[source] = by_source.get(source, 0) + int(n)
                matrix.setdefault(source, {})[reason] = int(n)           # where each producer loses its jobs
            excluded = 0
            if live and due:
                cursor.execute("UPDATE v7_jobs SET status=%s, unresolved_reason=CASE WHEN recovered_at IS NULL THEN %s ELSE %s END, updated_at=%s WHERE status='HOLD' AND (updated_at < %s OR recovered_at IS NOT NULL)",
                               (EXCLUDED, REASON, FAILED_RECOVERY, now, cutoff))
                excluded = int(cursor.rowcount or 0)
    return {"on_hold": holding, "due": due, "excluded": excluded, "by_reason": by_reason, "by_source": by_source, "by_source_reason": matrix, "saved": bool(live),
            "recovery_candidates": candidates, "recovered": requeued}
