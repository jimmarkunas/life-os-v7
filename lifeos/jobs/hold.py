"""Jobs that sit on HOLD for a week are excluded, so the newsletter mail that holds them open can close.
HOLD means retries ran out (a link that never resolves, a page that never reads, a link rejected again and again). Nothing retries a HOLD job, so
it never reaches an outcome by itself, and a newsletter mail closes only when every job from it has one (D-finalize). After HOLD_DAYS the job
becomes EXCLUDED_UNRESOLVED: terminal, never published, never retried. Counts only.

D185 recovery (docs/DECISIONS.md): a worthwhile HOLD job gets ONE more try through the normal path, from day 6; one still HOLD, NEW or RESOLVED
RECOVERY_HOURS after its try is excluded as `unresolved_after_recovery`, so the latest exclusion is day 7 and the seven-day rule is unchanged."""
from datetime import datetime, timedelta

from lifeos.jobs import lanes, store
from lifeos.platform import limits

HOLD_DAYS = 7
RECOVER_DAYS = 6               # a hold is recovered from day 6 ...
RECOVERY_HOURS = 12            # ... for this long, and a recovered job has this long to leave NEW / RESOLVED: 6 days + 12 h + 12 h = the seven days
RECOVER_PER_RUN = 25
EXCLUDED = "EXCLUDED_UNRESOLVED"
REASON = "hold_expired"
FAILED_RECOVERY = "unresolved_after_recovery"      # the alert counts this reason
RESOLVED_BY = ("jobright", "linkedin-alerts", "linkedin-search", "dice")       # the sources a resolve stage reads (lifeos/run.py); a NEW job of any other source would wait for nothing
RETIRED = {"lensa": "source_retired_lensa"}               # D201: a retired source's NEW or HOLD jobs end EXCLUDED_UNRESOLVED with this reason (its RESOLVED jobs carry on)


def worthwhile(lane, posted, first_seen, reason, now):
    if str(reason or "").startswith(("closed", "dup")):
        return False
    policy = lanes.POLICIES.get(lanes.lane_for(lane))                          # still inside its lane's admission age (what Fit would apply anyway); an unknown lane is not worthwhile (fail closed)
    return bool(policy) and (now.date() - (posted or first_seen.date())).days <= policy.max_age_days


def recover(cursor, now, live):
    """Requeue the worthwhile HOLD jobs of day 6, once each (a dry run only counts). -> (candidates, requeued)."""
    cursor.execute("SELECT id, lane, posted_date, first_seen, unresolved_reason, final_apply_url IS NOT NULL, source, dedupe_key FROM v7_jobs WHERE status='HOLD' AND recovered_at IS NULL AND source_url IS NOT NULL "
                   "AND updated_at < %s AND updated_at >= %s ORDER BY updated_at, id", (now - timedelta(days=RECOVER_DAYS), now - timedelta(days=RECOVER_DAYS, hours=RECOVERY_HOURS)))
    picked = [(row[0], row[7]) for row in cursor.fetchall() if worthwhile(row[1], row[2], row[3], row[4], now) and (row[5] or row[6] in RESOLVED_BY)]
    done = []
    for job_id, key in picked[:RECOVER_PER_RUN] if live else ():
        cursor.execute("UPDATE v7_jobs SET status=CASE WHEN final_apply_url IS NULL THEN 'NEW' ELSE 'RESOLVED' END, unresolved_reason='recovery_retry', recovered_at=%s, updated_at=%s, "
                       "resolve_attempts=%s, enrich_attempts=%s WHERE id=%s AND status='HOLD' AND recovered_at IS NULL",       # one attempt left on either counter
                       (now, now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1, job_id))
        if cursor.rowcount:
            done.append((job_id, key))
    if done:                                                                  # read-back: the same jobs (id and dedupe key), in the state, reason, time and counters written
        cursor.execute("SELECT id, dedupe_key FROM v7_jobs WHERE id IN (" + ",".join(["%s"] * len(done)) + ") AND status=CASE WHEN final_apply_url IS NULL THEN 'NEW' ELSE 'RESOLVED' END AND unresolved_reason='recovery_retry' "
                       "AND recovered_at=%s AND resolve_attempts=%s AND enrich_attempts=%s", (*[d[0] for d in done], now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1))
        if sorted(tuple(r) for r in cursor.fetchall()) != sorted(done):
            raise store.StoreError("HOLD_READBACK_MISMATCH")
    return len(picked), len(done)


RELEASE_AT = datetime(2026, 10, 26, 0, 5)       # Jim, Oct 25 2026 ~6:00 PM CT: "Fix those 77 jobs on hold immediately". 7:05 PM CT, after the TinyFish Fetch day cap resets (UTC midnight)
EARLY_AT = datetime(2026, 10, 25, 23, 25)        # Jim, 6:20 PM CT: "solve this in the next 20 minutes": a hold no TinyFish read can help (a wrong link, a refused page) goes at 6:25 PM CT
NEEDS_READER = ("jd_thin", "description_empty", "description_empty_tf")     # these wait for 7:05 PM CT: retried before the cap resets they would fail again and be excluded


def release(cursor, now, live):
    """One-off (Jim, Oct 25): every worthwhile job that went on HOLD before RELEASE_AT gets its D185 retry now instead of on day 6, once each (recovered_at),
    through the normal path, so it reaches an outcome tonight; one that fails again is excluded as `unresolved_after_recovery` by the same rules.
    An employer-board job (web:*) with no final link goes back to RESOLVED on its own page (enrich proves the link again). -> (candidates, released)."""
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
    if done:                                                                  # read-back: the same jobs, out of HOLD, with the reason, time and counters written
        cursor.execute("SELECT id, dedupe_key FROM v7_jobs WHERE id IN (" + ",".join(["%s"] * len(done)) + ") AND status IN ('NEW', 'RESOLVED') AND unresolved_reason='recovery_retry' "
                       "AND recovered_at=%s AND resolve_attempts=%s AND enrich_attempts=%s", (*[d[0] for d in done], now, limits.RESOLVE_MAX_ATTEMPTS - 1, limits.ENRICH_MAX_ATTEMPTS - 1))
        if sorted(tuple(r) for r in cursor.fetchall()) != sorted(done):
            raise store.StoreError("HOLD_READBACK_MISMATCH")
    return len(picked), len(done)


DUE = "((status='HOLD' AND (updated_at < %s OR recovered_at IS NOT NULL)) OR (status IN ('NEW', 'RESOLVED') AND recovered_at < %s))"       # (hold cutoff, recovery deadline)


def run(limit, live, now=None):
    from lifeos.jobs.retention import _now                                  # noqa: PLC0415 - one clock for the jobs stages
    now = (now or _now()).replace(microsecond=0)                           # the DATETIME columns keep whole seconds, so the read-back compares the value they store
    due_at = (now - timedelta(days=HOLD_DAYS), now - timedelta(hours=RECOVERY_HOURS))
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            candidates, requeued = recover(cursor, now, live)
            release_candidates, released = release(cursor, now, live)
            cursor.execute("SELECT COUNT(*), COALESCE(SUM(unresolved_reason IS NULL), 0), COALESCE(SUM(recovered_at IS NOT NULL), 0) FROM v7_jobs WHERE " + DUE, due_at)
            due, no_reason, failed_due = (int(x or 0) for x in cursor.fetchone())
            cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE status='HOLD'")
            holding = int(cursor.fetchone()[0] or 0)
            cursor.execute("SELECT COALESCE(unresolved_reason, 'none'), COALESCE(source, 'unknown'), COUNT(*) FROM v7_jobs WHERE status='HOLD' GROUP BY 1, 2")
            by_reason, by_source, matrix = {}, {}, {}
            for reason, source, n in cursor.fetchall():                 # fixed reason codes and producer names, counts only
                by_reason[reason] = by_reason.get(reason, 0) + int(n)
                by_source[source] = by_source.get(source, 0) + int(n)
                matrix.setdefault(source, {})[reason] = int(n)           # where each producer loses its jobs
            retired = {}
            for source, reason in RETIRED.items():
                cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE source=%s AND status IN ('NEW', 'HOLD')", (source,))
                open_rows = int(cursor.fetchone()[0] or 0)
                retired[reason] = open_rows
                if live and open_rows:
                    cursor.execute("UPDATE v7_jobs SET status=%s, unresolved_reason=%s, updated_at=%s WHERE source=%s AND status IN ('NEW', 'HOLD')", (EXCLUDED, reason, now, source))
                    cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE source=%s AND status IN ('NEW', 'HOLD')", (source,))
                    if int(cursor.fetchone()[0] or 0):                  # read-back: none of the retired source's open jobs is left open
                        raise store.StoreError("HOLD_RETIRE_READBACK_MISMATCH")
            excluded = 0
            if live and due:
                cursor.execute("UPDATE v7_jobs SET status=%s, unresolved_reason=CASE WHEN recovered_at IS NULL THEN %s ELSE %s END, updated_at=%s WHERE " + DUE,
                               (EXCLUDED, REASON, FAILED_RECOVERY, now, *due_at))
                excluded = int(cursor.rowcount or 0)
                cursor.execute("SELECT COUNT(*), COALESCE(SUM(unresolved_reason=%s), 0) FROM v7_jobs WHERE status=%s AND updated_at=%s AND unresolved_reason IN (%s, %s)", (FAILED_RECOVERY, EXCLUDED, now, REASON, FAILED_RECOVERY))
                if tuple(int(x or 0) for x in cursor.fetchone()) != (due, failed_due) or excluded != due:         # read-back: every due job is now terminal with its reason, as many as were counted
                    raise store.StoreError("HOLD_READBACK_MISMATCH")
    return {"on_hold": holding, "due": due, "excluded": excluded, "by_reason": by_reason, "by_source": by_source, "by_source_reason": matrix, "saved": bool(live),
            "recovery_candidates": candidates, "recovered": requeued, "retired": retired, "release_candidates": release_candidates, "released": released}
