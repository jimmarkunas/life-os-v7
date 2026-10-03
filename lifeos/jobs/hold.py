"""Jobs that sit on HOLD for a week are excluded, so the newsletter mail that holds them open can close.
HOLD means retries ran out (a link that never resolves, a page that never reads, a link rejected again and again). Nothing retries a HOLD job, so
it never reaches an outcome by itself, and a newsletter mail closes only when every job from it has one (D-finalize). After HOLD_DAYS the job
becomes EXCLUDED_UNRESOLVED: terminal, never published, never retried. Counts only."""
from datetime import timedelta

from lifeos.jobs import store

HOLD_DAYS = 7
EXCLUDED = "EXCLUDED_UNRESOLVED"
REASON = "hold_expired"


def run(limit, live, now=None):
    from lifeos.jobs.retention import _now                                  # noqa: PLC0415 - one clock for the jobs stages
    now = now or _now()
    cutoff = now - timedelta(days=HOLD_DAYS)
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*), COALESCE(SUM(unresolved_reason IS NULL), 0) FROM v7_jobs WHERE status='HOLD' AND updated_at < %s", (cutoff,))
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
                cursor.execute("UPDATE v7_jobs SET status=%s, unresolved_reason=%s, updated_at=%s WHERE status='HOLD' AND updated_at < %s",
                               (EXCLUDED, REASON, now, cutoff))
                excluded = int(cursor.rowcount or 0)
    return {"on_hold": holding, "due": due, "excluded": excluded, "by_reason": by_reason, "by_source": by_source, "by_source_reason": matrix, "saved": bool(live)}
