"""The `fit` stage: score every job that has a full description and no current score. Counts only in the log.

Reads and writes are separate short connections; scoring is pure CPU between them. A job is re-scored when the model
version, the private profile or the description changes. Each job is then judged by its lane policy (lifeos.jobs.lanes: Fit 68, work mode, explicit pay, age; a Newsletter job is judged
as US Remote) and the decision is stored. With V7_FIT_GATE=true an EXCLUDE READY job becomes EXCLUDED_FIT (never published) and
only ADMIT / REVIEW publish; without it everything is recorded in shadow mode and publishing is unchanged.
"""
from datetime import datetime, timezone
import json
import os

from lifeos.jobs import lanes, sponsors, store
from lifeos.jobs.fit import GO_THRESHOLD, MODEL_VERSION, profile as fit_profile, semantic as fit_semantic
from lifeos.jobs.fit.score import evaluate

PICK = ("SELECT j.id, j.title, j.company, d.full_text, d.fingerprint, j.lane, j.location_text, j.salary_text,"
        " j.posted_date, j.first_seen, j.route_evidence FROM v7_jobs j"
        " JOIN v7_job_descriptions d ON d.job_id = j.id LEFT JOIN v7_job_fit f ON f.job_id = j.id"
        " WHERE j.status IN (%s) AND (f.job_id IS NULL OR f.model_version <> %s OR f.profile_hash <> %s"
        " OR f.jd_fingerprint <> d.fingerprint) ORDER BY j.first_seen LIMIT %s")
OPEN = ("'READY'", "'RESOLVED'")
AUTO = OPEN + ("'PUBLISHED'",)                          # D94: every tick also re-scores published jobs whose score is stale (model, policy, profile or description changed); the audit then clears the ones now excluded
ALL = AUTO + ("'EXCLUDED_FIT'",)                         # FIT_ALL=true (by hand): also re-score excluded jobs (calibration)


PROFILE_TAG_WIDTH = 16                                   # v7_job_fit.profile_hash is CHAR(16)


def fit_tag(profile_hash, matcher):
    """What a stored score is compared with: the profile, the lane policy and the semantic layer. It must fit the column: a longer tag was cut on write, never
    matched on read, and so every run re-scored the same oldest jobs and never reached the newer ones (D107). The tail is kept, so a policy change always shows."""
    return (profile_hash[:12] + lanes.POLICY_VERSION + ("s1" if matcher else ""))[-PROFILE_TAG_WIDTH:]


def only_clause(raw):
    """D109: a dispatch may name companies (comma separated, 3+ letters each; the Hourly `report` input) whose stale jobs are scored first. -> (sql, params)."""
    names = [n.strip() for n in (raw or "").split(",") if len(n.strip()) >= 3 and n.strip().lower() != "funnel"][:12]
    if not names:
        return "", ()
    likes = ["%" + n.replace("%", "").replace("_", "") + "%" for n in names]
    sql = " AND (" + " OR ".join("j.company LIKE %s OR j.source LIKE %s" for _ in likes) + ")"
    return sql, tuple(p for like in likes for p in (like, like))


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def score_rows(rows, profile, today, semantic=None, register=None):
    """[(job_id, fingerprint, Result, Decision, lane, work_mode, eligible)] for rows of (id, title, company, full_text, fingerprint,
    [lane, location, salary_text, posted_date, first_seen, route_evidence]). `register`: the sponsor register (Skilled Worker route evidence). Pure."""
    out = []
    for job_id, title, company, text, fingerprint, *rest in rows:
        lane, location, salary, posted, first_seen, route = (list(rest) + [None] * 6)[:6]
        result = evaluate(title, company, text, profile, today, semantic)
        if register is not None:
            route = lanes.join_routes(route, **{sponsors.ROUTE: register.state(company)})
        facts = lanes.facts_for(result.score, title, location, text, salary, posted, first_seen or today, route=route)
        decision, lane_name, eligible = lanes.decide_all(lane, facts, today, result.exclusion)
        out.append((job_id, fingerprint, result, decision, lane_name, facts.work_mode, eligible))
    return out


def requeue_floor(cursor):
    """D84, one time: jobs excluded only for Fit under the old floor (reason text 'Fit N below 72', score 60 and up) go back to READY so the new
    floor and the Review band judge them. The new decision text never says 'below 72', so a job is requeued at most once."""
    cursor.execute("UPDATE v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id SET j.status='READY', j.unresolved_reason='requeued_floor', j.updated_at=%s"
                   " WHERE j.status='EXCLUDED_FIT' AND j.unresolved_reason='lane_exclude' AND j.notion_page_id IS NULL"
                   " AND f.admission_reason LIKE 'Fit %% below 72' AND f.score >= 60", (_now(),))
    return int(cursor.rowcount or 0)


def run(limit, live, environ=os.environ):
    counts = {"picked": 0, "go": 0, "no_go": 0, "excluded": 0, "unscorable": 0, "low_confidence": 0, "gated": 0, "profile": "ok",
              "semantic": "off", "lane_admit": 0, "lane_review": 0, "lane_exclude": 0, "shadow_jobs_changed": 0, "shadow_flips": 0, "shadow_reclassified": 0,
              "sim_72_77": 0, "sim_77_82": 0, "sim_82_up": 0, "fcap_moved": 0, "fcap_go_lost": 0, "revived": 0}
    try:
        profile = fit_profile.load(environ)
    except fit_profile.ProfileError:
        counts["profile"] = "missing"
        return counts
    gate = environ.get("V7_FIT_GATE") == "true"
    embed = fit_semantic.load_embedder(environ)
    matcher = fit_semantic.Matcher(profile, embed) if embed else None      # shadow only: never changes a score
    counts["semantic"] = "on" if matcher else "off"
    tag = fit_tag(profile.hash, bool(matcher))         # a profile, lane-policy or semantic change re-scores
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            counts["requeued"] = requeue_floor(cursor) if live else 0
            extra_sql, extra_params = only_clause(environ.get("FIT_ONLY"))
            query = (PICK % (", ".join(ALL if environ.get("FIT_ALL") == "true" else AUTO), "%s", "%s", "%s")).replace(" ORDER BY j.first_seen", extra_sql + " ORDER BY j.first_seen")
            cursor.execute(query, (MODEL_VERSION, tag, *extra_params, limit))
            rows = cursor.fetchall()
    counts["picked"] = len(rows)
    scored = score_rows(rows, profile, _now().date(), matcher, sponsors.load())      # slow work: no connection is open here
    for _, _, result, decision, _, _, _ in scored:
        counts["lane_" + decision.status.lower()] += 1
        key = {"Go": "go", "No-Go": "no_go", "Unscorable": "unscorable"}[result.decision]
        counts[key] += 1
        counts["excluded"] += bool(result.exclusion)
        if result.score is not None and result.pre_functional is not None and result.pre_functional > result.score:          # D103 calibration: what the Functional cap moved
            counts["fcap_moved"] += 1
            counts["fcap_go_lost"] += result.pre_functional >= GO_THRESHOLD > result.score
        counts["low_confidence"] += result.confidence == "low" and result.decision != "Unscorable"
        counts["shadow_jobs_changed"] += result.shadow_changes > 0
        counts["shadow_flips"] += result.shadow_flip
        counts["shadow_reclassified"] += result.shadow_changes
        for sim in result.shadow_sims:
            counts["sim_82_up" if sim >= 0.82 else "sim_77_82" if sim >= 0.77 else "sim_72_77"] += 1
    if live and scored:
        with store.connect() as connection, connection.cursor() as cursor:
            for job_id, fingerprint, r, decision, lane_name, work_mode, eligible in scored:
                cursor.execute(
                    "REPLACE INTO v7_job_fit (job_id, score, decision, line, why, exclusion, confidence, buckets, trace,"
                    " model_version, profile_hash, jd_fingerprint, scored_at, shadow_score, shadow_changes,"
                    " admission, admission_reason, work_mode, lane, eligible)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (job_id, r.score, r.decision, r.line[:600], r.why[:300], r.exclusion, r.confidence,
                     json.dumps(r.buckets), json.dumps(r.items), MODEL_VERSION, tag, fingerprint, _now(), r.shadow_score, r.shadow_changes,
                     decision.status, (decision.reason or "")[:80], work_mode, lane_name, ",".join(eligible)))
                if gate and decision.status in (lanes.ADMIT, lanes.REVIEW):          # D106: an excluded job that now qualifies (a wrong place, a profile that now knows its evidence) comes back
                    cursor.execute("UPDATE v7_jobs SET status='READY', unresolved_reason='requeued_fit', updated_at=%s WHERE id=%s AND status='EXCLUDED_FIT'"
                                   " AND unresolved_reason='lane_exclude' AND notion_page_id IS NULL", (_now(), job_id))
                    counts["revived"] += cursor.rowcount
                if gate and decision.status == lanes.EXCLUDE:
                    cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT', unresolved_reason=%s, updated_at=%s"
                                   " WHERE id=%s AND status='READY'", ("lane_exclude", _now(), job_id))
                    counts["gated"] += cursor.rowcount
    return counts
