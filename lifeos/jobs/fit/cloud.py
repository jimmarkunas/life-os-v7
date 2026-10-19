"""LIFE-41 (10472 C, 10474, 10475): the switchable cloud-Fit shadow. `V7_CLOUD_FIT` = off (default, unset or anything unknown) | shadow | canary | on.

Only off is authorized today. shadow asks Oracle's existing `fit1` verb (lifeos.jobs.fit.edge, the same framing, receipt and judge.accept envelope) for at most
`V7_CLOUD_FIT_PER_RUN` judgments a tick (default 1, never more than MAX_PER_RUN) and stores the answer privately in nullable `cloud_*` columns of the job's
existing v7_job_fit row. It never changes the visible Fit, the decision, the admission, the Ledger, Gmail, the audit or Trash: the static score stays the
authority. canary and on are refused with no call (G8 is a separate later decision).

Candidates are NEW records only: READY / RESOLVED, no Ledger page, a static admission of ADMIT or REVIEW, no private exclusion and never asked before
(`cloud_status IS NULL`), in a stable order by first seen and Stable Job Key. A PUBLISHED job is never asked, whatever changes on it later. V7 sends no profile
text: the profile is the Oracle-held brief named by `V7_CLOUD_FIT_PROFILE_REF` (16 hex); without it nothing is sent. Every failure is a fixed code shown as
`cloud_fit=DEGRADED(<code>)`; a failure that is not about the one job (no host, refusal, timeout, a receipt that does not match) stops the run's cloud calls and
writes nothing, so the job is asked again on a later tick and no static score is ever presented as a cloud answer. Counts only in the log."""
from datetime import datetime, timezone
import os
import re
import time

from lifeos.jobs import store
from lifeos.jobs.fit import edge, judge
from lifeos.jobs.fit.score import evaluate

MODES = ("off", "shadow", "canary", "on")
MAX_PER_RUN = 2                         # the hard ceiling, whatever the variable says (10472 C.2)
STAGE_BUDGET = 240                      # the tick's Fit budget (tick.PLAN): a call starts only when the stage can still finish it
CALL_SECONDS = 15 + edge.WAIT           # connect timeout + the longest reply wait
_REF = re.compile(r"^[0-9a-f]{16}$")
JOB_OUTCOMES = {"FIT_EDGE_JUDGMENT_REJECTED", "FIT_EDGE_REQUEST_TOO_LARGE", "FIT_EDGE_OVERSIZE"}      # about this job's request: recorded on its row, never re-asked

PICK = ("SELECT j.id, j.title, j.company, j.location_text, d.full_text, d.fingerprint FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id"
        " JOIN v7_job_descriptions d ON d.job_id = j.id"
        " WHERE j.status IN ('READY', 'RESOLVED') AND j.notion_page_id IS NULL AND f.admission IN ('ADMIT', 'REVIEW') AND f.exclusion IS NULL"
        " AND f.score IS NOT NULL AND f.cloud_status IS NULL ORDER BY j.first_seen, j.dedupe_key LIMIT %s")
WRITE = ("UPDATE v7_job_fit SET cloud_status=%s, cloud_reason=%s, cloud_score=%s, cloud_confidence=%s, cloud_rationale=%s, cloud_request_hash=%s,"
         " cloud_model=%s, cloud_run_id=%s, cloud_jd_fingerprint=%s, cloud_at=%s WHERE job_id=%s AND cloud_status IS NULL")


def mode(environ):
    value = (environ.get("V7_CLOUD_FIT") or "off").strip().lower()
    return value if value in MODES else "off"


def per_run(environ):
    try:
        return max(0, min(int(environ.get("V7_CLOUD_FIT_PER_RUN") or 1), MAX_PER_RUN))
    except ValueError:
        return 1


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run(environ=os.environ, live=False, started=None, profile=None, send=edge.exchange, clock=time.monotonic):
    """-> counts: cloud_fit (off | OK | DEGRADED(<code>)), cloud_calls, cloud_ok, cloud_skipped, cloud_degraded. Called by the Fit stage after its static write, with its
    profile: the static result of the same text is the base, so the hard-family and junior caps and the gate apply to the cloud score exactly as to the static one."""
    counts = {"cloud_fit": mode(environ), "cloud_calls": 0, "cloud_ok": 0, "cloud_skipped": 0, "cloud_degraded": 0}
    if counts["cloud_fit"] == "off":
        return counts                                                    # no store read, no network, no model
    if counts["cloud_fit"] != "shadow":
        return _degraded(counts, "MODE_NOT_AUTHORIZED")
    if not live:
        counts["cloud_fit"] = "dry_run"                                  # a dry run asks nothing
        return counts
    ref, host = (environ.get("V7_CLOUD_FIT_PROFILE_REF") or "").strip(), (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()
    if not _REF.match(ref):
        return _degraded(counts, "PROFILE_REF_UNCONFIGURED")
    if not host:
        return _degraded(counts, "FIT_EDGE_NO_HOST")
    cap = per_run(environ)
    if not cap:
        return _degraded(counts, "PER_RUN_ZERO")
    if profile is None:
        return _degraded(counts, "PROFILE_MISSING")
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute(PICK, (cap,))
        rows = cursor.fetchall()
    started = clock() if started is None else started
    results = []
    for job_id, title, company, location, text, fingerprint in rows:      # no store session is open across a network call
        sent = judge.request(title, text, {"ref": ref}, location)
        if sent is None:
            results.append((job_id, "SKIPPED", "NO_SUBSTANTIVE_TEXT", None, None, fingerprint))
            counts["cloud_skipped"] += 1
            continue
        if clock() - started + CALL_SECONDS > STAGE_BUDGET:
            _degraded(counts, "STAGE_BUDGET")
            break
        counts["cloud_calls"] += 1
        try:
            got = edge.invoke(host, sent, evaluate(title, company, text, profile, _now().date()), title, send=send)
        except edge.EdgeError as error:
            code = str(error)
            if code not in JOB_OUTCOMES:
                _degraded(counts, code)                                    # not about this job: nothing written, no further call this run
                break
            results.append((job_id, "DEGRADED", code, None, None, fingerprint))
            _degraded(counts, code)
            continue
        results.append((job_id, "OK", None, got, edge.digest(sent)[:16], fingerprint))
        counts["cloud_ok"] += 1
    if results:
        with store.connect() as connection, connection.cursor() as cursor:
            for job_id, status, reason, got, request_hash, fingerprint in results:
                j, receipt = (got or {}).get("judgment"), (got or {}).get("receipt") or {}
                cursor.execute(WRITE, (status, reason, j.score if j else None, j.confidence if j else None, j.rationale[:300] if j else None, request_hash,
                                       str(receipt.get("model") or "")[:40] or None, str((receipt.get("run_ids") or [""])[0])[:64] or None, fingerprint, _now(), job_id))
    if counts["cloud_fit"] == "shadow":
        counts["cloud_fit"] = "OK"
    return counts


def _degraded(counts, code):
    counts["cloud_fit"], counts["cloud_degraded"] = f"DEGRADED({code})", counts["cloud_degraded"] + 1
    return counts
