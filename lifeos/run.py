"""One entry point for every pipeline stage:  python -m lifeos.run <stage> [--limit N] [--live]

A stage is a function (limit, live) -> counts. A job source is one row in lifeos/platform/source_registry.json (see lifeos/platform/source_registry.py); any other stage is one line in STAGES: lazy("package.module", "function") calls
function(limit, live), with `floor` raising the limit and any other keyword passed through; the module is imported only when the stage runs.
Errors from a stage are fixed codes only. Output is one counts-only line (public repo, D7).
"""
import argparse
import functools
import importlib
import json
import os
import re
import sys

from lifeos.calendar_bridge.sync import BridgeError
from lifeos.network.errors import NetworkError
from lifeos.platform.db import StoreError
from lifeos.platform.jira import JiraError
from lifeos.platform.notion_client import NotionError
from lifeos.platform.gcal import GcalError
from lifeos.platform.outlook import OutlookError
from lifeos.platform import source_registry as registry


def lazy(module, name="run", floor=0, live_only=False, **fixed):
    """A stage that imports `module` on first use and calls module.name(max(limit, floor), live, **fixed); with live_only it calls module.name(live=live, **fixed) and leaves the function's own limit alone."""
    def stage(limit, live):
        function = getattr(importlib.import_module(module), name)
        return function(live=live, **fixed) if live_only else function(max(limit, floor), live, **fixed)
    stage.target = (module, name)                # lets a test prove every stage points at a real function
    return stage


def _jobright(limit, live):
    from lifeos.jobs.resolve.aggregators import jobright
    from lifeos.jobs.resolve import stage
    return stage.run_rows("jobright", limit, live, lambda rows: jobright.resolve_many_sync([row[1] for row in rows]), batch=max(limit, 1))     # one login for the whole pick


def _linkedin(limit, live):
    from lifeos.jobs.resolve.aggregators import linkedin
    from lifeos.jobs.resolve import stage
    budget = {"left": linkedin.SEARCH_LIMIT}
    return stage.run_rows("linkedin-alerts", limit, live, functools.partial(linkedin.resolve_rows, budget=budget))


def _outlook_auth(limit, live):
    from lifeos.outlook import stage
    return stage.auth(limit, live, replace=os.environ.get("OUTLOOK_REPLACE") == "true", write=os.environ.get("OUTLOOK_WRITE") == "true")


STAGES = {
    "resolve-jobright": _jobright,
    "resolve-linkedin": _linkedin,
    "resolve-dice": lazy("lifeos.jobs.resolve.aggregators.dice"),
    "hermes": lazy("lifeos.hermes_requests"),          # LIFE-307: one signed Buzz request per new `hermes`-labelled Agent Go issue; dry run unless --live
    "alerts": lazy("lifeos.jobs.alerts"),
    "interview-probe": lazy("lifeos.interview.stage", "probe"),
    "interview-acceptance": lazy("lifeos.interview.acceptance"),
    "granola": lazy("lifeos.interview.granola_bridge", "stage"),     # LIFE-37 Step 2: carry ONE pinned Granola meeting (GRAN_PIN_DAY) from Oracle into its round; manual only, no schedule
    "jira-snapshot": lazy("lifeos.jira.snapshot"),
    "bills-snapshot": lazy("lifeos.bills.snapshot"),
    "bills-paid": lazy("lifeos.bills.paid"),
    "bills-card": lazy("lifeos.bills.card"),
    "jira-rollover": lazy("lifeos.jira.rollover"),
    "jira-rollover-scheduled": lazy("lifeos.jira.rollover", auto=True),
    "jira-rollover-watch": lazy("lifeos.jira.watch"),
    "jira-probe": lazy("lifeos.jira.snapshot", "probe"),
    "jira-card": lazy("lifeos.jira.card"),
    "agenda-snapshot": lazy("lifeos.agenda.snapshot"),
    "agenda-card": lazy("lifeos.agenda.card"),
    "fit-profile-sync": lazy("lifeos.jobs.fit.profile_sync"),     # Fit profile: the approved Notion block into the one Bitwarden extra secret (isolated job, profile-sync.yml)
    "scale-up-holds": lazy("lifeos.jobs.holds"),
    "scale-up-links": lazy("lifeos.jobs.linkcheck"),
    "scale-up-exclusions": lazy("lifeos.jobs.exclusions"),
    "scale-up-exclusion-review": lazy("lifeos.jobs.exclusions", "review"),          # LIFE-121: the high-scoring Scale-Up jobs Jim's own Fit rules removed, to one private Notion page (diag.yml, report job); counts in the clear
    "alerts-open": lazy("lifeos.jobs.alertstate"),
    "jobs-backlog": lazy("lifeos.jobs.backlog"),          # LIFE-212 (a): every job not on the Job Ledger yet and why it waits, to one dated private Notion page (diag.yml, report job); counts in the clear
    "interview-ui-probe": lazy("lifeos.interview.ui_probe"),   # LIFE-88 first gate: can the V7 REST transport build UI 3c (diag.yml job `interview_ui`, typed QA); one QA probe page only; never on a tick
    "fit-edge-test": lazy("lifeos.jobs.fit.edge"),                       # LIFE-60: manual and synthetic-only (fit-edge-test.yml); never on a tick
    "safety-sql-smoke": lazy("lifeos.jobs.safety", "run_sql_smoke"),
    "safety-fit-capture": lazy("lifeos.jobs.safety", "run_fit_capture"),
    "safety-fit-golden": lazy("lifeos.jobs.safety", "run_fit_golden"),
    "amazon-orders": lazy("lifeos.amazon.stage", floor=200),
    "amazon-card": lazy("lifeos.amazon.card"),
    "amazon-retention": lazy("lifeos.amazon.retention"),
    "amazon-review-reset": lazy("lifeos.amazon.reset"),
    "mail-alerts": lazy("lifeos.physmail.stage", floor=1000),
    "mail-card": lazy("lifeos.physmail.card"),
    "amazon-backfill": lazy("lifeos.amazon.backfill", floor=2000),
    "amazon-census": lazy("lifeos.delivery.census", floor=2000),
    "recruiters": lazy("lifeos.recruiters.stage"),
    "recruiter-outreach": lazy("lifeos.recruiters.outreach_card"),     # LIFE-20: the Daily Report's Recruiter Outreach table, after the Recruiters stage; live writes need RECRUITER_OUTREACH=on
    "imessage-alerts": lazy("lifeos.imessage.stage"),
    "imessage-comms": lazy("lifeos.imessage.comms_card"),
    "capacity-card": lazy("lifeos.jobs.fit.capacity_card"),                  # LIFE-77 D228: the AI Capacity card; not configured (AI_CAPACITY_BLOCK_ID unset) until Jim activates it
    "hiring-outcomes": lazy("lifeos.hiring.outcomes"),
    "outlook-auth": _outlook_auth,
    "outlook-probe": lazy("lifeos.outlook.stage", "probe"),
    "dcc-card": lazy("lifeos.dcc.card"),
    "calendar-sync": lazy("lifeos.calendar_bridge.watch"),
    "network-inspect": lazy("lifeos.network.inspect"),
    "network-import": lazy("lifeos.network.importer"),
    "network-audit": lazy("lifeos.network.importer", "audit"),
    "probe": lazy("lifeos.probe"),
    "enrich": lazy("lifeos.jobs.enrich"),
    "fit": lazy("lifeos.jobs.fit.stage", floor=500),
    "fit-cloud-backlog": lazy("lifeos.jobs.fit.backlog"),           # D219: Jim's separately approved bounded Cloud Fit batch (manual only)
    "audit": lazy("lifeos.jobs.audit", floor=2000),
    "audit-ready": lazy("lifeos.jobs.audit", floor=2000, ready_only=True),     # LIFE-76: the seconds-long gate on the rows about to be published
    "publish": lazy("lifeos.jobs.publish"),
    "audit-fresh": lazy("lifeos.jobs.audit", floor=2000, ready_only=True),     # LIFE-76: publish-first ticks gate the rows this run scored, then publish them
    "publish-fresh": lazy("lifeos.jobs.publish"),
    # LIFE-76 drain pass: the same stages again at the end of a tick, on the backlog, only while the run's time lasts (tick.PLAN bulk steps)
    "drain-audit-ready": lazy("lifeos.jobs.audit", floor=2000, ready_only=True),
    "drain-publish": lazy("lifeos.jobs.publish"),
    "drain-enrich": lazy("lifeos.jobs.enrich", drain=True),
    "drain-fit": lazy("lifeos.jobs.fit.stage", floor=500),
    "expire-holds": lazy("lifeos.jobs.hold"),
    "funnel": lazy("lifeos.jobs.funnel"),
    "fit-review": lazy("lifeos.jobs.review"),
    "explain": lazy("lifeos.jobs.explain"),
    "sync-seen": lazy("lifeos.jobs.repost", "sync_last_seen", live_only=True),
    "purge": lazy("lifeos.jobs.retention", live_only=True),
}
STAGES.update(registry.stages(lazy))                                    # LIFE-618: the stages of the registered sources (lifeos/platform/source_registry.json)


# D182: a stage whose counts carry `failed > 0` exits 1 with this fixed code (counts are still printed), so the workflow step reports failure, the
# finish job's lane list names it, and the existing report job pushes it. Only the stages listed here; every other stage keeps its exit behavior. A registered source adds its own through `partial_failure` in its registry row.
PARTIAL_FAILURE = {"capacity-card": "CAPACITY_READ_FAILED", "publish": "PUBLISH_RECORDS_FAILED", "fit": "FIT_CLOUD_DEGRADED", "fit-cloud-backlog": "FIT_CLOUD_DEGRADED", "scale-up-exclusion-review": "PRIVATE_REPORT_FAILED", "jobs-backlog": "PRIVATE_REPORT_FAILED"}      # fit: D214, only when the cloud shadow is enabled and failed; jobs-backlog: LIFE-212 (a), the backlog report page
PARTIAL_FAILURE.update(registry.partial_failures())


def _step_code(counts):
    """LIFE-41: a degraded cloud Fit's fixed code (e.g. USAGE_UNAVAILABLE) becomes the step output `cloud_fit_code`, so the finish job's lane list and the
    existing phone push name it. Fixed upper-case codes only; nothing else is ever written."""
    match = re.fullmatch(r"DEGRADED\(([A-Z0-9_]{1,48})\)", str(counts.get("cloud_fit") or ""))
    target = os.environ.get("GITHUB_OUTPUT")
    if match and target:
        with open(target, "a") as handle:
            handle.write(f"cloud_fit_code={match.group(1)}\n")


def _headline(stage, counts):
    """Jim, Nov 6, 2026: the Granola line in the job log was one long JSON line nobody could read at a glance. One plain sentence, from the counts and the fixed code only (never a title, name or text)."""
    if stage != "granola-auto" or not isinstance(counts, dict) or "meetings" not in counts:
        return None
    def number(key):
        value = counts.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0
    code = counts.get("code") if re.fullmatch(r"[A-Z][A-Z0-9_]{1,48}", str(counts.get("code") or "")) else "no code"
    return (f"Granola auto: {code}. On Oracle: {number('meetings')}; already filed: {number('already')}; filed this run: {number('carried')}; "
            f"waiting for Jim (Unfiled): {number('unfiled_waiting') + number('unfiled_created')}; failed: {number('failed')}.")


def _announce(stage, counts):
    line = _headline(stage, counts)
    if line is None:
        return
    print(line)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        try:
            with open(target, "a") as handle:
                handle.write(f"### {stage}\n\n{line}\n")
        except OSError:
            pass                                                                       # a summary is a convenience; never fail a stage for it


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=sorted(STAGES))
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--live", action="store_true", help="write results (default: dry run, counts only)")
    args = parser.parse_args(argv)
    try:
        counts = STAGES[args.stage](args.limit, args.live)
    except (StoreError, NotionError, JiraError, OutlookError, GcalError, BridgeError, NetworkError) as error:
        print(f"{args.stage.upper()} FAILED: {error}", file=sys.stderr)
        return 1
    _announce(args.stage, counts)
    print(f"{args.stage}:", json.dumps(counts, sort_keys=True))
    if args.live:
        from lifeos.jobs import daily                                                  # noqa: PLC0415 - LIFE-74: the monitors' daily evidence; best effort, never fails a stage
        daily.record(args.stage, counts)
    failed = counts.get("failed") if isinstance(counts, dict) else None
    if args.stage in PARTIAL_FAILURE and isinstance(failed, int) and failed > 0:
        print(f"{args.stage.upper()} FAILED: {PARTIAL_FAILURE[args.stage]}", file=sys.stderr)
        _step_code(counts)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
