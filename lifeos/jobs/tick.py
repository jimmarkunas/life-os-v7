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

RUN_BUDGET_SECONDS = 35 * 60          # the workflow job allows 45 minutes; the rest is setup and the fast-path floors
FAST_FLOOR_SECONDS = 60               # a fast-path stage is never skipped: late in a run it still gets this long


@dataclass(frozen=True)
class Step:
    name: str
    budget: int                       # seconds this stage may run
    limit: int = 40                   # its --limit (the same caps the separate workflow steps used)
    bulk: bool = False                # bulk discovery: runs after the fast path, only while the run's time lasts
    module: str = ""                  # a module whose main(argv) is the stage (finalize); otherwise a lifeos.run stage
    group: str = ""                   # LIFE-569: consecutive steps with the same group run side by side (threads of this one process) when TICK_CONCURRENCY=on; otherwise in order as always
    providers: tuple = ()             # LIFE-569: the outside services it calls; each has a cap (TICK_CAP_*), so no service sees more at once than today. "ats" is capped inside the reader


PLAN = (
    Step("resolve-jobright", 600, 40, group="A", providers=("jobright",)),
    Step("resolve-linkedin", 420, 150, group="A", providers=("linkedin",)),
    Step("linkedin-search", 180, 40, group="A", providers=("linkedin",)),
    Step("resolve-dice", 180, 15, group="A", providers=("dice",)),
    Step("web", 180, 12, group="A", providers=("ats",)),
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
    Step("boards", 1080, 900, bulk=True, group="B", providers=("ats",)),          # Oct 25 2026: 600 boards took 318-588 s (about 1 s a board on a slow hour), so 900 get 1080 s
    Step("web-scale-up", 300, 12, bulk=True, group="B", providers=("ats",)),
    Step("web-titlewatch", 240, bulk=True, group="B", providers=("tinyfish",)),
    Step("discover-jobs", 300, 12, bulk=True, group="B", providers=("tinyfish", "linkedin")),
    Step("sponsors", 120, bulk=True, group="B"),
    Step("boards-backfill", 600, 450, bulk=True),   # D217: after the hourly work, so a first pass never takes time from it; off unless BOARDS_BACKFILL=on
    # LIFE-76 drain pass (Jim, Oct 25: "drain this backlog quickly"): the backlog the first pass found, in the run's spare time, never before the hourly
    # work. Publish first (READY rows already scored, gated first), then read and score more for the next run; each step finds nothing once the queue is empty.
    Step("drain-audit-ready", 60, bulk=True),
    Step("drain-publish", 720, 120, bulk=True),      # Jim, Oct 25: publish more per run; ~2.8 s a page at noon (60 in 168 s), so 120 fit 720 s at twice that
    Step("drain-enrich", 480, 600, bulk=True),       # no TinyFish fallback: the Fetch day cap stays the hourly pass's; 300 took 122 s at noon, 450 took 235 s at 2:14 PM CT (600 for Jim's 5 PM CT drain, Oct 25)
    Step("drain-fit", 240, bulk=True),
)
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
    """LIFE-569 -> None (off: stages run in order, exactly as before) or {"caps": {provider: n}, "parallel": n}. Only TICK_CONCURRENCY=on turns it on; a cap that is not a whole number from 1 to 16
    falls back to its safe default, so a typo can never raise a cap. A cap of 1 reproduces today's behaviour for that service; "ats" is the one global limit on board reads across boards, web and
    web-scale-up (default: the same 8 workers a single stage uses today)."""
    environ = os.environ if environ is None else environ
    if (environ.get("TICK_CONCURRENCY") or "off").strip().lower() != "on":
        return None
    from lifeos.platform import limits                                                 # noqa: PLC0415
    return {"caps": {"linkedin": _small_int(environ.get("TICK_CAP_LINKEDIN"), CAP_DEFAULTS["linkedin"]), "tinyfish": _small_int(environ.get("TICK_CAP_TINYFISH"), CAP_DEFAULTS["tinyfish"]),
                     "jobright": _small_int(environ.get("TICK_CAP_JOBRIGHT"), CAP_DEFAULTS["jobright"]), "dice": _small_int(environ.get("TICK_CAP_DICE"), CAP_DEFAULTS["dice"]),
                     "ats": _small_int(environ.get("TICK_CAP_ATS"), limits.ATS_WORKERS)},
            "parallel": _small_int(environ.get("TICK_MAX_PARALLEL"), MAX_PARALLEL_DEFAULT)}


def _stop_thread(thread, flag):
    """Raise StageTimeout inside `thread` (the alarm signal only reaches the main thread). It takes effect when the thread is next running Python code, so a read blocked in the network ends at its own
    socket timeout; the flag stops it from firing at a stage that already finished."""
    with flag["lock"]:
        if not flag["done"] and thread.ident is not None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread.ident), ctypes.py_object(StageTimeout))


def _run_group(steps, live, quick, stages, clock, left, concurrency, outcome, timings):
    """LIFE-569: run consecutive independent steps side by side as threads of this process (one store tunnel: lifeos/platform/db.py opens it once per process). Each keeps its own budget, its own
    outcome and its own `stage ...` line; a stage that fails or times out is that stage's red entry and the others finish and save. Services are capped (concurrency["caps"]); a stage waits for its
    services' slots before its budget starts. Fills `outcome` and `timings` exactly like the sequential loop."""
    caps = concurrency["caps"]
    slots = {name: threading.BoundedSemaphore(caps[name]) for name in caps if name != "ats"}      # "ats" is capped inside the reader, per read
    parallel = threading.BoundedSemaphore(concurrency["parallel"])
    shared = threading.Lock()
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
            seconds = clock() - began
            with shared:
                outcome[step.name] = label or "failed"
                timings[step.name] = seconds
                print(f"stage {step.name}: {outcome[step.name]} {seconds:.1f}s", flush=True)

    longest = 0
    for step in steps:
        if step.bulk and left < 60:
            outcome[step.name] = "skipped"                                              # no time left for bulk work this run
            print(f"stage {step.name}: skipped 0.0s", flush=True)
            timings[step.name] = 0.0
            continue
        allowed = max(1, int(min(step.budget, left))) if step.bulk else min(step.budget, max(FAST_FLOOR_SECONDS, int(left)))
        limit = QUICK_LIMITS.get(step.name, step.limit) if quick else step.limit
        longest = max(longest, allowed)
        thread = threading.Thread(target=work, args=(step, allowed, limit), name=f"tick-{step.name}", daemon=True)
        thread.start()
        threads.append((step, thread))
    deadline = clock() + longest * (len(steps) + 1) + 60                                   # an outer bound only: every stage has its own timer
    for step, thread in threads:
        thread.join(max(deadline - clock(), 0))
        if thread.is_alive():
            with shared:
                outcome.setdefault(step.name, "timeout")


def tick(live, quick=False, plan=PLAN, stages=None, clock=time.monotonic, budget=RUN_BUDGET_SECONDS, timings=None, concurrency=None):
    """-> {stage: ok | failed | timeout | skipped}. `stages` (name -> callable(limit, live)) replaces lifeos.run in tests. LIFE-569: each stage prints one `stage <name>: <outcome> <seconds>s` line as
    it ends, and `timings` (when given) collects {stage: seconds}. `concurrency` (from concurrency_from_env; None = off) runs each run of consecutive steps that share a `group` side by side."""
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
                    _run_group(run_of, live, quick, stages, clock, budget - (clock() - started), concurrency, outcome, timings)
                    position += len(run_of)
                    continue
            position += 1
            left = budget - (clock() - started)
            if step.bulk and left < 60:
                outcome[step.name] = "skipped"                                      # no time left for bulk work this run; it resumes next run
                continue
            allowed = max(1, int(min(step.budget, left))) if step.bulk else min(step.budget, max(FAST_FLOOR_SECONDS, int(left)))   # bounded run; a fast-path stage keeps its floor
            limit = QUICK_LIMITS.get(step.name, step.limit) if quick else step.limit
            signal.alarm(allowed)
            stage_started = clock()
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
                seconds = clock() - stage_started
                if timings is not None:
                    timings[step.name] = seconds
                print(f"stage {step.name}: {outcome.get(step.name, 'failed')} {seconds:.1f}s", flush=True)           # LIFE-569: one line per stage, name, outcome and seconds
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
    timings, begun = {}, time.monotonic()
    concurrency = concurrency_from_env()
    if concurrency:                                                                    # LIFE-569: TICK_CONCURRENCY=on only; the global limit on board reads is set before any stage runs
        from lifeos.platform import read_slots                                         # noqa: PLC0415
        read_slots.configure("ats", concurrency["caps"]["ats"])
        print("tick: concurrency on, caps", json.dumps(concurrency["caps"], sort_keys=True), "parallel", concurrency["parallel"], flush=True)
    outcome = tick(args.live, args.quick, timings=timings, concurrency=concurrency)
    print("tick:", json.dumps(outcome, sort_keys=True))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:                                                                         # LIFE-569: a short table in the finish job summary
        try:
            with open(path, "a") as handle:
                handle.write(summary_table(outcome, timings, time.monotonic() - begun))
        except OSError:
            pass
    return 1 if any(v in ("failed", "timeout") for v in outcome.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
