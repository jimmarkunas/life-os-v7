"""LIFE-41 (10472 C, 10474, 10475): the switchable cloud-Fit shadow. `V7_CLOUD_FIT` = off (default, unset or anything unknown) | shadow | canary | on.

shadow and canary are authorized (canary: Jim, LIFE-41 10555, D226); on is refused. shadow asks Oracle's existing `fit1` verb (lifeos.jobs.fit.edge, the same framing, receipt and judge.accept envelope) for at most
one judgment a tick (MAX_PER_RUN; `V7_CLOUD_FIT_PER_RUN` can only lower it to 0) and stores the answer privately in nullable `cloud_*` columns of the job's
existing v7_job_fit row. It never changes the visible Fit, the decision, the admission, the Ledger, Gmail, the audit or Trash: the static score stays the
authority. canary (D226) asks exactly as shadow and stores the answer first as the named failure DEGRADED / CANARY_PROMOTION_FAILED; then the Fit stage's `promote` makes that
one job's accepted score its visible Fit through the stage's own lane decision, in the one statement that also marks the row CANARY. A promotion that does not
happen leaves that failure (the static score stays visible and re-scorable, the run is DEGRADED); every other failure path stores and promotes nothing visible.
on is refused with no call.

Candidates are NEW records only: READY / RESOLVED, a public employer / ATS posting (10500: proven by the final Apply URL's host, `public_posting`) that the precision
audit would keep (`audit_problem`: its listing/root/no-job-id link and thin-description guards, judged exactly as lifeos.jobs.audit does), no Ledger page, a static admission of ADMIT or REVIEW, no private exclusion and never asked before
(`cloud_status IS NULL`), in a stable order by first seen and Stable Job Key. Up to SCAN of them are examined a run: one that cannot be sent (not a public
posting, a place in its title, nothing substantive) is recorded SKIPPED with its fixed reason at no network cost, and the run moves on to the next, so an
unsendable backlog never uses up the run's one call (LIFE-41 G6 proof run 39202038517 spent its only pick on a skip). A PUBLISHED job is never asked, whatever changes on it later. V7 sends no profile
text: the profile is the Oracle-held brief named by `V7_CLOUD_FIT_PROFILE_REF` (16 hex); without it nothing is sent. Every failure is a fixed code shown as
`cloud_fit=DEGRADED(<code>)`; a failure that is not about the one job (no host, refusal, timeout, a receipt that does not match) stops the run's cloud calls and
writes nothing, so the job is asked again on a later tick and no static score is ever presented as a cloud answer. Counts only in the log."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_for
from datetime import datetime, timezone
import os
import re
import signal
import time
import uuid

from lifeos.jobs import audit, classify, lanes, store
from lifeos.jobs.fit import edge, judge
from lifeos.jobs.fit.score import evaluate

MODES = ("off", "shadow", "canary", "on")
MAX_PER_RUN = 1                         # the hard ceiling, whatever the variable says: one cloud call a tick when activated (Jim, LIFE-41)
SCAN = 25                               # candidates examined a run; a skip costs no network or model call, so it never consumes the one call
# LIFE-41 10500: only a public employer / ATS posting may be judged (the description enrich read from the employer's own page); a title-only or title-watch
# placeholder, and any other source, is never sent. ats_list (LIFE-41 G6, Jim): the posting's own text from the employer's ATS board listing that the web
# boards lane reads (sources/web/run.py; final Apply URL on that ATS, apply_kind='ats'), the same provenance as ats_api; public_posting still checks the host.
PUBLIC_SOURCES = ("ats_api", "ats_list", "jsonld", "next_data", "page_text")
STAGE_BUDGET = 240                      # the tick's Fit budget (tick.PLAN): a call starts only when the stage can still finish it
READ_WAIT = 150                         # LIFE-41 10504 C4: Oracle's real worst case is ~110 s (60 s deadline + 20 s orphan wait + purge lock waits + audit)
CALL_SECONDS = 15 + READ_WAIT           # connect timeout + the reply wait: 165 s, so one call fits the 240 s stage and a second never could
_REF = re.compile(r"^[0-9a-f]{16}$")
JOB_OUTCOMES = {"FIT_EDGE_JUDGMENT_REJECTED", "FIT_EDGE_REQUEST_TOO_LARGE", "FIT_EDGE_OVERSIZE"}      # about this job's request: recorded on its row, never re-asked
WRITE_CONFLICT = "CANARY_WRITE_CONFLICT"     # D226: the guarded write stored nothing (the row was no longer unasked): an unpersisted answer is never a success
PROMOTION_FAILED = "CANARY_PROMOTION_FAILED"   # D226: a canary answer is stored as this until the promotion itself marks it CANARY, so no failure can leave a stuck CANARY
STOP_AND_RECORD = {"FIT_EDGE_PURGE_FAILED"}   # 10504 C5: a model ran, so its provenance (no score) is kept on the row, and no further request is sent

PICK = ("SELECT j.id, j.title, j.company, j.location_text, d.full_text, d.fingerprint, j.final_apply_url, j.source, j.link_proof FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id"
        " JOIN v7_job_descriptions d ON d.job_id = j.id"
        " WHERE j.status IN ('READY', 'RESOLVED') AND j.notion_page_id IS NULL AND d.source_kind IN %s AND f.admission IN ('ADMIT', 'REVIEW') AND f.exclusion IS NULL"
        " AND f.score IS NOT NULL AND f.cloud_status IS NULL ORDER BY j.first_seen, j.dedupe_key LIMIT %s")
WRITE = ("UPDATE v7_job_fit SET cloud_status=%s, cloud_reason=%s, cloud_score=%s, cloud_confidence=%s, cloud_rationale=%s, cloud_request_hash=%s,"
         " cloud_model=%s, cloud_run_id=%s, cloud_jd_fingerprint=%s, cloud_at=%s WHERE job_id=%s AND cloud_status='CLAIMED' AND cloud_run_id=%s")
# D219: a job is claimed by one conditional write before its call (the token is this run's), released only when Oracle refused before any model call.
CLAIM = "UPDATE v7_job_fit SET cloud_status='CLAIMED', cloud_reason=NULL, cloud_run_id=%s, cloud_at=%s WHERE job_id=%s AND cloud_status IS NULL"
RELEASE = "UPDATE v7_job_fit SET cloud_status=NULL, cloud_run_id=NULL, cloud_at=NULL WHERE job_id=%s AND cloud_status='CLAIMED' AND cloud_run_id=%s"
SKIP = "UPDATE v7_job_fit SET cloud_status='SKIPPED', cloud_reason=%s, cloud_jd_fingerprint=%s, cloud_at=%s WHERE job_id=%s AND cloud_status IS NULL"
# D219 backlog (Jim's separately approved bounded batch): the same predicates without "never published", most useful first (Admit before Review, the higher
# static Fit, the newer posting), so a batch spends its calls on the jobs Jim is most likely to apply to rather than on the oldest records.
BACKLOG_PICK = ("SELECT j.id, j.title, j.company, j.location_text, d.full_text, d.fingerprint, j.final_apply_url, j.source, j.link_proof, j.notion_page_id"
                " FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id JOIN v7_job_descriptions d ON d.job_id = j.id"
                " WHERE j.status IN ('READY', 'RESOLVED', 'PUBLISHED') AND d.source_kind IN %s AND f.admission IN ('ADMIT', 'REVIEW') AND f.exclusion IS NULL"
                " AND f.score IS NOT NULL AND f.cloud_status IS NULL"
                " ORDER BY (f.admission = 'ADMIT') DESC, f.score DESC, j.posted_date IS NULL, j.posted_date DESC, j.first_seen, j.dedupe_key LIMIT %s")
# LIFE-41 G6: Jim's one preapproved job, named by its Ledger page id (V7_CLOUD_FIT_BACKLOG_PAGE): the same predicates, so a target the batch would not ask is never asked.
BACKLOG_ONE = BACKLOG_PICK.replace(" ORDER BY", " AND REPLACE(LOWER(j.notion_page_id), '-', '') = %s ORDER BY")
TARGET_STATE = ("SELECT j.status, f.admission, f.exclusion IS NULL, f.score IS NULL, f.cloud_status, d.source_kind FROM v7_jobs j"
                " LEFT JOIN v7_job_fit f ON f.job_id = j.id LEFT JOIN v7_job_descriptions d ON d.job_id = j.id WHERE REPLACE(LOWER(j.notion_page_id), '-', '') = %s")


def _code(value):
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "NONE").upper()).strip("_")[:20] or "NONE"


def target_reason(rows):
    """Why BACKLOG_ONE found no row for Jim's target: the first BACKLOG_PICK predicate it fails, as one fixed code from the store's own enums. Read only."""
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
BACKLOG_CALLS_MAX = 600                # D219, D240 (Jim, Oct 20: 100 -> 600 once a call took ~6 s): the hard ceiling of one batch, whatever is asked (Oracle's own daily cap still applies under it)
BACKLOG_BUDGET_MAX = 2700               # seconds a batch may run (D238: 45 min, so a tick's batch fills the hour to :55); each call starts only with CALL_SECONDS left
BACKLOG_SCAN_PER_CALL, BACKLOG_SCAN_MAX = 5, 3000   # D240: enough candidates for a 600-call batch (about 40% are skipped without a call)
MAX_PARALLEL = 3                        # D232: Oracle's lanes (fit, fit2, fit3); a batch never keeps more calls in flight
NO_CALL = {"FIT_EDGE_BUSY", "FIT_EDGE_NO_HOST", "FIT_EDGE_NOT_CONFIGURED", "FIT_EDGE_PROBE_FAILED", "FIT_EDGE_CONNECT_FAILED"} | {f"FIT_EDGE_REFUSED_{r}" for r in edge.REASONS} | {"FIT_EDGE_REFUSED"}
QUOTA_STOP = "FIT_EDGE_REFUSED_QUOTA_RESERVE"   # LIFE-77 (Jim: stop at 50 % used): the account's allowance reached Jim's reserve; a normal stop, not a failure
USAGE_UNAVAILABLE = "USAGE_UNAVAILABLE"   # LIFE-41: the fixed code for missing, invalid or inconsistent usage, from Oracle's refusal or the receipt itself
USAGE_SUMS = {"cloud_tokens_input": "input_tokens", "cloud_tokens_cached": "cached_input_tokens", "cloud_tokens_output": "output_tokens",
              "cloud_tokens_reasoning": "reasoning_tokens", "cloud_tokens_total": "total_tokens"}     # the receipts' own counts, summed; never estimated


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


def parallel(environ):
    """D232 (parallel Luna, Jim Oct 20): V7_CLOUD_FIT_PARALLEL, the calls a backlog batch keeps in flight at once, at most MAX_PARALLEL (Oracle's lanes). Unset,
    not a whole number or out of range: 1, one call at a time as before."""
    raw = (environ.get("V7_CLOUD_FIT_PARALLEL") or "").strip()
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= MAX_PARALLEL else 1


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


def remaining(elapsed, budget=STAGE_BUDGET):
    """Seconds this Fit stage may still run: the tick's own alarm when one is set (it can cut the stage below 240 s when the run is short of time), never more than
    `budget` (the tick's STAGE_BUDGET, or a backlog batch's) from the stage's start. Read without disturbing the alarm."""
    own = budget - elapsed
    alarm = signal.getitimer(signal.ITIMER_REAL)[0]
    return min(own, alarm) if alarm > 0 else own


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run(environ=os.environ, live=False, started=None, profile=None, send=None, clock=time.monotonic, promote=None, batch=None, ledger_check=None):
    """-> counts: cloud_fit (off | OK | DEGRADED(<code>)), cloud_calls, cloud_ok, cloud_skipped, cloud_degraded, cloud_promoted and the receipts' own token
    sums. Called by the Fit stage after its static write (shadow / canary: V7_CLOUD_FIT, at most one call), or by the backlog stage with `batch` (D219, Jim's
    separately approved bounded batch: {"calls": n, "budget": seconds}; it never reads V7_CLOUD_FIT). The static result of the same text is the base, so the hard-family
    and junior caps and the gate apply to the cloud score exactly as to the static one.

    Every asked job is first CLAIMED (one conditional write with this run's token), so two workers never ask the same job, and its result is written right after its
    call, so an interrupted run keeps every finished answer. A refusal made before any model call (BUSY, REFUSED_*) releases the claim and stops the run; any other
    failure is recorded on the row, so a model call that may have run is never repeated. A backlog batch may keep up to V7_CLOUD_FIT_PARALLEL calls in flight (D232,
    one per Oracle lane): only the calls themselves run at once; claims, writes, promotions and counts stay on this thread, and a stop lets the calls already sent
    finish and be recorded."""
    counts = {"cloud_fit": "backlog" if batch else mode(environ), "cloud_calls": 0, "cloud_ok": 0, "cloud_skipped": 0, "cloud_degraded": 0, "cloud_skip_reasons": {},
              "cloud_promoted": 0, "cloud_failed": 0, "cloud_claim_lost": 0, **{k: 0 for k in USAGE_SUMS}, "cloud_usage_unavailable": 0}
    if counts["cloud_fit"] == "off":
        return counts                                                    # no store read, no network, no model
    canary = counts["cloud_fit"] in ("canary", "backlog")                # both make an accepted answer visible through the stage's promotion
    if counts["cloud_fit"] not in ("shadow", "canary", "backlog"):
        return _degraded(counts, "MODE_NOT_AUTHORIZED")
    if canary and promote is None:
        return _degraded(counts, "CANARY_WITHOUT_PROMOTION")              # canary is only meaningful inside the Fit stage, which owns admission
    if batch and ledger_check is None:
        return _degraded(counts, "BACKLOG_WITHOUT_LEDGER_CHECK")          # a published job is asked only after its page is read for human state
    if not live:
        counts["cloud_fit"] = "dry_run"                                  # a dry run asks nothing
        return counts
    ref, host = (environ.get("V7_CLOUD_FIT_PROFILE_REF") or "").strip(), (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()
    if not _REF.match(ref):
        return _degraded(counts, "PROFILE_REF_UNCONFIGURED")
    if not host:
        return _degraded(counts, "FIT_EDGE_NO_HOST")
    cap = batch["calls"] if batch else per_run(environ)
    budget = batch["budget"] if batch else STAGE_BUDGET
    send = send or edge.exchange                                         # looked up at call time: a test's patched transport is the one used
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
            counts["backlog_target_reason"] = target_reason(cursor.fetchall())    # a fixed code, never an id or a value from the job's text
    if batch and batch.get("page") and not rows:
        return _degraded(counts, "BACKLOG_TARGET_NOT_ELIGIBLE")          # gone, already asked, unpublishable or excluded: nothing else is asked in its place
    started = clock() if started is None else started
    token = f"claim:{uuid.uuid4().hex[:24]}"

    def write(sql, args):
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, args)
            return cursor.rowcount

    def skip(job_id, reason, fingerprint):
        if write(SKIP, (reason, fingerprint, _now(), job_id)) == 1:
            counts["cloud_skipped"] += 1
            counts["cloud_skip_reasons"][reason] = counts["cloud_skip_reasons"].get(reason, 0) + 1     # fixed codes only: why, never which job

    def record(job_id, status, reason, got, request_hash, fingerprint):
        """The one write of an asked job's outcome, guarded by this run's claim. -> rows written (0: the claim was lost)."""
        j, receipt = (got or {}).get("judgment"), (got or {}).get("receipt") or {}
        return write(WRITE, (status, reason, j.score if j else None, j.confidence if j else None, j.rationale[:300] if j else None, request_hash,
                             str(receipt.get("model") or "")[:40] or None, str((receipt.get("run_ids") or [""])[0])[:64] or None, fingerprint, _now(), job_id, token))

    readings = []                                                        # LIFE-77: the meter reading each call was admitted (or refused) on
    state = {"lanes": parallel(environ) if batch else 1, "stop": False}   # parallel Luna (D232): a backlog batch only; the tick's one call is always alone

    def settle(job_id, sent, fingerprint, call):
        """One sent call's outcome (`call` returns the answer or raises its EdgeError): recorded, counted and promoted here, on the run's own thread.
        Sets state["stop"] when no further call may be sent; calls already in flight still finish and are settled."""
        try:
            got = call()
        except edge.EdgeError as error:
            code = str(error)
            if code == "FIT_EDGE_BUSY" and state["lanes"] > 1:
                write(RELEASE, (job_id, token))                          # D232: Oracle had fewer free lanes than this batch's calls: no model ran; the job waits for the
                state["lanes"] -= 1                                      # next batch, and this one keeps going with one call fewer in flight
                counts["cloud_busy"] = counts.get("cloud_busy", 0) + 1
                return
            if code in NO_CALL:                                          # refused before any model call: the job goes back to the queue, the run stops
                write(RELEASE, (job_id, token))                          # cloud_calls still counts the request sent
                readings.append(error.quota)
                if code == QUOTA_STOP:
                    counts["cloud_quota_reserve"] = 1                    # LIFE-77: Jim's reserve of the account's allowance is reached: a normal stop; the rest wait for the reset
                else:
                    _degraded(counts, USAGE_UNAVAILABLE if code == "FIT_EDGE_REFUSED_USAGE_UNAVAILABLE" else code)
                state["stop"] = True
                return
            if code == "FIT_EDGE_RECEIPT_REJECTED" and error.detail:
                field = error.detail.upper()                                 # a fixed contract field name, as a code
                fields = counts.setdefault("cloud_receipt_fields", {})
                fields[field] = fields.get(field, 0) + 1     # LIFE-77: which receipt field, a fixed name
            record(job_id, "DEGRADED", code, {"receipt": error.receipt} if code in STOP_AND_RECORD else None, edge.digest(sent)[:16], fingerprint)
            counts["cloud_failed"] += 1
            _degraded(counts, code)
            if code not in JOB_OUTCOMES:
                state["stop"] = True                                     # anything not about this job (a model may have run): recorded so it is never repeated, and the run stops
            return                                                       # about this job only: recorded, never re-asked; the next job may be asked
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
        status, reason = ("DEGRADED", PROMOTION_FAILED) if canary else ("OK", None)     # D226: fail closed until the promotion's own write marks it CANARY
        if record(job_id, status, reason, got, edge.digest(sent)[:16], fingerprint) != 1:
            counts["cloud_ok"] -= 1
            _degraded(counts, WRITE_CONFLICT)
            return
        if canary:
            if promote(job_id, got["judgment"], str(receipt.get("model") or "model")):
                counts["cloud_promoted"] += 1                            # D226: only an accepted, receipt-checked, purged answer that was just stored
            else:
                counts["cloud_ok"] -= 1
                _degraded(counts, PROMOTION_FAILED)
        if not measured:
            _degraded(counts, USAGE_UNAVAILABLE)                         # LIFE-41 usage integrity, every mode: the answer is kept; no further call this run
            state["stop"] = True

    pool = ThreadPoolExecutor(max_workers=state["lanes"]) if state["lanes"] > 1 else None
    inflight = {}                                                        # D232: future -> (job_id, sent, fingerprint); only the network call runs off this thread

    def settle_next():
        done, _ = wait_for(inflight, return_when=FIRST_COMPLETED)
        for future in done:
            settle(*inflight.pop(future), future.result)

    try:
        for job_id, title, company, location, text, fingerprint, url, source, link_proof, page_id in (r if len(r) == 10 else (*r, None) for r in rows):
            if state["stop"] or counts["cloud_calls"] >= cap:
                break                                                    # stopped, or the run's calls are spent (or attempted): later candidates stay unasked, untouched
            if not public_posting(url, source):
                skip(job_id, "NOT_PUBLIC_POSTING", fingerprint)
                continue
            sent = judge.request(title, text, {"ref": ref}, location)
            if sent is None and judge.title_place(judge.request_title(title, location)):
                skip(job_id, "TITLE_LOCATION", fingerprint)              # fail closed: a place left in the title is never sent
                continue
            if sent is None:
                skip(job_id, "NO_SUBSTANTIVE_TEXT", fingerprint)
                continue
            audited = audit_problem(url, text, link_proof, source, company, title)
            if audited:
                skip(job_id, audited, fingerprint)                     # LIFE-41 G6, the last gate before egress: the same tick's audit would demote it; never spend the call on it
                continue
            if page_id:
                held = ledger_check(page_id, company, title)             # D219: applied (or unreadable) Ledger pages never use a call; the page itself is never changed here
                if held:
                    counts["cloud_skipped"] += 1
                    counts["cloud_skip_reasons"]["LEDGER_" + held.upper()] = counts["cloud_skip_reasons"].get("LEDGER_" + held.upper(), 0) + 1
                    continue                                             # not recorded: the human state can change, so it is checked again next time
            if remaining(clock() - started, budget) < CALL_SECONDS:
                if batch and counts["cloud_calls"]:
                    counts["cloud_budget_reached"] = 1                   # LIFE-41: a bounded batch stopping at its time limit after real work is normal; the rest wait for the next run
                else:
                    _degraded(counts, "STAGE_BUDGET")                    # no time for even one call (or the tick's single call): a real shortfall
                break
            if write(CLAIM, (token, _now(), job_id)) != 1:
                counts["cloud_claim_lost"] += 1                          # another worker holds (or finished) this job: never asked twice
                continue
            counts["cloud_calls"] += 1
            base = evaluate(title, company, text, profile, _now().date())
            ask = lambda sent=sent, base=base, title=title: edge.invoke(host, sent, base, title, send=send, wait=READ_WAIT)     # noqa: E731
            if pool is None:
                settle(job_id, sent, fingerprint, ask)                   # one call at a time, exactly as before
                continue
            inflight[pool.submit(ask)] = (job_id, sent, fingerprint)
            while inflight and len(inflight) >= state["lanes"]:
                settle_next()                                            # a lane frees before the next call is sent
        while inflight:
            settle_next()                                                # every call already sent is settled, whatever stopped the run
    finally:
        if pool:
            pool.shutdown(wait=True)                                     # an interrupted run leaves its in-flight jobs CLAIMED: held, never re-asked
    quota = quota_summary(readings)
    if quota:
        counts["cloud_quota"] = quota                                    # LIFE-77: readings and estimates only, never the account
    if counts["cloud_fit"] in ("shadow", "canary", "backlog"):
        counts["cloud_fit"] = "OK"
    return counts


def usage_ok(u):
    """LIFE-41 usage integrity: the receipt's own counts are usable only when input, output and total are non-negative integers with total = input + output.
    Cached and reasoning counts may be omitted or the literal "unavailable" (Oracle's form when the Gateway did not report one); any other present value must be
    an integer inside input / output, so a malformed one (a string, bool, container, None, float) is never treated as clean. Never estimated, never zero-filled."""
    if not isinstance(u, dict) or not all(type(u.get(k)) is int and u[k] >= 0 for k in ("input_tokens", "output_tokens", "total_tokens")):
        return False
    def optional(key, cap):
        value = u.get(key, "unavailable")
        return (isinstance(value, str) and value == "unavailable") or (type(value) is int and 0 <= value <= cap)
    return u["total_tokens"] == u["input_tokens"] + u["output_tokens"] and optional("cached_input_tokens", u["input_tokens"]) and optional("reasoning_tokens", u["output_tokens"])


def quota_summary(readings):
    """LIFE-77: the meter readings this run's calls were admitted on (oldest first; the last may be a refusal's) -> per window the first and last used percent, its reset
    in Central time, and an ESTIMATE from the rises between consecutive readings: the percent per call and the calls left before the stop. A window that did not move
    over k calls is below the meter's 1 % resolution: an upper bound per call and a lower bound on calls left, never "0 %". A fall is a reset and starts a new segment."""
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


def _degraded(counts, code):
    counts["cloud_fit"], counts["cloud_degraded"] = f"DEGRADED({code})", counts["cloud_degraded"] + 1
    return counts
