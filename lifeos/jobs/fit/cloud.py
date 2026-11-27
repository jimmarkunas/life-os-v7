"""The switchable cloud-Fit shadow: V7_CLOUD_FIT = off (default; unset or unknown) | shadow | canary | on (refused).

shadow and canary ask Oracle's `fit1` verb (lifeos.jobs.fit.edge) for at most one judgment a tick. shadow stores the
answer in the `cloud_*` columns of v7_job_fit and changes nothing visible; canary also promotes the accepted score to
the visible Fit. Input: the brief named by V7_CLOUD_FIT_PROFILE_REF (without it nothing is sent). Failures are fixed
codes shown as `cloud_fit=DEGRADED(<code>)`. Counts only in the log.
"""
# Candidates: READY / RESOLVED public-posting jobs with no Ledger page, ADMIT or REVIEW, unexcluded, never asked.
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_for
from datetime import datetime
import os
import re
import signal
import time
import uuid

from lifeos.jobs import audit, classify, lanes, store
from lifeos.jobs.fit import edge, judge
from lifeos.jobs.fit.score import evaluate

MODES = ("off", "shadow", "canary", "on")
MAX_PER_RUN = 1
SCAN = 25
PUBLIC_SOURCES = ("ats_api", "ats_list", "jsonld", "next_data", "page_text")
# the Fit stage budget: one call (connect 15 s + READ_WAIT) fits, a second never could
STAGE_BUDGET = 240
READ_WAIT = 150
CALL_SECONDS = 15 + READ_WAIT
_REF = re.compile(r"^[0-9a-f]{16}$")
JOB_OUTCOMES = {"FIT_EDGE_JUDGMENT_REJECTED", "FIT_EDGE_REQUEST_TOO_LARGE", "FIT_EDGE_OVERSIZE"}
SIZE_STOP = 3
REJECTED = "FIT_EDGE_JUDGMENT_REJECTED"
REJECT_MIN, REJECT_SHARE = 3, 0.10
SIZE_OUTCOMES = {"FIT_EDGE_REQUEST_TOO_LARGE", "FIT_EDGE_OVERSIZE"}
WRITE_CONFLICT = "CANARY_WRITE_CONFLICT"
PROMOTION_FAILED = "CANARY_PROMOTION_FAILED"
STOP_AND_RECORD = {"FIT_EDGE_PURGE_FAILED"}

PICK = ("SELECT j.id, j.title, j.company, j.location_text, d.full_text, d.fingerprint, j.final_apply_url, j.source, j.link_proof FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id"
        " JOIN v7_job_descriptions d ON d.job_id = j.id"
        " WHERE j.status IN ('READY', 'RESOLVED') AND j.notion_page_id IS NULL AND d.source_kind IN %s AND f.admission IN ('ADMIT', 'REVIEW') AND f.exclusion IS NULL"
        " AND f.score IS NOT NULL AND f.cloud_status IS NULL ORDER BY j.first_seen, j.dedupe_key LIMIT %s")
WRITE = ("UPDATE v7_job_fit SET cloud_status=%s, cloud_reason=%s, cloud_score=%s, cloud_confidence=%s, cloud_rationale=%s, cloud_request_hash=%s,"
         " cloud_model=%s, cloud_run_id=%s, cloud_jd_fingerprint=%s, cloud_at=%s WHERE job_id=%s AND cloud_status='CLAIMED' AND cloud_run_id=%s")
CLAIM = "UPDATE v7_job_fit SET cloud_status='CLAIMED', cloud_reason=NULL, cloud_run_id=%s, cloud_at=%s WHERE job_id=%s AND cloud_status IS NULL"
RELEASE = "UPDATE v7_job_fit SET cloud_status=NULL, cloud_run_id=NULL, cloud_at=NULL WHERE job_id=%s AND cloud_status='CLAIMED' AND cloud_run_id=%s"
SKIP = "UPDATE v7_job_fit SET cloud_status='SKIPPED', cloud_reason=%s, cloud_jd_fingerprint=%s, cloud_at=%s WHERE job_id=%s AND cloud_status IS NULL"
BACKLOG_PICK = ("SELECT j.id, j.title, j.company, j.location_text, d.full_text, d.fingerprint, j.final_apply_url, j.source, j.link_proof, j.notion_page_id"
                " FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id JOIN v7_job_descriptions d ON d.job_id = j.id"
                " WHERE j.status IN ('READY', 'RESOLVED', 'PUBLISHED') AND d.source_kind IN %s AND f.admission IN ('ADMIT', 'REVIEW') AND f.exclusion IS NULL"
                " AND f.score IS NOT NULL AND f.cloud_status IS NULL"
                " ORDER BY (f.admission = 'ADMIT') DESC, f.score DESC, j.posted_date IS NULL, j.posted_date DESC, j.first_seen, j.dedupe_key LIMIT %s")
BACKLOG_ONE = BACKLOG_PICK.replace(" ORDER BY", " AND REPLACE(LOWER(j.notion_page_id), '-', '') = %s ORDER BY")
TARGET_STATE = ("SELECT j.status, f.admission, f.exclusion IS NULL, f.score IS NULL, f.cloud_status, d.source_kind FROM v7_jobs j"
                " LEFT JOIN v7_job_fit f ON f.job_id = j.id LEFT JOIN v7_job_descriptions d ON d.job_id = j.id WHERE REPLACE(LOWER(j.notion_page_id), '-', '') = %s")


def _code(value):
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "NONE").upper()).strip("_")[:20] or "NONE"


def target_reason(rows):
    if not rows:
        return "NOT_IN_STORE"
    if len(rows) > 1:
        return "AMBIGUOUS"
    status, admission, unexcluded, unscored, cloud_status, source = rows[0]
    if status not in ("READY", "RESOLVED", "PUBLISHED"):
        return "STATUS_" + _code(status)
    if admission not in ("ADMIT", "REVIEW") or unscored:
        return "NOT_SCORED" if admission is None or unscored else "ADMISSION_" + _code(admission)
    if not unexcluded:
        return "EXCLUDED"
    if cloud_status is not None:
        return "ASKED_" + _code(cloud_status)
    if source is None:
        return "NO_DESCRIPTION"
    return "UNKNOWN" if source in PUBLIC_SOURCES else "SOURCE_" + _code(source)
BACKLOG_CALLS_MAX = 600
BACKLOG_BUDGET_MAX = 2700
BACKLOG_SCAN_PER_CALL, BACKLOG_SCAN_MAX = 5, 3000
MAX_PARALLEL = 3
NO_CALL = {"FIT_EDGE_BUSY", "FIT_EDGE_NO_HOST", "FIT_EDGE_NOT_CONFIGURED", "FIT_EDGE_PROBE_FAILED", "FIT_EDGE_CONNECT_FAILED"} | {f"FIT_EDGE_REFUSED_{r}" for r in edge.REASONS} | {"FIT_EDGE_REFUSED"}
QUOTA_STOP = "FIT_EDGE_REFUSED_QUOTA_RESERVE"
USAGE_UNAVAILABLE = "USAGE_UNAVAILABLE"
USAGE_SUMS = {"cloud_tokens_input": "input_tokens", "cloud_tokens_cached": "cached_input_tokens", "cloud_tokens_output": "output_tokens",
              "cloud_tokens_reasoning": "reasoning_tokens", "cloud_tokens_total": "total_tokens"}


def mode(environ):
    value = (environ.get("V7_CLOUD_FIT") or "off").strip().lower()
    return value if value in MODES else "off"


def per_run(environ):
    raw = environ.get("V7_CLOUD_FIT_PER_RUN")
    if raw is None or raw.strip() == "":
        return 1
    try:
        return max(0, min(int(raw.strip()), MAX_PER_RUN))
    except ValueError:
        return 0


def parallel(environ):
    raw = (environ.get("V7_CLOUD_FIT_PARALLEL") or "").strip()
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= MAX_PARALLEL else 1


def public_posting(url, source):
    """True when the Apply URL is on an employer ATS or the job was read off the employer's own board."""
    if not url:
        return False
    kind = classify.apply_kind(url)
    return kind == "ats" or (kind == "employer" and lanes.first_party_source(source))


def audit_problem(url, text, link_proof, source, company, title):
    """The precision audit's verdict as a fixed code, or None; a flagged row gets no call."""
    watched = (source or "").startswith("web:titlewatch:") or lanes.on_keep_list(company, title)
    why = audit.judge(url, text, link_proof == "title", watched)
    return why.upper() if why else None


def remaining(elapsed, budget=STAGE_BUDGET):
    """Seconds the stage may still run: the tick's alarm when set, at most `budget` from the start."""
    own = budget - elapsed
    alarm = signal.getitimer(signal.ITIMER_REAL)[0]
    return min(own, alarm) if alarm > 0 else own


_now = store.now


def run(environ=os.environ, live=False, started=None, profile=None, send=None, clock=time.monotonic, promote=None, batch=None, ledger_check=None):
    """-> counts: cloud_fit (off | OK | DEGRADED(<code>)), cloud_calls, cloud_ok, cloud_skipped, cloud_degraded,
    cloud_promoted and token sums.

    Called by the Fit stage (at most one call) or the backlog stage with `batch` ({"calls": n, "budget": seconds}).
    Each asked job is first CLAIMED so two workers never ask the same job. A refusal before any model call releases
    the claim and stops the run; any other failure is recorded on the row, so a call that may have run is never
    repeated. Claims, writes, promotions and counts stay on this thread.
    """
    counts = {"cloud_fit": "backlog" if batch else mode(environ), "cloud_calls": 0, "cloud_ok": 0, "cloud_skipped": 0, "cloud_degraded": 0, "cloud_skip_reasons": {},
              "cloud_promoted": 0, "cloud_failed": 0, "cloud_claim_lost": 0, **{k: 0 for k in USAGE_SUMS}, "cloud_usage_unavailable": 0}
    if counts["cloud_fit"] == "off":
        return counts
    canary = counts["cloud_fit"] in ("canary", "backlog")
    if counts["cloud_fit"] not in ("shadow", "canary", "backlog"):
        return _degraded(counts, "MODE_NOT_AUTHORIZED")
    if canary and promote is None:
        return _degraded(counts, "CANARY_WITHOUT_PROMOTION")
    if batch and ledger_check is None:
        return _degraded(counts, "BACKLOG_WITHOUT_LEDGER_CHECK")
    if not live:
        counts["cloud_fit"] = "dry_run"
        return counts
    ref, host = (environ.get("V7_CLOUD_FIT_PROFILE_REF") or "").strip(), (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()
    if not _REF.match(ref):
        return _degraded(counts, "PROFILE_REF_UNCONFIGURED")
    if not host:
        return _degraded(counts, "FIT_EDGE_NO_HOST")
    cap = batch["calls"] if batch else per_run(environ)
    budget = batch["budget"] if batch else STAGE_BUDGET
    send = send or edge.exchange
    if not cap:
        return _degraded(counts, "PER_RUN_ZERO")
    if profile is None:
        return _degraded(counts, "PROFILE_MISSING")
    with store.connect() as connection, connection.cursor() as cursor:
        if batch and batch.get("page"):
            cursor.execute(BACKLOG_ONE, (PUBLIC_SOURCES, batch["page"], 1))
        elif batch:
            cursor.execute(BACKLOG_PICK, (PUBLIC_SOURCES, min(cap * BACKLOG_SCAN_PER_CALL, BACKLOG_SCAN_MAX)))
        else:
            cursor.execute(PICK, (PUBLIC_SOURCES, SCAN))
        rows = cursor.fetchall()
        if batch and batch.get("page") and not rows:
            cursor.execute(TARGET_STATE, (batch["page"],))
            counts["backlog_target_reason"] = target_reason(cursor.fetchall())
    if batch and batch.get("page") and not rows:
        return _degraded(counts, "BACKLOG_TARGET_NOT_ELIGIBLE")
    started = clock() if started is None else started
    token = f"claim:{uuid.uuid4().hex[:24]}"

    def write(sql, args):
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, args)
            return cursor.rowcount

    def skip(job_id, reason, fingerprint):
        if write(SKIP, (reason, fingerprint, _now(), job_id)) == 1:
            counts["cloud_skipped"] += 1
            _bump(counts["cloud_skip_reasons"], reason)

    def record(job_id, status, reason, got, request_hash, fingerprint):
        j, receipt = (got or {}).get("judgment"), (got or {}).get("receipt") or {}
        return write(WRITE, (status, reason, j.score if j else None, j.confidence if j else None, j.rationale[:300] if j else None, request_hash,
                             str(receipt.get("model") or "")[:40] or None, str((receipt.get("run_ids") or [""])[0])[:64] or None, fingerprint, _now(), job_id, token))

    readings = []
    state = {"lanes": parallel(environ) if batch else 1, "stop": False}

    def settle(job_id, sent, fingerprint, call):
        """Record, count and promote one call's outcome; sets state["stop"] when no further call may be sent."""
        try:
            got = call()
        except edge.EdgeError as error:
            code = str(error)
            if code == "FIT_EDGE_BUSY" and state["lanes"] > 1:
                write(RELEASE, (job_id, token))
                state["lanes"] -= 1
                _bump(counts, "cloud_busy")
                return
            if code in NO_CALL:
                write(RELEASE, (job_id, token))
                readings.append(error.quota)
                if code == QUOTA_STOP:
                    counts["cloud_quota_reserve"] = 1
                else:
                    _degraded(counts, USAGE_UNAVAILABLE if code == "FIT_EDGE_REFUSED_USAGE_UNAVAILABLE" else code)
                state["stop"] = True
                return
            if code == "FIT_EDGE_RECEIPT_REJECTED" and error.detail:
                field = error.detail.upper()
                _bump(counts.setdefault("cloud_receipt_fields", {}), field)
            record(job_id, "DEGRADED", code, {"receipt": error.receipt} if code in STOP_AND_RECORD else None, edge.digest(sent)[:16], fingerprint)
            if code in SIZE_OUTCOMES:
                sized = counts.setdefault("cloud_job_too_large", {})
                _bump(sized, code)
                if code == "FIT_EDGE_OVERSIZE" and sized[code] >= SIZE_STOP and not counts["cloud_ok"]:
                    state["stop"] = counts["cloud_size_stop"] = True
                return
            if code == REJECTED and batch:
                _bump(counts, "cloud_job_rejected")
                return
            counts["cloud_failed"] += 1
            _degraded(counts, code)
            if code not in JOB_OUTCOMES:
                state["stop"] = True
            return
        receipt = got.get("receipt") or {}
        u = receipt.get("usage")
        measured = usage_ok(u)
        if measured:
            for key, name in USAGE_SUMS.items():
                counts[key] += u[name] if type(u.get(name)) is int else 0
        else:
            counts["cloud_usage_unavailable"] += 1
        counts["cloud_ok"] += 1
        readings.append(got.get("quota"))
        status, reason = ("DEGRADED", PROMOTION_FAILED) if canary else ("OK", None)
        if record(job_id, status, reason, got, edge.digest(sent)[:16], fingerprint) != 1:
            counts["cloud_ok"] -= 1
            _degraded(counts, WRITE_CONFLICT)
            return
        if canary:
            if promote(job_id, got["judgment"], str(receipt.get("model") or "model")):
                counts["cloud_promoted"] += 1
            else:
                counts["cloud_ok"] -= 1
                _degraded(counts, PROMOTION_FAILED)
        if not measured:
            _degraded(counts, USAGE_UNAVAILABLE)
            state["stop"] = True

    pool = ThreadPoolExecutor(max_workers=state["lanes"]) if state["lanes"] > 1 else None
    inflight = {}

    def settle_next():
        done, _ = wait_for(inflight, return_when=FIRST_COMPLETED)
        for future in done:
            settle(*inflight.pop(future), future.result)

    try:
        for job_id, title, company, location, text, fingerprint, url, source, link_proof, page_id in (r if len(r) == 10 else (*r, None) for r in rows):
            if state["stop"] or counts["cloud_calls"] >= cap:
                break
            if not public_posting(url, source):
                skip(job_id, "NOT_PUBLIC_POSTING", fingerprint)
                continue
            sent = judge.request(title, text, {"ref": ref}, location)
            if sent is None and judge.title_place(judge.request_title(title, location)):
                skip(job_id, "TITLE_LOCATION", fingerprint)
                continue
            if sent is None:
                skip(job_id, "NO_SUBSTANTIVE_TEXT", fingerprint)
                continue
            audited = audit_problem(url, text, link_proof, source, company, title)
            if audited:
                skip(job_id, audited, fingerprint)
                continue
            if page_id:
                held = ledger_check(page_id, company, title)
                if held:
                    counts["cloud_skipped"] += 1
                    _bump(counts["cloud_skip_reasons"], "LEDGER_" + held.upper())
                    continue
            if remaining(clock() - started, budget) < CALL_SECONDS:
                if batch and counts["cloud_calls"]:
                    counts["cloud_budget_reached"] = 1
                else:
                    _degraded(counts, "STAGE_BUDGET")
                break
            if write(CLAIM, (token, _now(), job_id)) != 1:
                counts["cloud_claim_lost"] += 1
                continue
            counts["cloud_calls"] += 1
            base = evaluate(title, company, text, profile, _now().date())
            ask = lambda sent=sent, base=base, title=title: edge.invoke(host, sent, base, title, send=send, wait=READ_WAIT)     # noqa: E731
            if pool is None:
                settle(job_id, sent, fingerprint, ask)
                continue
            inflight[pool.submit(ask)] = (job_id, sent, fingerprint)
            while inflight and len(inflight) >= state["lanes"]:
                settle_next()
        while inflight:
            settle_next()
    finally:
        if pool:
            pool.shutdown(wait=True)
    quota = quota_summary(readings)
    if quota:
        counts["cloud_quota"] = quota
    oversize = counts.get("cloud_job_too_large", {}).get("FIT_EDGE_OVERSIZE", 0)
    if counts.get("cloud_size_stop") or (oversize >= 2 and oversize == counts["cloud_calls"]):
        counts["cloud_failed"] += 1
        _degraded(counts, "FIT_EDGE_ALL_TOO_LARGE")
    rejected = counts.get("cloud_job_rejected", 0)
    if rejected > REJECT_MIN and rejected > REJECT_SHARE * counts["cloud_calls"]:
        counts["cloud_failed"] += rejected
        _degraded(counts, REJECTED)
    if counts["cloud_fit"] in ("shadow", "canary", "backlog"):
        counts["cloud_fit"] = "OK"
    return counts


def usage_ok(u):
    """True when input, output and total are non-negative integers that add up (cache inside or outside input).

    Cached and reasoning counts may be omitted or "unavailable"; any other value must be an integer.
    """
    if not isinstance(u, dict) or not all(type(u.get(k)) is int and u[k] >= 0 for k in ("input_tokens", "output_tokens", "total_tokens")):
        return False
    def optional(key, cap):
        value = u.get(key, "unavailable")
        return (isinstance(value, str) and value == "unavailable") or (type(value) is int and 0 <= value <= cap)
    def counted(key):
        value = u.get(key, "unavailable")
        return value if type(value) is int and value >= 0 else 0 if (isinstance(value, str) and value == "unavailable") else None
    if not optional("reasoning_tokens", u["output_tokens"]) or not optional("cached_input_tokens", 1 << 62) or not optional("cache_write_tokens", 1 << 62):
        return False
    inside = u["total_tokens"] == u["input_tokens"] + u["output_tokens"] and optional("cached_input_tokens", u["input_tokens"])
    cached, writes = u.get("cached_input_tokens"), counted("cache_write_tokens")
    outside = type(cached) is int and writes is not None and u["total_tokens"] == u["input_tokens"] + cached + writes + u["output_tokens"]
    return inside or outside


def quota_summary(readings):
    """Meter readings (oldest first) -> per window used percent, reset in Central time and a per-call estimate."""
    readings = [r for r in readings if r]
    if not readings:
        return None
    from zoneinfo import ZoneInfo                                       # noqa: PLC0415
    stop, out = readings[-1]["stop"], {}
    for w in readings[-1]["windows"]:
        series = [next((x["used"] for x in r["windows"] if x["label"] == w["label"]), None) for r in readings]
        rise = calls = 0
        for a, b in zip(series, series[1:]):
            if a is not None and b is not None and b >= a:
                rise, calls = rise + b - a, calls + 1
        try:
            reset = datetime.fromisoformat(w["reset_at"]).astimezone(ZoneInfo("America/Chicago")).strftime("%b %d %I:%M %p %Z")
        except ValueError:
            reset = None
        row = {"used_first": series[0], "used_last": w["used"], "resets": reset, "basis_calls": calls}
        room = max(stop - w["used"], 0)
        if calls and rise:
            row.update(estimate="ESTIMATED", per_call_percent=round(rise / calls, 4), calls_left=int(room * calls / rise))
        elif calls:
            row.update(estimate="BELOW_RESOLUTION", per_call_below=round(1 / calls, 4), calls_left_at_least=int(room * calls))
        out[w["label"]] = row
    return {"stop_used_percent": stop, "windows": out}


def _bump(table, key):
    table[key] = table.get(key, 0) + 1


def _degraded(counts, code):
    counts["cloud_fit"], counts["cloud_degraded"] = f"DEGRADED({code})", counts["cloud_degraded"] + 1
    return counts
