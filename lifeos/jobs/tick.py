"""LIFE-76 PR 4b (D208): one process runs a whole Jobs tick, in a fixed order, through ONE store tunnel (D207).

    python -m lifeos.jobs.tick [--live] [--quick]

The workflow's prep job has already swept and extracted the mail. The fast path runs first, so jobs already found reach the Ledger before any bulk work: the resolvers -> the US Remote
boards -> publish the READY jobs (gated) -> enrich -> fit -> publish the fresh ones (gated) -> audit -> hold expiry -> finalize. Bulk discovery runs after it, while the run's time lasts: the employer
boards (D202), the Scale-Up lane (its own budget; D206 canon: shared store, Ledger and Fit), title watch, discovery search, the sponsor register.
Each stage has a wall-clock budget, cut to the run's remaining time (a fast-path stage never below FAST_FLOOR_SECONDS); a stage over it is stopped and counted `timeout`, a failing stage never stops the next one, and a bulk
stage that cannot start inside the run's remaining time is counted `skipped`. Output: the stages' own counts-only lines (the same `<stage>: {...}`
lines the separate steps printed), then one `tick:` line; exit 1 when any stage failed or timed out (the old "lane failures fail the run" rule).
"""
import argparse
import ctypes
from dataclasses import dataclass
import importlib
import json
import os
import signal
import sys
import threading
import time

from lifeos.platform import source_registry as registry

RUN_BUDGET_SECONDS = 35 * 60          # the workflow job allows 45 minutes; the rest is setup and the fast-path floors
FAST_FLOOR_SECONDS = 60               # a fast-path stage is never skipped: late in a run it still gets this long


@dataclass(frozen=True)
class Step:
    name: str
    budget: int                       # seconds this stage may run
    limit: int = 40                   # its --limit (the same caps the separate workflow steps used)
    bulk: bool = False                # bulk discovery: runs after the fast path, only while the run's time lasts
    module: str = ""                  # a module whose main(argv) is the stage (finalize); otherwise a lifeos.run stage
    group: str = ""                   # LIFE-569: consecutive steps with the same group run side by side (threads of this one process) (the caps below keep each outside service at its own limit)
    providers: tuple = ()             # LIFE-569: the outside services it calls; each has a cap (TICK_CAP_*), so no service sees more at once than today. "ats" is capped inside the reader


PLAN = (
    Step("resolve-jobright", 600, 40, group="A", providers=("jobright",)),
    Step("resolve-linkedin", 420, 150, group="A", providers=("linkedin",)),
    Step("resolve-dice", 180, 15, group="A", providers=("dice",)),
    # LIFE-76 (Jim, Oct 25: publish first): the jobs already read and scored reach the Ledger before this run reads new descriptions (noon tick: 7.5 min to the
    # first page, 2.3 of them in enrich); the rows this run scores follow in publish-fresh
    Step("audit-ready", 60),                    # LIFE-76: nothing that fails the guards is published; only the READY rows, no page reads, seconds
    Step("publish", 720, 40),                   # ~13 s a page incl. read-back and Fit sync: 40 fit the 12-minute budget (tick 39170290502 timed out at 60)
    Step("enrich", 480, 150),
    Step("fit", 240),
    Step("audit-fresh", 60),                    # the same gate on the rows enrich and fit just made READY
    Step("publish-fresh", 300, 20),
    Step("audit", 180),                         # the full pass after publish: published pages, duplicates and Exclude clean-up, so it never delays the Ledger
    Step("expire-holds", 120),
    Step("finalize", 180, 300, module="lifeos.sources.newsletters.finalize"),     # its own default; the workflow step never passed one
    # LIFE-76 drain pass (Jim, Oct 25: "drain this backlog quickly"): the backlog the first pass found, in the run's spare time, never before the hourly
    # work. Publish first (READY rows already scored, gated first), then read and score more for the next run; each step finds nothing once the queue is empty.
    Step("drain-audit-ready", 60, bulk=True),
    Step("drain-publish", 720, 120, bulk=True),      # Jim, Oct 25: publish more per run; ~2.8 s a page at noon (60 in 168 s), so 120 fit 720 s at twice that
    Step("drain-enrich", 480, 600, bulk=True),       # no TinyFish fallback: the Fetch day cap stays the hourly pass's; 300 took 122 s at noon, 450 took 235 s at 2:14 PM CT (600 for Jim's 5 PM CT drain, Oct 25)
    Step("drain-fit", 240, bulk=True),
)
PLAN = registry.with_tick_steps(PLAN, Step)                                # LIFE-618: the registered sources' steps (lifeos/platform/source_registry.json), each after the step it names
QUICK_LIMITS = {"boards": 50, "publish": 10, "publish-fresh": 5, "boards-backfill": 50, "drain-publish": 10, "drain-enrich": 50}   # a manual quick proof run (D206, D208) reads few boards and publishes few pages


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


CAP_DEFAULTS = {"linkedin": 1, "tinyfish": 1, "jobright": 1, "dice": 1}           # LIFE-569: one at a time each, exactly today's behaviour for these services
MAX_PARALLEL_DEFAULT = 4


def _small_int(raw, default, low=1, high=16):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def concurrency_from_env(environ=None):
    """LIFE-569, always on since LIFE-604 -> {"caps": {provider: n}, "parallel": n}. The on/off switch is gone: stages of a group always run side by side. A cap that is not a whole number
    from 1 to 16 falls back to its safe default, so a typo can never raise a cap. A cap of 1 reproduces the one-at-a-time behaviour for that service; "ats" is the one global limit on board reads across
    boards, web and web-scale-up (default: the same 8 workers a single stage uses today)."""
    environ = os.environ if environ is None else environ
    from lifeos.platform import limits                                                 # noqa: PLC0415
    caps = {name: _small_int(environ.get("TICK_CAP_" + name.upper()), default) for name, default in CAP_DEFAULTS.items()}
    return {"caps": {**caps, "ats": _small_int(environ.get("TICK_CAP_ATS"), limits.ATS_WORKERS)},
            "parallel": _small_int(environ.get("TICK_MAX_PARALLEL"), MAX_PARALLEL_DEFAULT)}


def _stop_thread(thread, flag):
    """Raise StageTimeout inside `thread` (the alarm signal only reaches the main thread). It takes effect when the thread is next running Python code, so a read blocked in the network ends at its own
    socket timeout; the flag stops it from firing at a stage that already finished."""
    with flag["lock"]:
        if not flag["done"] and thread.ident is not None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread.ident), ctypes.py_object(StageTimeout))


ABANDON_GRACE_SECONDS = 5             # LIFE-604: after a stage's own budget, a side-by-side stage gets this long to end before the run stops waiting for it


def _allot(step, left, quick):
    """LIFE-604: the ONE rule both runners use. -> (seconds the stage may run, its --limit), or None when a bulk stage has under a minute of the run left (it is skipped, and resumes next run).
    A fast-path stage is never below FAST_FLOOR_SECONDS."""
    if step.bulk and left < 60:
        return None
    allowed = max(1, int(min(step.budget, left))) if step.bulk else min(step.budget, max(FAST_FLOOR_SECONDS, int(left)))
    return allowed, (QUICK_LIMITS.get(step.name, step.limit) if quick else step.limit)


def _report(step, label, seconds, outcome, timings, lock=None):
    """LIFE-604: one stage's result in one place: the outcome, the seconds and the `stage <name>: <outcome> <seconds>s` line (LIFE-569). Every stage prints exactly one, skipped ones included."""
    with lock or threading.Lock():
        outcome[step.name] = label
        timings[step.name] = seconds
        print(f"stage {step.name}: {label} {seconds:.1f}s", flush=True)


def _run_group(steps, live, quick, stages, clock, remaining, concurrency, outcome, timings):
    """LIFE-569: run consecutive independent steps side by side as threads of this process (one store tunnel: lifeos/platform/db.py opens it once per process). Each keeps its own budget, its own
    outcome and its own `stage ...` line; a stage that fails or times out is that stage's red entry and the others finish and save. Services are capped (concurrency["caps"]); a stage waits for its
    services' slots before its budget starts. Fills `outcome` and `timings` exactly like the sequential loop. `remaining()` is the run's time left, asked once per stage as it is started.
    Timeouts here are a timer thread that raises StageTimeout inside the stage's thread (the alarm signal only reaches the main thread); a read blocked in the operating system ignores that until it
    returns, so the run stops waiting for such a stage after its budget plus ABANDON_GRACE_SECONDS and names it (`stage abandoned: <name>`)."""
    caps = concurrency["caps"]
    slots = {name: threading.BoundedSemaphore(caps[name]) for name in caps if name != "ats"}      # "ats" is capped inside the reader, per read
    parallel = threading.BoundedSemaphore(concurrency["parallel"])
    shared = threading.RLock()                                                          # re-entrant: the check for "abandoned" and the report are one step
    abandoned = set()
    threads = []

    def work(step, allowed, limit):
        held = []
        flag = {"done": False, "lock": threading.Lock()}
        began = clock()
        label = None
        try:
            with parallel:
                for name in sorted(p for p in step.providers if p in slots):                  # a fixed order, so two stages that need two services each can never wait on each other
                    slots[name].acquire()
                    held.append(name)
                began = clock()
                timer = threading.Timer(allowed, _stop_thread, args=(threading.current_thread(), flag))
                timer.daemon = True
                timer.start()
                try:
                    label = _run_step(step, live, limit, stages)
                except StageTimeout:
                    label = "timeout"
                    print(f"{step.name.upper()} FAILED: STAGE_TIMEOUT_{allowed}S", file=sys.stderr)
                except Exception as error:                                             # noqa: BLE001 - one stage never stops the next; the code is fixed
                    label = "failed"
                    print(f"{step.name.upper()} FAILED: {type(error).__name__}", file=sys.stderr)
                finally:
                    with flag["lock"]:
                        flag["done"] = True
                    timer.cancel()
        except StageTimeout:                                                           # the timer fired a moment after the stage finished
            label = label or "timeout"
        finally:
            for name in held:
                slots[name].release()
            with shared:
                if step.name not in abandoned:                                         # the run stopped waiting and already reported it: a late return changes nothing
                    _report(step, label or "failed", clock() - began, outcome, timings, shared)

    for step in steps:
        plan_for = _allot(step, remaining(), quick)
        if plan_for is None:
            _report(step, "skipped", 0.0, outcome, timings, shared)
            continue
        allowed, limit = plan_for
        thread = threading.Thread(target=work, args=(step, allowed, limit), name=f"tick-{step.name}", daemon=True)
        thread.start()
        threads.append((step, thread, allowed, time.monotonic() + allowed + ABANDON_GRACE_SECONDS))      # a stage's own bound: its budget, one grace, from the moment it started
    for step, thread, allowed, stop_waiting in threads:                                  # every stage has its own timer; this is only the bound for one that cannot hear it
        thread.join(max(stop_waiting - time.monotonic(), 0))
        if thread.is_alive():
            with shared:
                if step.name not in outcome:
                    abandoned.add(step.name)
                    _report(step, "timeout", float(allowed), outcome, timings, shared)
                    print(f"stage abandoned: {step.name}", flush=True)


def tick(live, quick=False, plan=PLAN, stages=None, clock=time.monotonic, budget=RUN_BUDGET_SECONDS, timings=None, concurrency=None):
    """-> {stage: ok | failed | timeout | skipped}. `stages` (name -> callable(limit, live)) replaces lifeos.run in tests. LIFE-569: each stage prints one `stage <name>: <outcome> <seconds>s` line as
    it ends, and `timings` (when given) collects {stage: seconds}. `concurrency` (from concurrency_from_env; None = off) runs each run of consecutive steps that share a `group` side by side.
    LIFE-604: both runners use `_allot` and `_report`; the sequential one stops a stage with the alarm signal, the side-by-side one with a timer thread (see `_run_group`)."""
    started, outcome = clock(), {}
    timings = {} if timings is None else timings
    previous = signal.signal(signal.SIGALRM, _alarm)
    try:
        position = 0
        while position < len(plan):
            step = plan[position]
            if concurrency and step.group:
                run_of = []
                for candidate in plan[position:]:
                    if candidate.group != step.group:
                        break
                    run_of.append(candidate)
                if len(run_of) > 1:
                    _run_group(run_of, live, quick, stages, clock, lambda: budget - (clock() - started), concurrency, outcome, timings)
                    position += len(run_of)
                    continue
            position += 1
            plan_for = _allot(step, budget - (clock() - started), quick)
            if plan_for is None:
                _report(step, "skipped", 0.0, outcome, timings)                      # no time left for bulk work this run; it resumes next run
                continue
            allowed, limit = plan_for
            signal.alarm(allowed)
            stage_started = clock()
            label = "failed"
            try:
                label = _run_step(step, live, limit, stages)
            except StageTimeout:
                label = "timeout"
                print(f"{step.name.upper()} FAILED: STAGE_TIMEOUT_{allowed}S", file=sys.stderr)
            except Exception as error:                                              # noqa: BLE001 - one stage never stops the next; the code is fixed
                print(f"{step.name.upper()} FAILED: {type(error).__name__}", file=sys.stderr)
            finally:
                signal.alarm(0)
                _report(step, label, clock() - stage_started, outcome, timings)
    finally:
        signal.signal(signal.SIGALRM, previous)
    return {step.name: outcome[step.name] for step in plan if step.name in outcome}      # plan order, whatever order the side-by-side stages finished in


def summary_table(outcome, timings, total):
    """LIFE-569: the stage table for the job summary: name, outcome, seconds, share of the Jobs tick; skipped stages show 0. Counts stay in each stage's own line."""
    lines = ["### Jobs tick: seconds per stage", "", "| Stage | Outcome | Seconds | Share |", "|---|---|---:|---:|"]
    for name, result in outcome.items():
        seconds = timings.get(name, 0.0)
        lines.append(f"| {name} | {result} | {seconds:.1f} | {100 * seconds / total:.0f}% |" if total > 0 else f"| {name} | {result} | {seconds:.1f} | - |")
    lines.append(f"| **total** | | **{total:.1f}** | |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--quick", action="store_true", help="small caps for a manual end-to-end proof (D206)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)                                     # each stage's counts line reaches the log when it is printed, not at exit
    from lifeos.platform import bench                                                # noqa: PLC0415 - LIFE-604: all benchmark logic lives there; this file only calls it
    concurrency = concurrency_from_env()
    settings = bench.tick_settings(concurrency)
    if settings:                                                                     # LIFE-577: benchmark mode is always a dry run, and says what is in force before any stage starts
        if args.live:
            print("tick: BENCHMARK_REFUSED_LIVE", file=sys.stderr)
            return 2
        print("benchmark: settings in force:", json.dumps(settings, sort_keys=True), flush=True)
    timings, begun = {}, time.monotonic()
    if concurrency:                                                                    # LIFE-569: the global limit on board reads is set before any stage runs
        from lifeos.platform import read_slots                                         # noqa: PLC0415
        read_slots.configure("ats", concurrency["caps"]["ats"])
        print("tick: concurrency on, caps", json.dumps(concurrency["caps"], sort_keys=True), "parallel", concurrency["parallel"], flush=True)
    outcome = tick(args.live, args.quick, timings=timings, concurrency=concurrency)
    print("tick:", json.dumps(outcome, sort_keys=True))
    wall = time.monotonic() - begun
    if settings:
        print(bench.tick_totals(outcome, timings, wall), flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:                                                                         # LIFE-569: a short table in the finish job summary
        try:
            with open(path, "a") as handle:
                if settings:
                    handle.write(bench.tick_summary(settings, outcome, timings, wall))
                handle.write(summary_table(outcome, timings, wall))
        except OSError:
            pass
    return 1 if any(v in ("failed", "timeout") for v in outcome.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
