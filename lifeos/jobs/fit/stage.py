"""The `fit` stage: score every job that has a full description and no current score. Counts only in the log.

Reads and writes are separate short connections; scoring is pure CPU between them. A job is re-scored when the model
version, the private profile or the description changes. Each job is then judged by its lane policy (lifeos.jobs.lanes: Fit 72, work mode, explicit pay, age; a Newsletter job is judged
as US Remote) and the decision is stored. With V7_FIT_GATE=true an EXCLUDE READY job becomes EXCLUDED_FIT (never published) and
only ADMIT / REVIEW publish; without it everything is recorded in shadow mode and publishing is unchanged.
"""
from datetime import datetime, timezone
import json
import os

from lifeos.jobs import lanes, store
from lifeos.jobs.fit import MODEL_VERSION, profile as fit_profile, semantic as fit_semantic
from lifeos.jobs.fit.score import evaluate

PICK = ("SELECT j.id, j.title, j.company, d.full_text, d.fingerprint, j.lane, j.location_text, j.salary_text,"
        " j.posted_date, j.first_seen FROM v7_jobs j"
        " JOIN v7_job_descriptions d ON d.job_id = j.id LEFT JOIN v7_job_fit f ON f.job_id = j.id"
        " WHERE j.status IN ('READY', 'RESOLVED') AND (f.job_id IS NULL OR f.model_version <> %s OR f.profile_hash <> %s"
        " OR f.jd_fingerprint <> d.fingerprint) ORDER BY j.first_seen LIMIT %s")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def score_rows(rows, profile, today, semantic=None):
    """[(job_id, fingerprint, Result, Decision, lane, work_mode)] for rows of (id, title, company, full_text, fingerprint,
    [lane, location, salary_text, posted_date, first_seen]). Pure."""
    out = []
    for job_id, title, company, text, fingerprint, *rest in rows:
        lane, location, salary, posted, first_seen = (list(rest) + [None] * 5)[:5]
        result = evaluate(title, company, text, profile, today, semantic)
        facts = lanes.facts_for(result.score, title, location, text, salary, posted, first_seen or today)
        decision, lane_name = lanes.decide(lane, facts, today, result.exclusion)
        out.append((job_id, fingerprint, result, decision, lane_name, facts.work_mode))
    return out


def run(limit, live, environ=os.environ):
    counts = {"picked": 0, "go": 0, "no_go": 0, "excluded": 0, "unscorable": 0, "low_confidence": 0, "gated": 0, "profile": "ok",
              "semantic": "off", "lane_admit": 0, "lane_review": 0, "lane_exclude": 0, "shadow_jobs_changed": 0, "shadow_flips": 0, "shadow_reclassified": 0,
              "sim_72_77": 0, "sim_77_82": 0, "sim_82_up": 0}
    try:
        profile = fit_profile.load(environ)
    except fit_profile.ProfileError:
        counts["profile"] = "missing"
        return counts
    gate = environ.get("V7_FIT_GATE") == "true"
    embed = fit_semantic.load_embedder(environ)
    matcher = fit_semantic.Matcher(profile, embed) if embed else None      # shadow only: never changes a score
    counts["semantic"] = "on" if matcher else "off"
    tag = profile.hash[:12] + lanes.POLICY_VERSION + ("s1" if matcher else "")  # a profile, lane-policy or semantic change re-scores
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(PICK, (MODEL_VERSION, tag, limit))
            rows = cursor.fetchall()
    counts["picked"] = len(rows)
    scored = score_rows(rows, profile, _now().date(), matcher)
    for _, _, result, decision, _, _ in scored:
        counts["lane_" + decision.status.lower()] += 1
        key = {"Go": "go", "No-Go": "no_go", "Unscorable": "unscorable"}[result.decision]
        counts[key] += 1
        counts["excluded"] += bool(result.exclusion)
        counts["low_confidence"] += result.confidence == "low" and result.decision != "Unscorable"
        counts["shadow_jobs_changed"] += result.shadow_changes > 0
        counts["shadow_flips"] += result.shadow_flip
        counts["shadow_reclassified"] += result.shadow_changes
        for sim in result.shadow_sims:
            counts["sim_82_up" if sim >= 0.82 else "sim_77_82" if sim >= 0.77 else "sim_72_77"] += 1
    if live and scored:
        with store.connect() as connection, connection.cursor() as cursor:
            for job_id, fingerprint, r, decision, lane_name, work_mode in scored:
                cursor.execute(
                    "REPLACE INTO v7_job_fit (job_id, score, decision, line, why, exclusion, confidence, buckets, trace,"
                    " model_version, profile_hash, jd_fingerprint, scored_at, shadow_score, shadow_changes,"
                    " admission, admission_reason, work_mode, lane)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (job_id, r.score, r.decision, r.line[:600], r.why[:300], r.exclusion, r.confidence,
                     json.dumps(r.buckets), json.dumps(r.items), MODEL_VERSION, tag, fingerprint, _now(), r.shadow_score, r.shadow_changes,
                     decision.status, (decision.reason or "")[:80], work_mode, lane_name))
                if gate and decision.status == lanes.EXCLUDE:
                    cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT', unresolved_reason=%s, updated_at=%s"
                                   " WHERE id=%s AND status='READY'", ("lane_exclude", _now(), job_id))
                    counts["gated"] += cursor.rowcount
    return counts
