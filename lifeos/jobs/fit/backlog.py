"""The Cloud Fit backlog batch: a bounded run of the Oracle fit1 path beyond the tick canary.

`python -m lifeos.run fit-cloud-backlog --limit N --live` asks at most min(N, V7_CLOUD_FIT_BACKLOG_MAX,
BACKLOG_CALLS_MAX) jobs, V7_CLOUD_FIT_PARALLEL at a time, within V7_CLOUD_FIT_BACKLOG_BUDGET seconds. The MAX
ceiling is a repository variable; unset or invalid is 0 (nothing asked). V7_CLOUD_FIT_BACKLOG_PAGE (optional, one
Ledger page id) limits the batch to that job or nothing; the id never reaches the log. An accepted answer becomes
the visible Fit through the Fit stage's promotion; the batch then runs fit_sync, and a promoted page not synced is
DEGRADED(LEDGER_SYNC_INCOMPLETE). Counts only in the log.
"""

# Candidates are the canary's own (a public employer / ATS posting the audit would keep, a static Admit or Review,
# never asked) plus published jobs, most useful first. A published job's Ledger page is read first
# (lifeos.jobs.guard): applied, an Applied On date or an unreadable page uses no call. A published job the cloud
# excludes is promoted the same way and the next tick's gated audit removes its page. The publish step does not run on
# a backlog-only dispatch, so the batch runs fit_sync itself.
#
# Parallelism is 1-3, unset 1; Oracle answers BUSY when no lane is free, which releases that job and lowers the calls
# in flight by one. Each job is claimed before its call and written right after it, so a batch can stop anywhere.
import os
import re

from lifeos.jobs import fit_sync, guard, ledger, sponsors, store
from lifeos.jobs.fit import cloud as fit_cloud, profile as fit_profile, profile_sync, stage
from lifeos.jobs.fit import edge

LEDGER_SKIP = {guard.APPLIED, guard.APPLIED_ON, guard.UNREADABLE}
REMAINING = (
    "SELECT j.status = 'PUBLISHED', f.admission, COUNT(*) FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id JOIN v7_job_descriptions d ON d.job_id = j.id"
    " WHERE j.status IN ('READY', 'RESOLVED', 'PUBLISHED') AND d.source_kind IN %s AND f.admission IN ('ADMIT', 'REVIEW') AND f.exclusion IS NULL"
    " AND f.score IS NOT NULL AND f.cloud_status IS NULL GROUP BY j.status = 'PUBLISHED', f.admission"
)


def _whole(raw, default=0):
    try:
        return max(0, int(str(raw).strip())) if raw is not None and str(raw).strip() != "" else default
    except ValueError:
        return 0


def _refused(counts, state):
    counts.update(cloud_fit=state, cloud_degraded=1, failed=1)
    return counts


def remaining(cursor):
    cursor.execute(REMAINING, (fit_cloud.PUBLIC_SOURCES,))
    out = {f"{p}_{a}": 0 for p in ("unpublished", "published") for a in ("admit", "review")}
    for published, admission, n in cursor.fetchall():
        out[f"{'published' if published else 'unpublished'}_{admission.lower()}"] = int(n)
    return out


def run(limit, live, environ=os.environ, client=None):
    ceiling = _whole(environ.get("V7_CLOUD_FIT_BACKLOG_MAX"))
    calls = min(_whole(limit), ceiling, fit_cloud.BACKLOG_CALLS_MAX)
    budget = min(
        _whole(environ.get("V7_CLOUD_FIT_BACKLOG_BUDGET"), fit_cloud.BACKLOG_BUDGET_MAX), fit_cloud.BACKLOG_BUDGET_MAX
    )
    counts = {
        "backlog_requested": _whole(limit),
        "backlog_ceiling": ceiling,
        "backlog_calls_allowed": calls,
        "backlog_budget_s": budget,
        "revived": 0,
        "gated": 0,
    }
    with store.connect() as connection, connection.cursor() as cursor:
        counts["remaining_before"] = remaining(cursor)
    if calls == 0:
        return _refused(counts, "DEGRADED(BACKLOG_NOT_AUTHORIZED)")
    page = (environ.get("V7_CLOUD_FIT_BACKLOG_PAGE") or "").strip().replace("-", "").lower()
    if page and not re.fullmatch(r"[0-9a-f]{32}", page):
        return _refused(counts, "DEGRADED(BACKLOG_TARGET_INVALID)")
    if page:
        calls = counts["backlog_calls_allowed"] = 1
        counts["backlog_targeted"] = 1
    try:
        profile = fit_profile.load(environ, profile_sync.stored_block(environ))
    except fit_profile.ProfileError:
        return _refused(counts, "DEGRADED(PROFILE_MISSING)")
    ledger_check, target = None, ledger.OK
    if live:
        if client is None:
            from lifeos.platform import notion_client  # noqa: PLC0415 - only a live batch reads Ledger pages

            client = notion_client.Client(environ)
        target = ledger.verify(client)
        if target != ledger.OK:
            ledger_check = lambda page_id, company, title: target  # noqa: E731 - every published job is skipped, never guessed
        else:

            def ledger_check(page_id, company, title):
                held = guard.protection(client, page_id, company, title)
                return held if held in LEDGER_SKIP else None

    gate = environ.get("V7_FIT_GATE") == "true"
    staged, promoted = stage.cloud_promoter(profile, gate, counts, sponsors.load()), []

    def promote(job_id, judgment, model):
        outcome = staged(job_id, judgment, model)
        if outcome is True:
            promoted.append(job_id)
        return outcome

    counts.update(
        fit_cloud.run(
            environ,
            live,
            None,
            profile,
            promote=promote,
            batch={"calls": calls, "budget": budget, "page": page or None},
            ledger_check=ledger_check or (lambda *a: None),
        )
    )
    with store.connect() as connection, connection.cursor() as cursor:
        counts["remaining_after"] = remaining(cursor)
        pages = []
        if promoted:
            cursor.execute(
                "SELECT id FROM v7_jobs WHERE id IN %s AND status = 'PUBLISHED' AND notion_page_id IS NOT NULL",
                (tuple(promoted),),
            )
            pages = [r[0] for r in cursor.fetchall()]
    if live:
        try:
            edge.limits(
                (environ.get("IMESSAGE_ALERTS_HOST") or "").strip(), backlog=sum(counts["remaining_after"].values())
            )
            counts["backlog_reported"] = sum(counts["remaining_after"].values())
        except edge.EdgeError as error:
            counts["backlog_report"] = str(error)
    counts["ledger_pages_promoted"] = len(pages)
    if live and pages:
        if target != ledger.OK:
            counts["ledger_pages_pending"] = len(pages)
        else:
            counts["fit_sync"] = fit_sync.run(client, live, gate)
            with store.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id WHERE j.id IN %s"
                    " AND (j.fit_synced_at IS NULL OR j.fit_synced_at < f.scored_at)",
                    (tuple(pages),),
                )
                counts["ledger_pages_pending"] = int(cursor.fetchone()[0])
        if counts["ledger_pages_pending"]:
            fit_cloud._degraded(counts, "LEDGER_SYNC_INCOMPLETE")
    counts["failed"] = counts["cloud_degraded"]
    return counts
