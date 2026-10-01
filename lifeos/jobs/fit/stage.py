"""The `fit` stage: score every job that has a full description and no current score. Counts only in the log.

Reads and writes are separate short connections; scoring is pure CPU between them. A job is re-scored when the model
version, the private profile or the description changes. With V7_FIT_GATE=true a No-Go READY job becomes EXCLUDED_FIT
(never published); without it the score is recorded in shadow mode and nothing about publishing changes.
"""
from datetime import datetime, timezone
import json
import os

from lifeos.jobs import store
from lifeos.jobs.fit import MODEL_VERSION, profile as fit_profile
from lifeos.jobs.fit.score import evaluate

PICK = ("SELECT j.id, j.title, j.company, d.full_text, d.fingerprint FROM v7_jobs j"
        " JOIN v7_job_descriptions d ON d.job_id = j.id LEFT JOIN v7_job_fit f ON f.job_id = j.id"
        " WHERE j.status IN ('READY', 'RESOLVED') AND (f.job_id IS NULL OR f.model_version <> %s OR f.profile_hash <> %s"
        " OR f.jd_fingerprint <> d.fingerprint) ORDER BY j.first_seen LIMIT %s")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def score_rows(rows, profile, today):
    """[(job_id, fingerprint, Result)] for rows of (id, title, company, full_text, fingerprint). Pure."""
    return [(job_id, fingerprint, evaluate(title, company, text, profile, today))
            for job_id, title, company, text, fingerprint in rows]


def run(limit, live, environ=os.environ):
    counts = {"picked": 0, "go": 0, "no_go": 0, "excluded": 0, "no_data": 0, "low_confidence": 0, "gated": 0, "profile": "ok"}
    try:
        profile = fit_profile.load(environ)
    except fit_profile.ProfileError:
        counts["profile"] = "missing"
        return counts
    gate = environ.get("V7_FIT_GATE") == "true"
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(PICK, (MODEL_VERSION, profile.hash, limit))
            rows = cursor.fetchall()
    counts["picked"] = len(rows)
    scored = score_rows(rows, profile, _now().date())
    for _, _, result in scored:
        key = {"Go": "go", "No-Go": "no_go", "No-Data": "no_data"}[result.decision]
        counts[key] += 1
        counts["excluded"] += bool(result.exclusion)
        counts["low_confidence"] += result.confidence == "low" and result.decision != "No-Data"
    if live and scored:
        with store.connect() as connection, connection.cursor() as cursor:
            for job_id, fingerprint, r in scored:
                cursor.execute(
                    "REPLACE INTO v7_job_fit (job_id, score, decision, line, why, exclusion, confidence, buckets, trace,"
                    " model_version, profile_hash, jd_fingerprint, scored_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (job_id, r.score, r.decision, r.line[:600], r.why[:300], r.exclusion, r.confidence,
                     json.dumps(r.buckets), json.dumps(r.items), MODEL_VERSION, profile.hash, fingerprint, _now()))
                if gate and r.decision == "No-Go":
                    cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT', unresolved_reason=%s, updated_at=%s"
                                   " WHERE id=%s AND status='READY'", ("fit_no_go", _now(), job_id))
                    counts["gated"] += cursor.rowcount
    return counts
