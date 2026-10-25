"""LIFE-76 PR 4b (D208): one process runs a whole Jobs tick, in a fixed order, through ONE store tunnel (D207).

    python -m lifeos.jobs.tick [--live] [--quick]

The workflow's prep job has already swept and extracted the mail. The fast path runs first, so jobs already found reach the Ledger before any bulk work: the resolvers -> the US Remote
boards -> enrich -> fit -> audit -> publish -> hold expiry -> finalize. Bulk discovery runs after it, while the run's time lasts: the employer
boards (D202), the Scale-Up lane (its own budget; D206 canon: shared store, Ledger and Fit), title watch, discovery search, the sponsor register.
Each stage has a wall-clock budget, cut to the run's remaining time (a fast-path stage never below FAST_FLOOR_SECONDS); a stage over it is stopped and counted `timeout`, a failing stage never stops the next one, and a bulk
stage that cannot start inside the run's remaining time is counted `skipped`. Output: the stages' own counts-only lines (the same `<stage>: {...}`
lines the separate steps printed), then one `tick:` line; exit 1 when any stage failed or timed out (the old "lane failures fail the run" rule).
"""
import argparse
from dataclasses import dataclass
import importlib
import json
import signal
import sys
import time

RUN_BUDGET_SECONDS = 35 * 60          # the workflow job allows 45 minutes; the rest is setup and the fast-path floors
FAST_FLOOR_SECONDS = 60               # a fast-path stage is never skipped: late in a run it still gets this long


@dataclass(frozen=True)
class Step:
    name: str
    budget: int                       # seconds this stage may run
    limit: int = 40                   # its --limit (the same caps the separate workflow steps used)
    bulk: bool = False                # bulk discovery: runs after the fast path, only while the run's time lasts
    module: str = ""                  # a module whose main(argv) is the stage (finalize); otherwise a lifeos.run stage


PLAN = (
    Step("resolve-jobright", 600, 40),
    Step("resolve-linkedin", 420, 150),
    Step("linkedin-search", 180, 40),
    Step("resolve-dice", 180, 15),
    Step("web", 180, 12),
    Step("enrich", 480, 150),
    Step("fit", 240),
    Step("audit-ready", 60),                    # LIFE-76: nothing that fails the guards is published; only the READY rows, no page reads, seconds
    Step("publish", 720, 40),                   # ~13 s a page incl. read-back and Fit sync: 40 fit the 12-minute budget (tick 39170290502 timed out at 60)
    Step("audit", 180),                         # the full pass after publish: published pages, duplicates and Exclude clean-up, so it never delays the Ledger
    Step("expire-holds", 120),
    Step("finalize", 180, 300, module="lifeos.sources.newsletters.finalize"),     # its own default; the workflow step never passed one
    Step("boards", 720, 900, bulk=True),
    Step("web-scale-up", 300, 12, bulk=True),
    Step("web-titlewatch", 240, bulk=True),
    Step("discover-jobs", 300, 12, bulk=True),
    Step("sponsors", 120, bulk=True),
    Step("boards-backfill", 600, 450, bulk=True),   # D217: after the hourly work, so a first pass never takes time from it; off unless BOARDS_BACKFILL=on
    # LIFE-76 drain pass (Jim, Oct 25: "drain this backlog quickly"): the backlog the first pass found, in the run's spare time, never before the hourly
    # work. Publish first (READY rows already scored, gated first), then read and score more for the next run; each step finds nothing once the queue is empty.
    Step("drain-audit-ready", 60, bulk=True),
    Step("drain-publish", 480, 60, bulk=True),       # limits.NOTION_PER_RUN pages a call; ~2-3 s a page today (Oct 25), so 60 fit 480 s with room for a slow Notion minute
    Step("drain-enrich", 480, 300, bulk=True),       # no TinyFish fallback: the Fetch day cap stays the hourly pass's
    Step("drain-fit", 240, bulk=True),
)
QUICK_LIMITS = {"boards": 50, "publish": 10, "boards-backfill": 50, "drain-publish": 10, "drain-enrich": 50}   # a manual quick proof run (D206, D208) reads few boards and publishes few pages


class StageTimeout(BaseException):
    """Raised by the alarm. A BaseException, so a stage's own `except Exception` cannot swallow it."""


def _alarm(_signum, _frame):
    raise StageTimeout()


def _run_step(step, live, limit, stages):
    if step.module:
        argv = (["--live"] if live else []) + ["--limit", str(limit)]
        code = importlib.import_module(step.module).main(argv)
        return "ok" if code == 0 else "failed"
    from lifeos import run                                                          # noqa: PLC0415 - the one stage registry
    code = run.main([step.name, "--limit", str(limit)] + (["--live"] if live else [])) if stages is None else stages[step.name](limit, live)
    return "ok" if code in (0, None) or isinstance(code, dict) else "failed"


def tick(live, quick=False, plan=PLAN, stages=None, clock=time.monotonic, budget=RUN_BUDGET_SECONDS):
    """-> {stage: ok | failed | timeout | skipped}. `stages` (name -> callable(limit, live)) replaces lifeos.run in tests."""
    started, outcome = clock(), {}
    previous = signal.signal(signal.SIGALRM, _alarm)
    try:
        for step in plan:
            left = budget - (clock() - started)
            if step.bulk and left < 60:
                outcome[step.name] = "skipped"                                      # no time left for bulk work this run; it resumes next run
                continue
            allowed = max(1, int(min(step.budget, left))) if step.bulk else min(step.budget, max(FAST_FLOOR_SECONDS, int(left)))   # bounded run; a fast-path stage keeps its floor
            limit = QUICK_LIMITS.get(step.name, step.limit) if quick else step.limit
            signal.alarm(allowed)
            try:
                outcome[step.name] = _run_step(step, live, limit, stages)
            except StageTimeout:
                outcome[step.name] = "timeout"
                print(f"{step.name.upper()} FAILED: STAGE_TIMEOUT_{allowed}S", file=sys.stderr)
            except Exception as error:                                              # noqa: BLE001 - one stage never stops the next; the code is fixed
                outcome[step.name] = "failed"
                print(f"{step.name.upper()} FAILED: {type(error).__name__}", file=sys.stderr)
            finally:
                signal.alarm(0)
    finally:
        signal.signal(signal.SIGALRM, previous)
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--quick", action="store_true", help="small caps for a manual end-to-end proof (D206)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)                                     # each stage's counts line reaches the log when it is printed, not at exit
    outcome = tick(args.live, args.quick)
    print("tick:", json.dumps(outcome, sort_keys=True))
    return 1 if any(v in ("failed", "timeout") for v in outcome.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
