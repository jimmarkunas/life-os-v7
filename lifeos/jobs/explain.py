"""Explain: where is every job of one company or source, and why? Counts and fixed codes only.

Input: EXPLAIN_COMPANY (comma separated substrings of a company name or source id, 3+ letters each). Output: jobs
by status and reason code, plus one line per still-open job: a six-character hash of its id (never a title,
company or link), status, Fit score, reason codes and source kind. Writes nothing.
"""

import hashlib
import os

from lifeos.jobs import store
from lifeos.jobs.funnel import public_code

DONE = ("PUBLISHED", "EXCLUDED_FIT", "EXCLUDED_STALE", "CLOSED", "DUPLICATE", "PURGED")
MAX_OPEN = 40


def short(job_id):
    return hashlib.sha1(str(job_id).encode()).hexdigest()[:6]


def summarize(rows):
    by_status, by_reason, open_jobs = {}, {}, []
    for job_id, status, source, score, why, stop in rows:
        by_status[status] = by_status.get(status, 0) + 1
        key = f"{status}: {public_code(why or stop)}"
        by_reason[key] = by_reason.get(key, 0) + 1
        if status not in DONE and len(open_jobs) < MAX_OPEN:
            open_jobs.append(
                {
                    "h": short(job_id),
                    "status": status,
                    "fit": score,
                    "why": public_code(why),
                    "stop": public_code(stop),
                    "via": ((source or "").split(":")[0] or "unknown")[:20],
                }
            )
    return {
        "matched": len(rows),
        "by_status": by_status,
        "by_reason": by_reason,
        "open": open_jobs,
        "open_total": sum(n for s, n in by_status.items() if s not in DONE),
    }


def run(limit, live, environ=os.environ):
    names = [n.strip() for n in (environ.get("EXPLAIN_COMPANY") or "").split(",") if len(n.strip()) >= 3][:12]
    if not names:
        return {"error": "EXPLAIN_COMPANY_missing_or_short"}
    likes = ["%" + n.replace("%", "").replace("_", "") + "%" for n in names]
    where = " OR ".join("(j.company LIKE %s OR j.source LIKE %s)" for _ in likes)
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT j.id, j.status, COALESCE(j.source, ''), f.score, f.admission_reason, j.unresolved_reason FROM v7_jobs j LEFT JOIN v7_job_fit f ON f.job_id = j.id"
                " WHERE (" + where + ") ORDER BY j.id LIMIT 5000",
                tuple(p for like in likes for p in (like, like)),
            )
            rows = [tuple(r) for r in cursor.fetchall()]
    return summarize(rows)
