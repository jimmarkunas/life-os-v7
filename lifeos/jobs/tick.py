"""One process runs a whole Jobs tick in a fixed order through one store tunnel (LIFE-76 PR 4b, D207, D208).

    python -m lifeos.jobs.tick [--live] [--quick]

Order: the resolvers -> US Remote boards -> publish READY jobs (gated) -> enrich -> fit -> publish the fresh ones (gated) -> audit -> hold expiry -> finalize; then bulk discovery while time lasts (employer boards D202, the Scale-Up lane D206,
title watch, discovery search, the sponsor register). Input: the prep job has already swept and extracted the mail.
Each stage has a wall-clock budget cut to the run's remaining time (a fast-path stage never below FAST_FLOOR_SECONDS); a stage over it is counted `timeout`, a bulk stage that cannot start in time is counted `skipped`, a failing stage never stops the next.
Output: the stages' own counts-only `<stage>: {...}` lines, then one `tick:` line; exit 1 when any stage failed or a required (not bulk) stage timed out."""
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

from lifeos.platform import source_registry as registry, stage_timing

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
    early: bool = False               # LIFE-793: a grouped step that starts with the run, in its own thread, while the fast path goes on; the run waits for it where its group ends, so nothing that followed it starts sooner


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
QUICK_LIMITS = {"boards": 50, "publish": 10, "publish-fresh": 5, "boards-backfill": 50, "boards-tick": 50, "drain-publish": 10, "drain-enrich": 50}   # a manual quick proof run (D206, D208) reads few boards and publishes few pages


class StageTimeout(BaseException):
    """Raised by the alarm. A BaseException, so a stage's own `except Exception` cannot swallow it."""


def _alarm(_signum, _frame):
    raise StageTimeout()


def _run_step(step, live, limit, stages):
    stage_timing.begin(step.name)                                                   # LIFE-855: waits in read_slots from this thread count toward this stage
    if step.module:
        argv = (["--live"] if live else []) + ["--limit", str(limit)]
        code = importlib.import_module(step.module).main(argv)
        return "ok" if code == 0 else "failed"
    from lifeos import run                                                          # noqa: PLC0415 - the one stage registry
    code = run.main([step.name, "--limit", str(limit)] + (["--live"] if live else [])) if stages is None else stages[step.name](limit, live)
    if stages is not None:                                                          # LIFE-974: through lifeos.run.main the stage's counts are already noted and `code` is the exit code (0): noting it again wiped them, so `items` was null on every stage
        stage_timing.note_counts(code)
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
    return {"caps": {"linkedin": _small_int(environ.get("TICK_CAP_LINKEDIN"), CAP_DEFAULTS["linkedin"]), "tinyfish": _small_int(environ.get("TICK_CAP_TINYFISH"), CAP_DEFAULTS["tinyfish"]),
                     "jobright": _small_int(environ.get("TICK_CAP_JOBRIGHT"), CAP_DEFAULTS["jobright"]), "dice": _small_int(environ.get("TICK_CAP_DICE"), CAP_DEFAULTS["dice"]),
                     "ats": _small_int(environ.get("TICK_CAP_ATS"), limits.ATS_WORKERS)},
            "parallel": _small_int(environ.get("TICK_MAX_PARALLEL"), MAX_PARALLEL_DEFAULT)}


def tick_concurrency(environ=None):
    """LIFE-856: the caps, read AFTER the Runtime Configuration rows are applied. The workflow declares TICK_CAP_* empty so the settings source can fill them (LIFE-703), but the tick process read them before
    any stage had applied the settings, so a TICK_CAP_TINYFISH row never reached the caps (Nov 25 2026: discover-jobs still waited a p50 65 s behind web-titlewatch). Applying the source here never fails the tick."""
    environ = os.environ if environ is None else environ
    try:
        from lifeos.platform import settings                                           # noqa: PLC0415
        settings.apply(environ)
    except Exception:                                                                   # noqa: BLE001 - an unreadable settings source leaves the caps at their defaults, never fails the run
        pass
    return concurrency_from_env(environ)


EARLY_START_DELAY = 1.0               # LIFE-856: seconds an early step waits before it asks for its services' slots, so the group that starts with the run asks first (the slots hand out in the order asked)


def _after_a_moment(target, *args):
    """LIFE-856: start an early step a moment after the run's first group has asked for its slots. Without this, an early step that shares a service with group A (discover-jobs and the LinkedIn lanes) would take the slot first and hold group A back for its whole run."""
    time.sleep(EARLY_START_DELAY)
    target(*args)


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


def _report(step, label, seconds, outcome, timings, lock=None, queued=0.0):
    """LIFE-604: one stage's result in one place: the outcome, the seconds and the `stage <name>: <outcome> <seconds>s` line (LIFE-569). Every stage prints exactly one, skipped ones included.
    LIFE-855: and one `stage_timing` JSON line beside it (stderr and STAGE_TIMING_FILE; the line above is unchanged). `queued` is the time the stage waited for its services' slots before its budget started."""
    with lock or threading.Lock():
        outcome[step.name] = label
        timings[step.name] = seconds
        print(f"stage {step.name}: {label} {seconds:.1f}s", flush=True)
    waited, counts = stage_timing.take(step.name)
    stage_timing.emit(stage_timing.record(step.name, label, seconds, queued + waited, counts, stage_timing.items_key_for(step.name)))


_SLOT_LOCK = threading.Lock()


def _service_slots(concurrency):
    """LIFE-856: the per-service slots of this tick, made once. Each group used to make its own, which was right while the groups ran one after another; an early step runs beside another group, so a cap (LinkedIn 1, TinyFish 2) did not hold between them and discover-jobs ran with the LinkedIn lanes."""
    with _SLOT_LOCK:
        if "slots" not in concurrency:
            concurrency["slots"] = {name: threading.BoundedSemaphore(n) for name, n in concurrency["caps"].items() if name != "ats"}
        return concurrency["slots"]


def _run_group(steps, live, quick, stages, clock, remaining, concurrency, outcome, timings):
    """LIFE-569: run consecutive independent steps side by side as threads of this process (one store tunnel: lifeos/platform/db.py opens it once per process). Each keeps its own budget, its own
    outcome and its own `stage ...` line; a stage that fails or times out is that stage's red entry and the others finish and save. Services are capped (concurrency["caps"]); a stage waits for its
    services' slots before its budget starts. Fills `outcome` and `timings` exactly like the sequential loop. `remaining()` is the run's time left, asked once per stage as it is started.
    Timeouts here are a timer thread that raises StageTimeout inside the stage's thread (the alarm signal only reaches the main thread); a read blocked in the operating system ignores that until it
    returns, so the run stops waiting for such a stage after its budget plus ABANDON_GRACE_SECONDS and names it (`stage abandoned: <name>`)."""
    caps = concurrency["caps"]
    slots = _service_slots(concurrency)                                                  # LIFE-856: one set per tick, shared by every group and early step, so a service cap holds across them ("ats" is capped inside the reader, per read)
    parallel = threading.BoundedSemaphore(concurrency["parallel"])
    shared = threading.RLock()                                                          # re-entrant: the check for "abandoned" and the report are one step
    abandoned = set()
    threads = []

    def work(step, allowed, limit):
        held = []
        flag = {"done": False, "lock": threading.Lock()}
        began = clock()
        queued = 0.0
        label = None
        try:
            with parallel:
                for name in sorted(p for p in step.providers if p in slots):                  # a fixed order, so two stages that need two services each can never wait on each other
                    slots[name].acquire()
                    held.append(name)
                queued = clock() - began                                               # LIFE-855: the wait for the services' slots, before the budget starts
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
                    _report(step, label or "failed", clock() - began, outcome, timings, shared, queued)

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
    early = {}                                                                         # LIFE-793: name -> the thread running an `early` step (only with concurrency on, and only a grouped step)
    try:
        if concurrency:
            for step in plan:
                if step.early and step.group:
                    thread = threading.Thread(target=_after_a_moment, args=(_run_group, [step], live, quick, stages, clock, lambda: budget - (clock() - started), concurrency, outcome, timings), name=f"tick-early-{step.name}", daemon=True)
                    thread.start()
                    early[step.name] = thread
        position = 0
        while position < len(plan):
            step = plan[position]
            if concurrency and step.group:
                run_of = []
                for candidate in plan[position:]:
                    if candidate.group != step.group:
                        break
                    run_of.append(candidate)
                if len(run_of) > 1 or any(c.name in early for c in run_of):
                    members = [c for c in run_of if c.name not in early]
                    if members:
                        _run_group(members, live, quick, stages, clock, lambda: budget - (clock() - started), concurrency, outcome, timings)
                    for c in run_of:                                                   # the group is over only when its early step is: what follows it still starts after it, exactly as before
                        if c.name in early:
                            early[c.name].join()
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


SHADOW_WAIT_SECONDS = 20              # LIFE-858: how long the end of the run waits for the compare-only read of the Runtime Configuration source


def shadow_in_background(environ=None):
    """LIFE-858 / LIFE-859: the tick does not go through `lifeos.run`, so it never reached the source health check that `lifeos.run` makes. This starts the same read beside the stages (a thread, so the
    critical path pays nothing) and returns a `collect()` that waits at most SHADOW_WAIT_SECONDS and gives the receipt (counts, names and codes only; never a value). It changes no environment
    and writes nothing; any failure is a receipt, never a failed tick."""
    environ = os.environ if environ is None else environ
    box = {}

    def work():
        try:
            from lifeos.platform import runtime_config                          # noqa: PLC0415
            box["receipt"] = runtime_config.shadow(environ, once=True)
        except Exception as error:                                              # noqa: BLE001 - a compare-only read never fails the tick
            box["receipt"] = {"status": "unreadable:" + type(error).__name__}
    thread = threading.Thread(target=work, name="runtime-config-shadow", daemon=True)
    thread.start()

    def collect(wait=SHADOW_WAIT_SECONDS):
        thread.join(wait)
        return box.get("receipt") or {"status": "unreadable:timeout"}
    return collect


def planned(environ=None):
    """LIFE-653: PLAN without the stages TICK_SKIP_STAGES names (the workflow names resolve-jobright when the browser install failed, so that one lane is red and the rest runs)."""
    skip = (os.environ if environ is None else environ).get("TICK_SKIP_STAGES", "").split()
    return tuple(step for step in PLAN if step.name not in skip)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--quick", action="store_true", help="small caps for a manual end-to-end proof (D206)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)                                     # each stage's counts line reaches the log when it is printed, not at exit
    concurrency = tick_concurrency()
    timings, begun = {}, time.monotonic()
    if concurrency:                                                                    # LIFE-569: the global limit on board reads is set before any stage runs
        from lifeos.platform import read_slots                                         # noqa: PLC0415
        read_slots.configure("ats", concurrency["caps"]["ats"])
        print("tick: concurrency on, caps", json.dumps(concurrency["caps"], sort_keys=True), "parallel", concurrency["parallel"], flush=True)
    collect_shadow = shadow_in_background()                                        # LIFE-858: compare-only read of the Runtime Configuration source; off unless the step holds the bootstrap ID
    from lifeos.platform import due                                                # noqa: PLC0415
    full = planned()
    plan, left_out = due.split_plan(full)
    outcome = tick(args.live, args.quick, plan=plan, timings=timings, concurrency=concurrency)
    for step, why in left_out:                                                       # LIFE-617: a stage not due is skipped, not red, and the table says so
        print(f"stage {step.name}: not due 0.0s ({why})", flush=True)
        outcome[step.name], timings[step.name] = "not due", 0.0
    if left_out:
        outcome = {step.name: outcome[step.name] for step in full if step.name in outcome}
    print("tick:", json.dumps(outcome, sort_keys=True))
    shadowed = collect_shadow()
    if shadowed.get("status") != "off":
        print("runtime_config:", json.dumps(shadowed, sort_keys=True), flush=True)     # LIFE-858: counts, names and codes only
    if os.environ.get("GITHUB_RUN_ID"):                                              # LIFE-904: the correlation ID; the full-platform clock is printed by the domains run's `chain` job after its last job ends, never here
        print("chain:", json.dumps({"tick": os.environ["GITHUB_RUN_ID"], "platform": "pending: printed by the domains run chain job"}, sort_keys=True), flush=True)
    wall = time.monotonic() - begun
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:                                                                         # LIFE-569: a short table in the finish job summary
        try:
            with open(path, "a") as handle:
                handle.write(summary_table(outcome, timings, wall))
        except OSError:
            pass
    return 1 if failing_stages(outcome, full) else 0


def failing_stages(outcome, plan):
    """LIFE-691: the stages that fail the tick: any stage that failed, and a REQUIRED stage that ran out of its budget. A bulk (optional) stage that timed out is counted `timeout` and printed, and the tick passes."""
    optional = {step.name for step in plan if step.bulk}
    return [name for name, result in outcome.items() if result == "failed" or (result == "timeout" and name not in optional)]


if __name__ == "__main__":
    sys.exit(main())
