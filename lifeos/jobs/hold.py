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
            excluded = 0
            if live and due:
                cursor.execute("UPDATE v7_jobs SET status=%s, unresolved_reason=%s, updated_at=%s WHERE status='HOLD' AND updated_at < %s",
                               (EXCLUDED, REASON, now, cutoff))
                excluded = int(cursor.rowcount or 0)
    return {"on_hold": holding, "due": due, "excluded": excluded, "saved": bool(live)}
