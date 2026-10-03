"""Pipeline funnel report (D82): where each provider's jobs end up, and how Fit and the lane policy sort them. Read-only, counts only.
For jobs first seen in the last 24 hours and the last 7 days: per source the count by job status (found -> resolved -> ready -> published, or the
terminal reason), per source the Fit admission by score band and lane reason, and per source how long since a job was last found and last published
(a provider that stops finding or publishing shows up as a growing number). Sources, statuses and reasons are fixed codes; no title, company or link."""
import re
from datetime import timedelta

from lifeos.jobs import store

WINDOWS = (24, 168)
BANDS = ((60, "lt60"), (68, "60_67"), (72, "68_71"), (80, "72_79"))      # exclusive upper edges; 80 and up is "80_up"
_DIGITS = re.compile(r"\d+")


def band(score):
    if score is None:
        return "unscored"
    return next((name for edge, name in BANDS if score < edge), "80_up")


def reason_code(reason):
    return _DIGITS.sub("N", reason or "")[:60] or "none"          # "Fit 67 below 68" -> "Fit N below N"


def _add(table, *keys, n):
    for key in keys[:-1]:
        table = table.setdefault(key, {})
    table[keys[-1]] = table.get(keys[-1], 0) + int(n)


def run(limit, live, now=None):
    from lifeos.jobs.retention import _now                                  # noqa: PLC0415 - one clock for the jobs stages
    now = now or _now()
    out = {"windows": {}, "freshness_hours": {}, "saved": False}
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            for hours in WINDOWS:
                since = now - timedelta(hours=hours)
                cursor.execute("SELECT COALESCE(source, 'unknown'), status, COUNT(*) FROM v7_jobs WHERE first_seen >= %s GROUP BY 1, 2", (since,))
                status = {}
                for source, st, n in cursor.fetchall():
                    _add(status, source, st, n=n)
                cursor.execute("SELECT COALESCE(j.source, 'unknown'), f.admission, f.admission_reason, f.score, COUNT(*) FROM v7_jobs j"
                               " JOIN v7_job_fit f ON f.job_id = j.id WHERE j.first_seen >= %s GROUP BY 1, 2, 3, 4", (since,))
                fit_bands, fit_reasons = {}, {}
                for source, admission, reason, score, n in cursor.fetchall():
                    _add(fit_bands, source, f"{admission or 'none'}:{band(score)}", n=n)
                    _add(fit_reasons, source, f"{admission or 'none'}:{reason_code(reason)}", n=n)
                out["windows"][f"{hours}h"] = {"status": status, "fit_by_band": fit_bands, "fit_by_reason": fit_reasons}
            cursor.execute("SELECT COALESCE(source, 'unknown'), TIMESTAMPDIFF(HOUR, MAX(first_seen), %s),"
                           " TIMESTAMPDIFF(HOUR, MAX(CASE WHEN status = 'PUBLISHED' THEN updated_at END), %s) FROM v7_jobs GROUP BY 1", (now, now))
            for source, found, published in cursor.fetchall():
                out["freshness_hours"][source] = {"since_found": None if found is None else int(found),
                                                  "since_published": None if published is None else int(published)}
    return out
