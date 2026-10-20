"""The `fit` stage: score every job that has a full description and no current score. Counts only in the log.

Reads and writes are separate short connections; scoring is pure CPU between them. A job is re-scored when the model
version, the private profile or the description changes. Each job is then judged by its lane policy (lifeos.jobs.lanes: Fit 72, work mode, explicit pay, age; a Newsletter job is judged
as US Remote) and the decision is stored. With V7_FIT_GATE=true an EXCLUDE READY job becomes EXCLUDED_FIT (never published) and
only ADMIT / REVIEW publish; without it everything is recorded in shadow mode and publishing is unchanged.
"""
from datetime import datetime, timezone
import json
import os
import time

from lifeos.jobs import lanes, sponsors, store
from lifeos.jobs.fit import GO_THRESHOLD, MODEL_VERSION, UNIVERSAL_FIT_FLOOR, cloud as fit_cloud, profile as fit_profile, semantic as fit_semantic
from lifeos.jobs.fit.judge import Judgment
from lifeos.jobs.fit.score import evaluate

PICK = ("SELECT j.id, j.title, j.company, d.full_text, d.fingerprint, j.lane, j.location_text, j.salary_text,"
        " j.posted_date, j.first_seen, j.route_evidence, j.source FROM v7_jobs j"
        " JOIN v7_job_descriptions d ON d.job_id = j.id LEFT JOIN v7_job_fit f ON f.job_id = j.id"
        " WHERE j.status IN (%s) AND (f.cloud_status IS NULL OR f.cloud_status <> 'CANARY')"     # D226: a cloud-Fit canary score is never overwritten by the static scorer
        " AND (f.job_id IS NULL OR f.model_version <> %s OR f.profile_hash <> %s"
        " OR f.jd_fingerprint <> d.fingerprint) ORDER BY j.first_seen LIMIT %s")
OPEN = ("'READY'", "'RESOLVED'")
AUTO = OPEN + ("'PUBLISHED'",)                          # D94: every tick also re-scores published jobs whose score is stale (model, policy, profile or description changed); the audit then clears the ones now excluded
ALL = AUTO + ("'EXCLUDED_FIT'",)                         # FIT_ALL=true (by hand): also re-score excluded jobs (calibration)


# D214: an upsert of the static columns only. REPLACE INTO deleted the row first, so it wiped every column it did not name (the cloud shadow's private result).
_STATIC = ("job_id", "score", "decision", "line", "why", "exclusion", "confidence", "buckets", "trace", "model_version", "profile_hash", "jd_fingerprint", "scored_at",
           "shadow_score", "shadow_changes", "admission", "admission_reason", "work_mode", "lane", "eligible")
WRITE = (f"INSERT INTO v7_job_fit ({', '.join(_STATIC)}) VALUES ({','.join(['%s'] * len(_STATIC))})"
         " ON DUPLICATE KEY UPDATE " + ", ".join(f"{c}=VALUES({c})" for c in _STATIC[1:]))


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


def score_rows(rows, profile, today, semantic=None, register=None, fit=None):
    """[(job_id, fingerprint, Result, Decision, lane, work_mode, eligible)] for rows of (id, title, company, full_text, fingerprint,
    [lane, location, salary_text, posted_date, first_seen, route_evidence]). `register`: the sponsor register (Skilled Worker route evidence).
    `fit` (D226 cloud-Fit canary): {job_id: score} replaces the static score in the lane decision; every other rule is the same code. Pure."""
    out = []
    for job_id, title, company, text, fingerprint, *rest in rows:
        lane, location, salary, posted, first_seen, route, source = (list(rest) + [None] * 7)[:7]
        result = evaluate(title, company, text, profile, today, semantic)
        if register is not None:
            route = lanes.join_routes(route, **{sponsors.ROUTE: register.state(company)})
        score = (fit or {}).get(job_id, result.score)
        facts = lanes.facts_for(score, title, location, text, salary, posted, first_seen or today, route=route, first_party=lanes.first_party_source(source),
                                kept=lanes.on_keep_list(company, title) or lanes.title_watch_source(source),
                                title_only=lanes.title_watch_source(source))
        decision, lane_name, eligible = lanes.decide_all(lane, facts, today, result.exclusion)
        out.append((job_id, fingerprint, result, decision, lane_name, facts.work_mode, eligible))
    return out


def _gate(cursor, job_id, decision, gate, counts):
    """The lane gate's effect on the job row, for a static score and a cloud-Fit canary score alike."""
    if gate and decision.status in (lanes.ADMIT, lanes.REVIEW):          # D106: an excluded job that now qualifies (a wrong place, a profile that now knows its evidence) comes back
        cursor.execute("UPDATE v7_jobs SET status='READY', unresolved_reason='requeued_fit', updated_at=%s WHERE id=%s AND status='EXCLUDED_FIT'"
                       " AND unresolved_reason='lane_exclude' AND notion_page_id IS NULL", (_now(), job_id))
        counts["revived"] += cursor.rowcount
    if gate and decision.status == lanes.EXCLUDE:
        cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT', unresolved_reason=%s, updated_at=%s"
                       " WHERE id=%s AND status='READY'", ("lane_exclude", _now(), job_id))
        counts["gated"] += cursor.rowcount


CANARY_ROW = ("SELECT j.id, j.title, j.company, d.full_text, d.fingerprint, j.lane, j.location_text, j.salary_text, j.posted_date, j.first_seen, j.route_evidence, j.source"
              " FROM v7_jobs j JOIN v7_job_descriptions d ON d.job_id = j.id WHERE j.id = %s")
PROMOTE = ("UPDATE v7_job_fit SET score=%s, decision=%s, line=%s, why=%s, confidence=%s, admission=%s, admission_reason=%s, work_mode=%s, lane=%s, eligible=%s,"
           " scored_at=%s, cloud_status='CANARY', cloud_reason=NULL WHERE job_id=%s AND cloud_status='DEGRADED' AND cloud_reason=%s")


def promote_cloud(cursor, job_id, judgment, model, profile, today, gate, counts, register=None):
    """D226 (Jim, LIFE-41 10555): the cloud-Fit canary. The model's accepted score (judge.accept: the family and junior caps already applied) becomes this job's
    visible Fit: the same score_rows lane decision decides admission with it, and the same gate moves the job. The provenance is in the line ('Cloud Fit ...'),
    which publish writes to Why It Fits. Only the row the shadow has just stored as CANARY_PROMOTION_FAILED is touched, and the one UPDATE that writes the
    visible Fit is the one that marks it CANARY, so the marker and the visible score exist together or not at all. A published job the cloud excludes is promoted
    the same way (D224, replacing the D219 hold): its stored admission becomes EXCLUDE and the gated precision audit, which runs right after Fit
    in the tick, trashes its page under lifeos.jobs.guard (Applied, Applied On, a Saturn Decision or an active pipeline opportunity keeps it) and the job becomes
    EXCLUDED_FIT, never published again. -> True when promoted, else False."""
    cursor.execute(CANARY_ROW, (job_id,))
    row = cursor.fetchone()
    if row is None or judgment.score is None:
        return False
    (_, _, _, decision, lane_name, work_mode, eligible), = score_rows([row], profile, today, None, register, fit={job_id: judgment.score})
    line = f"Cloud Fit {judgment.score}% ({model}): {judgment.rationale}"
    cursor.execute(PROMOTE, (judgment.score, judgment.decision, line[:600], judgment.rationale[:300], judgment.confidence, decision.status,
                             (decision.reason or "")[:80], work_mode, lane_name, ",".join(eligible), _now(), job_id, fit_cloud.PROMOTION_FAILED))
    if cursor.rowcount != 1:
        return False
    _gate(cursor, job_id, decision, gate, counts)
    return True


def regate_canary(cursor):
    """D226: every tick re-applies the lane gate to each CANARY row from its stored admission (the same two idempotent moves as _gate), because the static pass never
    re-picks a canary row. A promotion whose gate write failed (the tick then fails loudly) is therefore moved on the next tick; an already-gated job is untouched. -> jobs moved."""
    cursor.execute("UPDATE v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id SET j.status='EXCLUDED_FIT', j.unresolved_reason='lane_exclude', j.updated_at=%s"
                   " WHERE f.cloud_status='CANARY' AND f.admission='EXCLUDE' AND j.status='READY'", (_now(),))
    moved = int(cursor.rowcount or 0)
    cursor.execute("UPDATE v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id SET j.status='READY', j.unresolved_reason='requeued_fit', j.updated_at=%s"
                   " WHERE f.cloud_status='CANARY' AND f.admission IN ('ADMIT', 'REVIEW') AND j.status='EXCLUDED_FIT' AND j.unresolved_reason='lane_exclude'"
                   " AND j.notion_page_id IS NULL", (_now(),))
    return moved + int(cursor.rowcount or 0)


HELD_ROWS = ("SELECT job_id, cloud_score, cloud_confidence, cloud_rationale, cloud_model, exclusion FROM v7_job_fit"
             " WHERE cloud_status='HELD' AND cloud_reason='PUBLISHED_WOULD_EXCLUDE' AND cloud_score IS NOT NULL ORDER BY job_id LIMIT 200")
UNHOLD = "UPDATE v7_job_fit SET cloud_status='DEGRADED', cloud_reason=%s WHERE job_id=%s AND cloud_status='HELD' AND cloud_reason='PUBLISHED_WOULD_EXCLUDE'"


def release_held(cursor, profile, today, gate, counts, register=None):
    """Jim's decision on the D219 holds (LIFE-41, Oct 20: "Apply the exclusion"): each published job the cloud had already judged and HELD is promoted from its
    stored private answer, through promote_cloud exactly as a new answer (no model call). The row first returns to the named canary failure, so a promotion that
    cannot complete stays visible as CANARY_PROMOTION_FAILED, never HELD again and never re-asked. Idempotent: once no row is HELD this does nothing.
    -> (released, failed)."""
    cursor.execute(HELD_ROWS)
    released = failed = 0
    for job_id, score, confidence, rationale, model, exclusion in cursor.fetchall():
        cursor.execute(UNHOLD, (fit_cloud.PROMOTION_FAILED, job_id))
        if cursor.rowcount != 1:
            continue
        judgment = Judgment(int(score), "No-Go" if exclusion or score < UNIVERSAL_FIT_FLOOR else "Go", rationale or "", confidence or "low")     # judge.accept's own rule
        if promote_cloud(cursor, job_id, judgment, model or "model", profile, today, gate, counts, register) is True:
            released += 1
        else:
            failed += 1
    return released, failed


def cloud_promoter(profile, gate, counts, register):
    """-> promote(job_id, judgment, model): one store session per promotion (D219: the cloud run writes each job's answer right after its call)."""
    def promote(job_id, judgment, model):
        with store.connect() as connection, connection.cursor() as cursor:
            return promote_cloud(cursor, job_id, judgment, model, profile, _now().date(), gate, counts, register)
    return promote


def requeue_floor(cursor):
    """D84, one time: jobs excluded only for Fit under the old floor (reason text 'Fit N below 72', score 60 and up) go back to READY so the new
    floor and the Review band judge them. The new decision text never says 'below 72', so a job is requeued at most once."""
    cursor.execute("UPDATE v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id SET j.status='READY', j.unresolved_reason='requeued_floor', j.updated_at=%s"
                   " WHERE j.status='EXCLUDED_FIT' AND j.unresolved_reason='lane_exclude' AND j.notion_page_id IS NULL"
                   " AND f.admission_reason LIKE 'Fit %% below 72' AND f.score >= 60", (_now(),))
    return int(cursor.rowcount or 0)


def _title_description(cursor, job_id, title):
    """Publishing needs a stored description row. A kept role whose page cannot be read gets the title and a plain note, never invented content."""
    from lifeos.jobs import jd                                                      # noqa: PLC0415
    d = jd.describe(f"{title}\n{lanes.TITLE_ONLY_REASON}.", is_html=False)
    cursor.execute("INSERT INTO v7_job_descriptions (job_id, source_kind, full_text, summary, responsibilities, requirements, qualifications, fingerprint, fetched_at)"
                   " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE job_id=job_id",
                   (job_id, "title_only", d["full_text"], d["summary"], d["responsibilities"], d["requirements"], d["qualifications"], d["fingerprint"], _now()))


def rejudge_excluded(cursor):
    """A READY job whose stored decision is EXCLUDE is judged again: its Fit row is made stale so the pass below re-scores it, and the gate then moves it to EXCLUDED_FIT (or admits it
    if the facts changed). The gate acts only on a job scored in that pass, so a job that came back to READY with an old EXCLUDE (an age or place rule since changed) was never re-picked
    and sat READY for days (40 on Oct 12). Same mechanism as the D111 title and place correction. D214: the row is marked stale (model_version '0', which PICK
    re-scores) instead of deleted, so its private cloud columns survive. -> rows marked."""
    cursor.execute("UPDATE v7_job_fit f JOIN v7_jobs j ON j.id = f.job_id SET f.model_version='0' WHERE j.status='READY' AND j.notion_page_id IS NULL AND f.admission='EXCLUDE'")
    return int(cursor.rowcount or 0)


def promote_kept(cursor):
    """D124: a job on Jim's keep list or found by title search that is still RESOLVED, has a stored link, and whose stored Fit decision is ADMIT or REVIEW
    becomes READY. Its own page answers 403 to Enrich, so nothing else would ever move it. Judged on the stored decision, so it does not wait for a re-score.
    -> counts: promoted, and (counts only) how many kept RESOLVED jobs lack a stored link, a description or a Review/Admit decision, or already have a page."""
    cursor.execute("SELECT j.id, j.company, j.title, j.source, j.final_apply_url IS NOT NULL, d.job_id IS NOT NULL, f.admission, j.notion_page_id IS NOT NULL, j.status"
                   " FROM v7_jobs j LEFT JOIN v7_job_fit f ON f.job_id = j.id LEFT JOIN v7_job_descriptions d ON d.job_id = j.id WHERE j.status IN ('RESOLVED', 'READY') LIMIT 5000")
    out = {"promoted": 0, "kept_resolved": 0, "no_link": 0, "no_description": 0, "no_decision": 0, "has_page": 0}
    for job_id, company, title, source, link, text, admission, page, status in cursor.fetchall():
        if not (lanes.on_keep_list(company, title) or lanes.title_watch_source(source)):
            continue
        if status == "READY":                                                        # already promoted: only the missing description row is added
            if not text and link:
                _title_description(cursor, job_id, title)
                out["described"] = out.get("described", 0) + 1
            continue
        out["kept_resolved"] += 1
        out["no_link"] += not link
        out["no_description"] += not text
        out["no_decision"] += admission not in (lanes.ADMIT, lanes.REVIEW)
        out["has_page"] += bool(page)
        if link and not page and admission in (lanes.ADMIT, lanes.REVIEW):
            if not text:
                _title_description(cursor, job_id, title)
            cursor.execute("UPDATE v7_jobs SET status='READY', unresolved_reason=NULL, updated_at=%s WHERE id=%s AND status='RESOLVED'", (_now(), job_id))
            out["promoted"] += 1
    return out


def run(limit, live, environ=os.environ):
    started = time.monotonic()
    counts = {"picked": 0, "go": 0, "no_go": 0, "excluded": 0, "unscorable": 0, "low_confidence": 0, "gated": 0, "profile": "ok",
              "semantic": "off", "lane_admit": 0, "lane_review": 0, "lane_exclude": 0, "shadow_jobs_changed": 0, "shadow_flips": 0, "shadow_reclassified": 0,
              "sim_72_77": 0, "sim_77_82": 0, "sim_82_up": 0, "fcap_moved": 0, "fcap_go_lost": 0, "revived": 0, "promoted": 0}
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
            counts.update(promote_kept(cursor) if live and gate else {"promoted": 0})
            counts["rejudged"] = rejudge_excluded(cursor) if live and gate else 0
            counts["canary_regated"] = regate_canary(cursor) if live and gate else 0
            extra_sql, extra_params = only_clause(environ.get("FIT_ONLY"))
            query = (PICK % (", ".join(ALL if environ.get("FIT_ALL") == "true" else AUTO), "%s", "%s", "%s")).replace(" ORDER BY j.first_seen", extra_sql + " ORDER BY j.first_seen")
            cursor.execute(query, (MODEL_VERSION, tag, *extra_params, limit))
            rows = cursor.fetchall()
    counts["picked"] = len(rows)
    register = sponsors.load()
    if live and gate:
        with store.connect() as connection, connection.cursor() as cursor:
            counts["cloud_held_released"], counts["cloud_held_failed"] = release_held(cursor, profile, _now().date(), gate, counts, register)
    scored = score_rows(rows, profile, _now().date(), matcher, register)      # slow work: no connection is open here
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
                cursor.execute(WRITE,
                    (job_id, r.score, r.decision, r.line[:600], r.why[:300], r.exclusion, r.confidence,
                     json.dumps(r.buckets), json.dumps(r.items), MODEL_VERSION, tag, fingerprint, _now(), r.shadow_score, r.shadow_changes,
                     decision.status, (decision.reason or "")[:80], work_mode, lane_name, ",".join(eligible)))
                _gate(cursor, job_id, decision, gate, counts)
    promote = cloud_promoter(profile, gate, counts, register)          # D226: canary only
    counts.update(fit_cloud.run(environ, live, started, profile, promote=promote))   # D214: after the static write; off by default, shadow never changes it
    counts["failed"] = counts["cloud_degraded"] + counts.get("cloud_held_failed", 0)     # D214: only an enabled shadow can degrade; run.PARTIAL_FAILURE makes the stage exit 1 (FIT_CLOUD_DEGRADED)
    return counts
