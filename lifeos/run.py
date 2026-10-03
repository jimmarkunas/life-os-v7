"""One entry point for every pipeline stage:  python -m lifeos.run <stage> [--limit N] [--live]

A stage is a function (limit, live) -> counts. Add a stage by adding one line to STAGES: lazy("package.module", "function") calls
function(limit, live), with `floor` raising the limit and any other keyword passed through; the module is imported only when the stage runs.
Errors from a stage are fixed codes only. Output is one counts-only line (public repo, D7).
"""
import argparse
import functools
import importlib
import json
import os
import sys

from lifeos.calendar_bridge.sync import BridgeError
from lifeos.platform.db import StoreError
from lifeos.platform.jira import JiraError
from lifeos.platform.notion_client import NotionError
from lifeos.platform.gcal import GcalError
from lifeos.platform.outlook import OutlookError


def lazy(module, name="run", floor=0, **fixed):
    """A stage that imports `module` on first use and calls module.name(max(limit, floor), live, **fixed)."""
    def stage(limit, live):
        return getattr(importlib.import_module(module), name)(max(limit, floor), live, **fixed)
    stage.target = (module, name)                # lets a test prove every stage points at a real function
    return stage


def _jobright(limit, live):
    from lifeos.jobs.resolve.aggregators import jobright
    from lifeos.jobs.resolve import stage
    return stage.run("jobright", limit, live, jobright.resolve_many_sync)


def _lensa(limit, live):
    from lifeos.jobs.resolve.aggregators import lensa
    from lifeos.jobs.resolve import stage
    from lifeos.platform import limits
    from lifeos.jobs import store
    requeued = 0
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            if live:
                requeued = lensa.requeue_held(cursor)             # the one-time catch-up for D80; finds nothing after it has run
            screened = lensa.screen(cursor, live)                 # D88: settle what the other producers already settled, before any search is spent
    counts = stage.run_rows("lensa", limit, live, lensa.make_resolver(), deadline_minutes=limits.LENSA_DEADLINE_MINUTES)
    return {**counts, "requeued": requeued, **screened}


def _linkedin(limit, live):
    from lifeos.jobs.resolve.aggregators import linkedin
    from lifeos.jobs.resolve import stage
    from lifeos.jobs import store
    requeued = 0
    if live:
        with store.connect() as connection:
            store.ensure_schema(connection)
            with connection.cursor() as cursor:
                requeued = linkedin.requeue_held(cursor)               # the one-time catch-up for D79; finds nothing once it has run
    budget = {"left": linkedin.SEARCH_LIMIT}
    counts = stage.run_rows("linkedin-alerts", limit, live, functools.partial(linkedin.resolve_rows, budget=budget))
    return {**counts, "requeued": requeued}


def _outlook_auth(limit, live):
    from lifeos.outlook import stage
    return stage.auth(limit, live, replace=os.environ.get("OUTLOOK_REPLACE") == "true", write=os.environ.get("OUTLOOK_WRITE") == "true")


def _sync_seen(limit, live):
    from lifeos.jobs import repost
    return repost.sync_last_seen(live=live)


def _purge(limit, live):
    from lifeos.jobs import retention
    return retention.run(live)


STAGES = {
    "resolve-jobright": _jobright,
    "resolve-linkedin": _linkedin,
    "resolve-lensa": _lensa,
    "resolve-dice": lazy("lifeos.jobs.resolve.aggregators.dice"),
    "web": lazy("lifeos.sources.web.run"),
    "web-scale-up": lazy("lifeos.sources.web.run", lane="Scale-Up"),
    "discover-jobs": lazy("lifeos.sources.web.discover_jobs"),
    "alerts": lazy("lifeos.jobs.alerts"),
    "interview-probe": lazy("lifeos.interview.stage", "probe"),
    "interview": lazy("lifeos.interview.stage"),
    "interview-acceptance": lazy("lifeos.interview.acceptance"),
    "jira-snapshot": lazy("lifeos.jira.snapshot"),
    "bills-snapshot": lazy("lifeos.bills.snapshot"),
    "bills-paid": lazy("lifeos.bills.paid"),
    "jira-rollover": lazy("lifeos.jira.rollover"),
    "jira-rollover-scheduled": lazy("lifeos.jira.rollover", auto=True),
    "jira-probe": lazy("lifeos.jira.snapshot", "probe"),
    "jira-card": lazy("lifeos.jira.card"),
    "agenda-snapshot": lazy("lifeos.agenda.snapshot"),
    "agenda-card": lazy("lifeos.agenda.card"),
    "amazon-orders": lazy("lifeos.amazon.stage"),
    "outlook-auth": _outlook_auth,
    "outlook-probe": lazy("lifeos.outlook.stage", "probe"),
    "outlook-newsletters": lazy("lifeos.sources.newsletters.outlook"),
    "calendar-sync": lazy("lifeos.calendar_bridge.sync"),
    "openjobs": lazy("lifeos.sources.openjobs"),
    "sponsors": lazy("lifeos.sources.sponsor_register"),
    "probe": lazy("lifeos.probe"),
    "enrich": lazy("lifeos.jobs.enrich"),
    "fit": lazy("lifeos.jobs.fit.stage", floor=500),
    "audit": lazy("lifeos.jobs.audit", floor=2000),
    "publish": lazy("lifeos.jobs.publish"),
    "expire-holds": lazy("lifeos.jobs.hold"),
    "funnel": lazy("lifeos.jobs.funnel"),
    "sync-seen": _sync_seen,
    "purge": _purge,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=sorted(STAGES))
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--live", action="store_true", help="write results (default: dry run, counts only)")
    args = parser.parse_args(argv)
    try:
        counts = STAGES[args.stage](args.limit, args.live)
    except (StoreError, NotionError, JiraError, OutlookError, GcalError, BridgeError) as error:
        print(f"{args.stage.upper()} FAILED: {error}", file=sys.stderr)
        return 1
    print(f"{args.stage}:", json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
