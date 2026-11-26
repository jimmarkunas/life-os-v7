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
import time

from lifeos.calendar_bridge.sync import BridgeError
from lifeos.network.errors import NetworkError
from lifeos.platform.db import StoreError
from lifeos.platform.jira import JiraError
from lifeos.platform.notion_client import NotionError
from lifeos.platform.gcal import GcalError
from lifeos.platform.outlook import OutlookError
from lifeos.platform import budget, isolate, source_registry as registry, stage_timing


def lazy(module, name="run", floor=0, live_only=False, **fixed):
    """A stage that imports `module` on first use and calls module.name(max(limit, floor), live, **fixed).

    With live_only it calls module.name(live=live, **fixed) and leaves the function's own limit alone.
    """
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


def _reconcile_daily(limit, live):
    """The daily Jira sprint reconcile, with the promotion call site passed in (the Jira package may not import accountability)."""
    from lifeos.accountability import promotion_call
    from lifeos.jira import reconcile_stage
    return reconcile_stage.run(limit, live, after=promotion_call.promotion)


def _outlook_auth(limit, live):
    from lifeos.outlook import stage
    return stage.auth(limit, live, replace=os.environ.get("OUTLOOK_REPLACE") == "true", write=os.environ.get("OUTLOOK_WRITE") == "true")


STAGES = {
    "resolve-jobright": _jobright,
    "resolve-linkedin": _linkedin,
    "resolve-dice": lazy("lifeos.jobs.resolve.aggregators.dice"),
    "hermes": lazy("lifeos.hermes_requests"),          # one signed Buzz request per new `hermes`-labelled Agent Go issue; dry run unless --live
    "alerts": lazy("lifeos.jobs.alerts"),
    "interview-probe": lazy("lifeos.interview.stage", "probe"),
    "interview-acceptance": lazy("lifeos.interview.acceptance"),
    "granola": lazy("lifeos.interview.granola_bridge", "stage"),     # carries one pinned Granola meeting (GRAN_PIN_DAY) from Oracle into its round; manual only
    "jira-snapshot": lazy("lifeos.jira.snapshot"),
    "bills-snapshot": lazy("lifeos.bills.snapshot"),
    "bills-paid": lazy("lifeos.bills.paid"),
    "bills-card": lazy("lifeos.bills.card"),
    "jira-rollover": lazy("lifeos.jira.rollover"),
    "jira-rollover-scheduled": lazy("lifeos.jira.rollover", auto=True),
    "jira-rollover-watch": lazy("lifeos.jira.watch"),
    "jira-reconcile-daily": _reconcile_daily,                              # daily 8 AM CT sprint reconciliation; off unless MEJ_SPRINT_AUTOMATION is on; live needs JIRA_WRITERS_VERIFIED "yes"
    "jira-probe": lazy("lifeos.jira.snapshot", "probe"),
    "jira-capture": lazy("lifeos.jira.capture"),                           # LIFE decisions into the shared memory through the governed tools; dry run unless --live
    "jira-card": lazy("lifeos.jira.card"),
    "jira-release": lazy("lifeos.jira.release"),                            # To Do tickets of an auto-release epic move to Agent Go when ready (at most 3 an hour, rank order); dry run unless live
    "jira-fill": lazy("lifeos.jira.fill_defaults"),                         # sets the missing epic, Component and Fix Version on LIFE tickets updated in the last 2 days; dry run unless --live
    "followup": lazy("lifeos.inbox.followup_stage"),                        # reconciles stored Followup threads with source deltas and publishes one component-state payload
    "jira-stalled": lazy("lifeos.jira.stalled"),                            # names LIFE tickets idle with no [waiting on] note; read only
    "inbox-schema-apply": lazy("lifeos.inbox.apply_schema", "apply_stage"),      # creates the four Action Inbox tables once, only when SCHEMA_CONFIRM is APPLY
    "inbox-schema-check": lazy("lifeos.inbox.schema"),                    # read-only check for the Action Inbox tables (counts and flags only)
    "inbox-view": lazy("lifeos.inbox.agent_inbox_stage"),                          # the Notion Agent Inbox view writer; manual QA proof (confirm = QA) and an hourly path switched by ACTION_INBOX_VIEW
    "followup-schema-apply": lazy("lifeos.inbox.followup_apply", "apply_stage"),   # creates the four v7_followup_* tables once, only when SCHEMA_CONFIRM is APPLY
    "followup-schema-check": lazy("lifeos.inbox.followup_schema"),            # read-only check for the Followup tables (counts and flags only)
    "accountability-week": lazy("lifeos.accountability.week"),            # "This week" for Jim and Matt on Jim & Matt V3, read-only from Jira
    "accountability-goals": lazy("lifeos.accountability.goals"),          # Quarterly goals, done / all per quarter epic
    "accountability-review": lazy("lifeos.accountability.review"),        # Quarterly review; past quarters are written once
    "accountability-artifacts": lazy("lifeos.accountability.artifacts"),    # Knowledge center: Jira issues labelled artifact, as title and link
    "accountability-changelog": lazy("lifeos.accountability.changelog"),    # Canon / Change Log; one dated line when a quarter begins, nothing else is edited
    "agenda-snapshot": lazy("lifeos.agenda.snapshot"),
    "agenda-card": lazy("lifeos.agenda.card"),
    "fit-profile-sync": lazy("lifeos.jobs.fit.profile_sync"),     # the approved Notion Fit profile block into the one Bitwarden extra secret (isolated job)
    "scale-up-holds": lazy("lifeos.jobs.holds"),
    "scale-up-links": lazy("lifeos.jobs.linkcheck"),
    "scale-up-exclusions": lazy("lifeos.jobs.exclusions"),
    "scale-up-exclusion-review": lazy("lifeos.jobs.exclusions", "review"),          # the high-scoring Scale-Up jobs removed by the Fit rules, to one private Notion page; counts in the clear
    "alerts-open": lazy("lifeos.jobs.alertstate"),
    "jobs-backlog": lazy("lifeos.jobs.backlog"),          # every job not yet on the Job Ledger and why it waits, to one dated private Notion page; counts in the clear
    "interview-ui-probe": lazy("lifeos.interview.ui_probe"),   # checks that the V7 REST transport can build the interview UI; one QA probe page only; never on a tick
    "fit-edge-test": lazy("lifeos.jobs.fit.edge"),                       # manual and synthetic-only; never on a tick
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
    "recruiter-outreach": lazy("lifeos.recruiters.outreach_card"),     # the Daily Report's Recruiter Outreach table, after the Recruiters stage; live writes need RECRUITER_OUTREACH=on
    "imessage-alerts": lazy("lifeos.imessage.stage"),
    "imessage-comms": lazy("lifeos.imessage.comms_card"),
    "capacity-card": lazy("lifeos.jobs.fit.capacity_card"),                  # the AI Capacity card; not configured while AI_CAPACITY_BLOCK_ID is unset
    "hiring-outcomes": lazy("lifeos.hiring.outcomes"),
    "outlook-auth": _outlook_auth,
    "outlook-probe": lazy("lifeos.outlook.stage", "probe"),
    "dcc-card": lazy("lifeos.dcc.card"),
    "report-archive": lazy("lifeos.dcc.weekly_archive"),                  # one static Daily Report snapshot a day into the Weekly Reports Archive (first run at or after 6 PM CT)
    "weekly-review": lazy("lifeos.dcc.weekly_review"),                   # the Weekly LIFE OS Review row, Friday 10 PM CT (first run at or after it); off unless WEEKLY_REVIEW is on
    "calendar-sync": lazy("lifeos.calendar_bridge.watch"),
    "network-inspect": lazy("lifeos.network.inspect"),
    "network-import": lazy("lifeos.network.importer"),
    "network-audit": lazy("lifeos.network.importer", "audit"),
    "probe": lazy("lifeos.probe"),
    "enrich": lazy("lifeos.jobs.enrich"),
    "fit": lazy("lifeos.jobs.fit.stage", floor=500),
    "fit-cloud-backlog": lazy("lifeos.jobs.fit.backlog"),           # a bounded Cloud Fit batch; manual only
    "audit": lazy("lifeos.jobs.audit", floor=2000),
    "audit-ready": lazy("lifeos.jobs.audit", floor=2000, ready_only=True),     # the seconds-long gate on the rows about to be published
    "publish": lazy("lifeos.jobs.publish"),
    "audit-fresh": lazy("lifeos.jobs.audit", floor=2000, ready_only=True),     # publish-first ticks gate the rows this run scored, then publish them
    "publish-fresh": lazy("lifeos.jobs.publish"),
    # Drain pass: the same stages again at the end of a tick, on the backlog, only while the run's time lasts (tick.PLAN bulk steps)
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
STAGES.update(registry.stages(lazy))                                    # the stages of the registered sources (lifeos/platform/source_registry.json)


# A stage listed here whose counts carry `failed > 0` exits 1 with this fixed code (counts are still printed), so the
# workflow step reports failure. Every other stage keeps its exit behavior. A registered source adds its own through
# `partial_failure` in its registry row.
PARTIAL_FAILURE = {"capacity-card": "CAPACITY_READ_FAILED", "publish": "PUBLISH_RECORDS_FAILED", "fit": "FIT_CLOUD_DEGRADED", "fit-cloud-backlog": "FIT_CLOUD_DEGRADED", "scale-up-exclusion-review": "PRIVATE_REPORT_FAILED", "jobs-backlog": "PRIVATE_REPORT_FAILED"}      # fit: only when the cloud shadow is enabled and failed
PARTIAL_FAILURE.update(registry.partial_failures())


def _step_code(counts):
    """Writes a degraded cloud Fit's fixed code (e.g. USAGE_UNAVAILABLE) to the step output `cloud_fit_code`.

    Fixed upper-case codes only; nothing else is written.
    """
    match = re.fullmatch(r"DEGRADED\(([A-Z0-9_]{1,48})\)", str(counts.get("cloud_fit") or ""))
    target = os.environ.get("GITHUB_OUTPUT")
    if match and target:
        with open(target, "a") as handle:
            handle.write(f"cloud_fit_code={match.group(1)}\n")


def _headline(stage, counts):
    """One plain sentence for the Granola line, from the counts and the fixed code only (never a title, name or text)."""
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
            pass                                                                       # the summary is optional; never fail a stage for it


def _timing(stage, label, began, counts=None):
    """Prints a standalone stage's own `stage_timing` line when STAGE_TIMING_FILE is set; the tick prints it for stages inside a tick."""
    if os.environ.get("STAGE_TIMING_FILE") and not stage_timing.in_tick():
        stage_timing.emit(stage_timing.record(stage, label, time.monotonic() - began, 0.0, counts, stage_timing.items_key_for(stage)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=sorted(STAGES))
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--live", action="store_true", help="write results (default: dry run, counts only)")
    args = parser.parse_args(argv)
    from lifeos.platform import settings                                               # noqa: PLC0415 - Runtime Configuration source; never fails a stage
    loaded = settings.apply(os.environ)
    from lifeos.platform import runtime_config                                         # noqa: PLC0415 - read-only health check of the source; never fails a stage
    shadowed = runtime_config.shadow(os.environ, once=True)
    seconds, required = budget.budget_for(args.stage)                                 # every stage has a budget (lifeos/platform/stage_budgets.json)
    began = time.monotonic()
    try:
        with budget.guard(seconds):
            counts = STAGES[args.stage](args.limit, args.live)
    except budget.StageBudgetExceeded:
        over, code = budget.over_budget(seconds, time.monotonic() - began, required)
        _timing(args.stage, "timeout", began)
        print(f"{args.stage.upper()} {'FAILED' if required else 'STOPPED'}: {budget.CODE}", file=sys.stderr)      # fixed code only; an optional stage records it and the run passes
        print(f"{args.stage}:", json.dumps(over, sort_keys=True))
        return code
    except (StoreError, NotionError, JiraError, OutlookError, GcalError, BridgeError, NetworkError) as error:
        print(f"{args.stage.upper()} FAILED: {error}", file=sys.stderr)
        _timing(args.stage, "failed", began)
        return 1
    stage_timing.note_counts(counts)                                                   # the tick reads these for the stage's item count
    _timing(args.stage, "ok", began, counts)
    if isinstance(counts, dict) and loaded["status"] not in ("off", "no_token"):       # only when the settings page was read (production)
        counts["settings"] = loaded
    if isinstance(counts, dict) and shadowed["status"] != "off":
        counts["runtime_config"] = shadowed
    _announce(args.stage, counts)
    print(f"{args.stage}:", json.dumps(counts, sort_keys=True))
    if args.live:
        from lifeos.jobs import daily                                                  # noqa: PLC0415 - the monitors' daily evidence; best effort, never fails a stage
        daily.record(args.stage, counts)
    failed = counts.get("failed") if isinstance(counts, dict) else None
    if args.stage in PARTIAL_FAILURE and isinstance(failed, int) and failed > 0:
        if isolate.stage_fails(counts) is False:                                       # some items failed but the stage did its job: failures are in the counts under fixed codes
            return 0
        print(f"{args.stage.upper()} FAILED: {PARTIAL_FAILURE[args.stage]}", file=sys.stderr)
        _step_code(counts)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
