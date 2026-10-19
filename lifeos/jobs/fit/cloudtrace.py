"""LIFE-41: counts-only Cloud Fit trace for the manual diag workflow. Reads the private `cloud_*` columns of v7_job_fit and prints only fixed status and reason codes, score bands
and per-day counts: no job id, title, employer, rationale, score value or text. Read-only (SELECT-only guard), no model call, no Notion, no write; never on a tick. It answers "did the
last Cloud Fit batch ask, skip or hold, and how did the scores fall", not "which job": the job's identity stays inside the store."""
import re

from lifeos.jobs import store

CODE = re.compile(r"[A-Z][A-Z0-9_]{0,39}")                                           # a status or reason code; anything else prints as OTHER, never as text
BANDS = ((25, "0_24"), (50, "25_49"), (75, "50_74"), (101, "75_100"))
DAYS = 7


def _code(value):
    return "" if value is None else value if CODE.fullmatch(str(value)) else "OTHER"


def _band(score):
    return next(name for limit, name in BANDS if score < limit)


def _select(cursor, sql, args=()):
    if not sql.lstrip().upper().startswith("SELECT"):
        raise store.StoreError("CLOUD_TRACE_NOT_READ_ONLY")
    cursor.execute(sql, args)
    return cursor.fetchall()


def run(limit, live):
    out = {"by_status": {}, "score_bands": {}, "by_day": {}, "held_published": 0, "never_asked": 0}
    with store.connect() as connection, connection.cursor() as cursor:
        for status, reason, count in _select(cursor, "SELECT cloud_status, cloud_reason, COUNT(*) FROM v7_job_fit WHERE cloud_status IS NOT NULL GROUP BY cloud_status, cloud_reason"):
            key = f"{_code(status)}|{_code(reason)}"
            out["by_status"][key] = out["by_status"].get(key, 0) + int(count)
        for status, score, count in _select(cursor, "SELECT cloud_status, cloud_score, COUNT(*) FROM v7_job_fit WHERE cloud_score IS NOT NULL GROUP BY cloud_status, cloud_score"):
            key = f"{_code(status)}|{_band(int(score))}"
            out["score_bands"][key] = out["score_bands"].get(key, 0) + int(count)
        for day, status, count in _select(cursor, "SELECT DATE(cloud_at), cloud_status, COUNT(*) FROM v7_job_fit WHERE cloud_at IS NOT NULL AND cloud_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)"
                                                  " GROUP BY DATE(cloud_at), cloud_status", (DAYS,)):
            key = f"{day}|{_code(status)}"
            out["by_day"][key] = out["by_day"].get(key, 0) + int(count)
        out["held_published"] = int(_select(cursor, "SELECT COUNT(*) FROM v7_job_fit f JOIN v7_jobs j ON j.id = f.job_id WHERE f.cloud_status = 'HELD' AND j.status = 'PUBLISHED'")[0][0])
        out["never_asked"] = int(_select(cursor, "SELECT COUNT(*) FROM v7_job_fit WHERE cloud_status IS NULL AND score IS NOT NULL")[0][0])
    return {k: dict(sorted(v.items())) if isinstance(v, dict) else v for k, v in out.items()}
