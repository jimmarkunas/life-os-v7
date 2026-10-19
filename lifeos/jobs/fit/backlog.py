"""D219 (LIFE-41, Jim): the Cloud Fit backlog batch. One manual, bounded run of the ONE Oracle fit1 path over the jobs the tick's single canary call never reaches.

`python -m lifeos.run fit-cloud-backlog --limit N --live` asks at most min(N, V7_CLOUD_FIT_BACKLOG_MAX, BACKLOG_CALLS_MAX) jobs, one at a time (Oracle runs one
request at a time and answers a second with BUSY: concurrency is 1, never bypassed), within V7_CLOUD_FIT_BACKLOG_BUDGET seconds. V7_CLOUD_FIT_BACKLOG_MAX is Jim's
approved ceiling (a repository variable); unset or invalid is 0, so nothing is asked. It does not read V7_CLOUD_FIT and no scheduled tick runs it.

Candidates are the canary's own (a public employer / ATS posting the audit would keep, a static Admit or Review, never asked) plus published jobs, most useful
first. A published job's Ledger page is read first (lifeos.jobs.guard): applied, an Applied On date or an unreadable page uses no call. An accepted answer becomes
the visible Fit through the Fit stage's own promotion, and fit_sync later writes only the page's machine-owned Fit fields (never Applied, Applied On or Saturn
Decision); a published job the cloud would exclude is HELD privately, its page untouched. The batch runs that unchanged fit_sync itself at its end (the publish step
does not run on a backlog-only dispatch) and checks every promoted page was synced: one that was not is DEGRADED(LEDGER_SYNC_INCOMPLETE), never a silent success. Each job is claimed before its call and written right after it, so a
batch can stop anywhere and the next one resumes with the jobs still unasked. Counts only in the log: no title, employer, text, profile or answer."""
import os

from lifeos.jobs import fit_sync, guard, ledger, sponsors, store
from lifeos.jobs.fit import cloud as fit_cloud, profile as fit_profile, stage

LEDGER_SKIP = {guard.APPLIED, guard.APPLIED_ON, guard.UNREADABLE}
REMAINING = ("SELECT j.status = 'PUBLISHED', f.admission, COUNT(*) FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id JOIN v7_job_descriptions d ON d.job_id = j.id"
             " WHERE j.status IN ('READY', 'RESOLVED', 'PUBLISHED') AND d.source_kind IN %s AND f.admission IN ('ADMIT', 'REVIEW') AND f.exclusion IS NULL"
             " AND f.score IS NOT NULL AND f.cloud_status IS NULL GROUP BY j.status = 'PUBLISHED', f.admission")


def _whole(raw, default=0):
    try:
        return max(0, int(str(raw).strip())) if raw is not None and str(raw).strip() != "" else default
    except ValueError:
        return 0                                                        # an explicit value that is not a whole number fails closed


def remaining(cursor):
    """-> the never-asked candidates by {published|unpublished}_{admit|review} (fixed keys, counts only)."""
    cursor.execute(REMAINING, (fit_cloud.PUBLIC_SOURCES,))
    out = {f"{p}_{a}": 0 for p in ("unpublished", "published") for a in ("admit", "review")}
    for published, admission, n in cursor.fetchall():
        out[f"{'published' if published else 'unpublished'}_{admission.lower()}"] = int(n)
    return out


def run(limit, live, environ=os.environ, client=None):
    ceiling = _whole(environ.get("V7_CLOUD_FIT_BACKLOG_MAX"))
    calls = min(_whole(limit), ceiling, fit_cloud.BACKLOG_CALLS_MAX)
    budget = min(_whole(environ.get("V7_CLOUD_FIT_BACKLOG_BUDGET"), fit_cloud.BACKLOG_BUDGET_MAX), fit_cloud.BACKLOG_BUDGET_MAX)
    counts = {"backlog_requested": _whole(limit), "backlog_ceiling": ceiling, "backlog_calls_allowed": calls, "backlog_budget_s": budget, "revived": 0, "gated": 0}
    with store.connect() as connection, connection.cursor() as cursor:
        counts["remaining_before"] = remaining(cursor)
    if calls == 0:
        counts.update(cloud_fit="DEGRADED(BACKLOG_NOT_AUTHORIZED)", cloud_degraded=1, failed=1)      # no approved ceiling or no request: nothing is asked
        return counts
    try:
        profile = fit_profile.load(environ)
    except fit_profile.ProfileError:
        counts.update(cloud_fit="DEGRADED(PROFILE_MISSING)", cloud_degraded=1, failed=1)
        return counts
    ledger_check, target = None, ledger.OK
    if live:
        if client is None:
            from lifeos.platform import notion_client                     # noqa: PLC0415 - only a live batch reads Ledger pages
            client = notion_client.Client(environ)
        target = ledger.verify(client)                                   # the Job Ledger target check comes before any page read
        if target != ledger.OK:
            ledger_check = lambda page_id, company, title: target         # noqa: E731 - every published job is skipped, never guessed
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
    counts.update(fit_cloud.run(environ, live, None, profile, promote=promote, batch={"calls": calls, "budget": budget}, ledger_check=ledger_check or (lambda *a: None)))
    with store.connect() as connection, connection.cursor() as cursor:
        counts["remaining_after"] = remaining(cursor)
        pages = []
        if promoted:
            cursor.execute("SELECT id FROM v7_jobs WHERE id IN %s AND status = 'PUBLISHED' AND notion_page_id IS NOT NULL", (tuple(promoted),))
            pages = [r[0] for r in cursor.fetchall()]
    counts["ledger_pages_promoted"] = len(pages)
    if live and pages:
        if target != ledger.OK:
            counts["ledger_pages_pending"] = len(pages)                  # unreachable today (no published job is asked without the target check); fail closed anyway
        else:
            counts["fit_sync"] = fit_sync.run(client, live, gate)         # the unchanged sync: machine-owned Fit fields only, never Applied / Applied On / Saturn Decision
            with store.connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id WHERE j.id IN %s"
                               " AND (j.fit_synced_at IS NULL OR j.fit_synced_at < f.scored_at)", (tuple(pages),))
                counts["ledger_pages_pending"] = int(cursor.fetchone()[0])
        if counts["ledger_pages_pending"]:
            fit_cloud._degraded(counts, "LEDGER_SYNC_INCOMPLETE")         # the score is kept and the next sync retries; the batch still reports a failure
    counts["failed"] = counts["cloud_degraded"]                         # run.PARTIAL_FAILURE: a degraded batch exits 1 with FIT_CLOUD_DEGRADED
    return counts
