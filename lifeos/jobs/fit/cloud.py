"""LIFE-41 (10472 C, 10474, 10475): the switchable cloud-Fit shadow. `V7_CLOUD_FIT` = off (default, unset or anything unknown) | shadow | canary | on.

Only off is authorized today. shadow asks Oracle's existing `fit1` verb (lifeos.jobs.fit.edge, the same framing, receipt and judge.accept envelope) for at most
one judgment a tick (MAX_PER_RUN; `V7_CLOUD_FIT_PER_RUN` can only lower it to 0) and stores the answer privately in nullable `cloud_*` columns of the job's
existing v7_job_fit row. It never changes the visible Fit, the decision, the admission, the Ledger, Gmail, the audit or Trash: the static score stays the
authority. canary and on are refused with no call (G8 is a separate later decision).

Candidates are NEW records only: READY / RESOLVED, a public employer / ATS posting (10500: proven by the final Apply URL's host, `public_posting`) that the precision
audit would keep (`audit_problem`: its listing/root/no-job-id link and thin-description guards, judged exactly as lifeos.jobs.audit does), no Ledger page, a static admission of ADMIT or REVIEW, no private exclusion and never asked before
(`cloud_status IS NULL`), in a stable order by first seen and Stable Job Key. Up to SCAN of them are examined a run: one that cannot be sent (not a public
posting, a place in its title, nothing substantive) is recorded SKIPPED with its fixed reason at no network cost, and the run moves on to the next, so an
unsendable backlog never uses up the run's one call (LIFE-41 G6 proof run 39202038517 spent its only pick on a skip). A PUBLISHED job is never asked, whatever changes on it later. V7 sends no profile
text: the profile is the Oracle-held brief named by `V7_CLOUD_FIT_PROFILE_REF` (16 hex); without it nothing is sent. Every failure is a fixed code shown as
`cloud_fit=DEGRADED(<code>)`; a failure that is not about the one job (no host, refusal, timeout, a receipt that does not match) stops the run's cloud calls and
writes nothing, so the job is asked again on a later tick and no static score is ever presented as a cloud answer. Counts only in the log."""
from datetime import datetime, timezone
import os
import re
import signal
import time

from lifeos.jobs import audit, classify, lanes, store
from lifeos.jobs.fit import edge, judge
from lifeos.jobs.fit.score import evaluate

MODES = ("off", "shadow", "canary", "on")
MAX_PER_RUN = 1                         # the hard ceiling, whatever the variable says: one cloud call a tick when activated (Jim, LIFE-41)
SCAN = 25                               # candidates examined a run; a skip costs no network or model call, so it never consumes the one call
# LIFE-41 10500: only a public employer / ATS posting may be judged (the description enrich read from the employer's own page); a title-only or title-watch
# placeholder, and any other source, is never sent.
PUBLIC_SOURCES = ("ats_api", "jsonld", "next_data", "page_text")
STAGE_BUDGET = 240                      # the tick's Fit budget (tick.PLAN): a call starts only when the stage can still finish it
READ_WAIT = 150                         # LIFE-41 10504 C4: Oracle's real worst case is ~110 s (60 s deadline + 20 s orphan wait + purge lock waits + audit)
CALL_SECONDS = 15 + READ_WAIT           # connect timeout + the reply wait: 165 s, so one call fits the 240 s stage and a second never could
_REF = re.compile(r"^[0-9a-f]{16}$")
JOB_OUTCOMES = {"FIT_EDGE_JUDGMENT_REJECTED", "FIT_EDGE_REQUEST_TOO_LARGE", "FIT_EDGE_OVERSIZE"}      # about this job's request: recorded on its row, never re-asked
STOP_AND_RECORD = {"FIT_EDGE_PURGE_FAILED"}   # 10504 C5: a model ran, so its provenance (no score) is kept on the row, and no further request is sent

PICK = ("SELECT j.id, j.title, j.company, j.location_text, d.full_text, d.fingerprint, j.final_apply_url, j.source, j.link_proof FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id"
        " JOIN v7_job_descriptions d ON d.job_id = j.id"
        " WHERE j.status IN ('READY', 'RESOLVED') AND j.notion_page_id IS NULL AND d.source_kind IN %s AND f.admission IN ('ADMIT', 'REVIEW') AND f.exclusion IS NULL"
        " AND f.score IS NOT NULL AND f.cloud_status IS NULL ORDER BY j.first_seen, j.dedupe_key LIMIT %s")
WRITE = ("UPDATE v7_job_fit SET cloud_status=%s, cloud_reason=%s, cloud_score=%s, cloud_confidence=%s, cloud_rationale=%s, cloud_request_hash=%s,"
         " cloud_model=%s, cloud_run_id=%s, cloud_jd_fingerprint=%s, cloud_at=%s WHERE job_id=%s AND cloud_status IS NULL")


def mode(environ):
    value = (environ.get("V7_CLOUD_FIT") or "off").strip().lower()
    return value if value in MODES else "off"


def per_run(environ):
    """Unset: 1. An explicit value that is not a whole number fails closed to 0 (TL review of #339)."""
    raw = environ.get("V7_CLOUD_FIT_PER_RUN")
    if raw is None or raw.strip() == "":
        return 1
    try:
        return max(0, min(int(raw.strip()), MAX_PER_RUN))
    except ValueError:
        return 0


def public_posting(url, source):
    """LIFE-41 10500 provenance: the job's canonical final Apply URL is on an employer ATS (classify.ATS), or the job was read off the employer's own board
    by the web pass (lanes.first_party_source) at an employer host. A description-reading method (`source_kind`) proves nothing about the site, so Dice,
    LinkedIn, every aggregator, an unknown host reached through a newsletter (it may be a recruiter's page) and a job with no URL are refused before any frame."""
    if not url:
        return False
    kind = classify.apply_kind(url)
    return kind == "ats" or (kind == "employer" and lanes.first_party_source(source))


def audit_problem(url, text, link_proof, source, company, title):
    """The precision audit's own verdict (lifeos.jobs.audit.judge, with the same `proven` and `title_only` inputs the audit derives) as an upper-case fixed
    code such as AUDIT_URL_LISTING_URL, or None. The audit runs right after the Fit stage and demotes such a row (status NEW, link cleared, description
    deleted), so a cloud call on it would be wasted (G6 run 39219054103 spent its only call on an audit_url_listing_url row)."""
    watched = (source or "").startswith("web:titlewatch:") or lanes.on_keep_list(company, title)
    why = audit.judge(url, text, link_proof == "title", watched)
    return why.upper() if why else None


def remaining(elapsed):
    """Seconds this Fit stage may still run: the tick's own alarm when one is set (it can cut the stage below 240 s when the run is short of time), never more than
    STAGE_BUDGET from the stage's start. Read without disturbing the alarm."""
    own = STAGE_BUDGET - elapsed
    alarm = signal.getitimer(signal.ITIMER_REAL)[0]
    return min(own, alarm) if alarm > 0 else own


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run(environ=os.environ, live=False, started=None, profile=None, send=None, clock=time.monotonic):
    """-> counts: cloud_fit (off | OK | DEGRADED(<code>)), cloud_calls, cloud_ok, cloud_skipped, cloud_degraded. Called by the Fit stage after its static write, with its
    profile: the static result of the same text is the base, so the hard-family and junior caps and the gate apply to the cloud score exactly as to the static one."""
    counts = {"cloud_fit": mode(environ), "cloud_calls": 0, "cloud_ok": 0, "cloud_skipped": 0, "cloud_degraded": 0, "cloud_skip_reasons": {}}
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
    cap, send = per_run(environ), send or edge.exchange                 # looked up at call time: a test's patched transport is the one used
    if not cap:
        return _degraded(counts, "PER_RUN_ZERO")
    if profile is None:
        return _degraded(counts, "PROFILE_MISSING")
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute(PICK, (PUBLIC_SOURCES, SCAN))
        rows = cursor.fetchall()
    started = clock() if started is None else started
    results = []
    def skip(job_id, reason, fingerprint):
        results.append((job_id, "SKIPPED", reason, None, None, fingerprint))
        counts["cloud_skipped"] += 1
        counts["cloud_skip_reasons"][reason] = counts["cloud_skip_reasons"].get(reason, 0) + 1     # fixed codes only: why, never which job

    for job_id, title, company, location, text, fingerprint, url, source, link_proof in rows:     # no store session is open across a network call
        if counts["cloud_calls"] >= cap:
            break                                                        # the one call is spent (or attempted): later candidates stay unasked, untouched
        if not public_posting(url, source):
            skip(job_id, "NOT_PUBLIC_POSTING", fingerprint)
            continue
        sent = judge.request(title, text, {"ref": ref}, location)
        if sent is None and judge.title_place(judge.request_title(title, location)):
            skip(job_id, "TITLE_LOCATION", fingerprint)                  # fail closed: a place left in the title is never sent
            continue
        if sent is None:
            skip(job_id, "NO_SUBSTANTIVE_TEXT", fingerprint)
            continue
        audited = audit_problem(url, text, link_proof, source, company, title)
        if audited:
            skip(job_id, audited, fingerprint)                         # LIFE-41 G6, the last gate before egress: the same tick's audit would demote it; never spend the call on it
            continue
        if remaining(clock() - started) < CALL_SECONDS:
            _degraded(counts, "STAGE_BUDGET")
            break
        counts["cloud_calls"] += 1
        try:
            got = edge.invoke(host, sent, evaluate(title, company, text, profile, _now().date()), title, send=send, wait=READ_WAIT)
        except edge.EdgeError as error:
            code = str(error)
            if code in STOP_AND_RECORD:
                results.append((job_id, "DEGRADED", code, {"receipt": error.receipt}, edge.digest(sent)[:16], fingerprint))
                _degraded(counts, code)
                break
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
