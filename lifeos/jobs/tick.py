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

from lifeos.platform import api_counts, source_registry as registry, stage_timing

RUN_BUDGET_SECONDS = 35 * 60  # the job allows 45 minutes; the rest is setup
FAST_FLOOR_SECONDS = 60  # a fast-path stage always gets at least this long


@dataclass(frozen=True)
class Step:
    name: str
    budget: int
    limit: int = 40
    bulk: bool = False  # runs after the fast path, while time lasts
    module: str = ""  # a module whose main(argv) is the stage
    group: str = ""  # consecutive steps with the same group run as threads
    providers: tuple = ()  # outside services it calls; each has a TICK_CAP_* cap
    # runs in its own thread; the run waits for it where its group ends
    early: bool = False


PLAN = (
    Step("resolve-jobright", 600, 40, group="A", providers=("jobright",)),
    Step("resolve-linkedin", 420, 150, group="A", providers=("linkedin",)),
    Step("resolve-dice", 180, 15, group="A", providers=("dice",)),
    Step("audit-ready", 60),
    Step("publish", 720, 40),  # ~13 s a page
    Step("enrich", 480, 150),
    Step("fit", 240),
    Step("audit-fresh", 60),
    Step("publish-fresh", 300, 20),
    Step("audit", 180),
    Step("expire-holds", 120),
    Step("finalize", 180, 300, module="lifeos.sources.newsletters.finalize"),
    Step("drain-audit-ready", 60, bulk=True),
    Step("drain-publish", 720, 120, bulk=True),
    Step("drain-enrich", 480, 600, bulk=True),  # no TinyFish fallback
    Step("drain-fit", 240, bulk=True),
)
# registered sources' steps, each after the step it names
PLAN = registry.with_tick_steps(PLAN, Step)
# --quick: a small proof run
QUICK_LIMITS = {
    "boards": 50,
    "publish": 10,
    "publish-fresh": 5,
    "boards-backfill": 50,
    "boards-tick": 50,
    "drain-publish": 10,
    "drain-enrich": 50,
}


class StageTimeout(BaseException):
    """Raised by the alarm. A BaseException so a stage's `except Exception` cannot swallow it."""


def _alarm(_signum, _frame):
    raise StageTimeout()


def _run_step(step, live, limit, stages):
    stage_timing.begin(step.name)
    if step.module:
        argv = (["--live"] if live else []) + ["--limit", str(limit)]
        code = importlib.import_module(step.module).main(argv)
        return "ok" if code == 0 else "failed"
    from lifeos import run  # noqa: PLC0415 - the one stage registry

    code = (
        run.main([step.name, "--limit", str(limit)] + (["--live"] if live else []))
        if stages is None
        else stages[step.name](limit, live)
    )
    if stages is not None:
        stage_timing.note_counts(code)
    return "ok" if code in (0, None) or isinstance(code, dict) else "failed"


CAP_DEFAULTS = {"linkedin": 1, "tinyfish": 1, "jobright": 1, "dice": 1}
MAX_PARALLEL_DEFAULT = 4


def _small_int(raw, default, low=1, high=16):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def concurrency_from_env(environ=None):
    """-> {"caps": {provider: n}, "parallel": n}; a cap that is not a whole number from 1 to 16 uses its default."""
    environ = os.environ if environ is None else environ
    from lifeos.platform import limits  # noqa: PLC0415

    return {
        "caps": {
            "linkedin": _small_int(environ.get("TICK_CAP_LINKEDIN"), CAP_DEFAULTS["linkedin"]),
            "tinyfish": _small_int(environ.get("TICK_CAP_TINYFISH"), CAP_DEFAULTS["tinyfish"]),
            "jobright": _small_int(environ.get("TICK_CAP_JOBRIGHT"), CAP_DEFAULTS["jobright"]),
            "dice": _small_int(environ.get("TICK_CAP_DICE"), CAP_DEFAULTS["dice"]),
            "ats": _small_int(environ.get("TICK_CAP_ATS"), limits.ATS_WORKERS),
        },
        "parallel": _small_int(environ.get("TICK_MAX_PARALLEL"), MAX_PARALLEL_DEFAULT),
    }


def tick_concurrency(environ=None):
    environ = os.environ if environ is None else environ
    try:
        from lifeos.platform import settings  # noqa: PLC0415

        settings.apply(environ)
    except Exception:  # noqa: BLE001 - unreadable settings leave the default caps
        pass
    return concurrency_from_env(environ)


# lets the first group ask for service slots first
EARLY_START_DELAY = 1.0


def _after_a_moment(target, *args):
    time.sleep(EARLY_START_DELAY)
    target(*args)


def _stop_thread(thread, flag):
    """Raise StageTimeout inside `thread` (SIGALRM only reaches the main thread), when it next runs Python code."""
    with flag["lock"]:
        if not flag["done"] and thread.ident is not None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread.ident), ctypes.py_object(StageTimeout))


# grace after a stage's budget before the run stops waiting
ABANDON_GRACE_SECONDS = 5


def _allot(step, left, quick):
    """-> (seconds the stage may run, its --limit); None when a bulk stage has under a minute left (skipped)."""
    if step.bulk and left < 60:
        return None
    allowed = max(1, int(min(step.budget, left))) if step.bulk else min(step.budget, max(FAST_FLOOR_SECONDS, int(left)))
    return allowed, (QUICK_LIMITS.get(step.name, step.limit) if quick else step.limit)


def _report(step, label, seconds, outcome, timings, lock=None, queued=0.0):
    """Print one `stage <name>: <outcome> <seconds>s` line plus a `stage_timing` JSON line; `queued` = slot wait."""
    with lock or threading.Lock():
        outcome[step.name] = label
        timings[step.name] = seconds
        print(f"stage {step.name}: {label} {seconds:.1f}s", flush=True)
    waited, counts = stage_timing.take(step.name)
    stage_timing.emit(
        stage_timing.record(
            step.name,
            label,
            seconds,
            queued + waited,
            counts,
            stage_timing.items_key_for(step.name),
            api_calls=api_counts.take(),
        )
    )


_SLOT_LOCK = threading.Lock()


def _service_slots(concurrency):
    with _SLOT_LOCK:
        if "slots" not in concurrency:
            concurrency["slots"] = {
                name: threading.BoundedSemaphore(n) for name, n in concurrency["caps"].items() if name != "ats"
            }
        return concurrency["slots"]


def _run_group(steps, live, quick, stages, clock, remaining, concurrency, outcome, timings):
    """Run consecutive independent steps as threads of this process (one store tunnel).

    Each keeps its own budget, outcome and `stage ...` line. A stage waits for its service slots before its budget
    starts. A timer raises StageTimeout in its thread; a read blocked in the OS ignores it, so the run stops waiting
    after ABANDON_GRACE_SECONDS and prints `stage abandoned: <name>`.
    """
    caps = concurrency["caps"]
    slots = _service_slots(concurrency)
    parallel = threading.BoundedSemaphore(concurrency["parallel"])
    shared = threading.RLock()
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
                # fixed order avoids deadlock
                for name in sorted(p for p in step.providers if p in slots):
                    slots[name].acquire()
                    held.append(name)
                queued = clock() - began
                began = clock()
                timer = threading.Timer(allowed, _stop_thread, args=(threading.current_thread(), flag))
                timer.daemon = True
                timer.start()
                try:
                    label = _run_step(step, live, limit, stages)
                except StageTimeout:
                    label = "timeout"
                    print(f"{step.name.upper()} FAILED: STAGE_TIMEOUT_{allowed}S", file=sys.stderr)
                except Exception as error:  # noqa: BLE001 - one stage never stops the next
                    label = "failed"
                    print(f"{step.name.upper()} FAILED: {type(error).__name__}", file=sys.stderr)
                finally:
                    with flag["lock"]:
                        flag["done"] = True
                    timer.cancel()
        except StageTimeout:
            label = label or "timeout"
        finally:
            for name in held:
                slots[name].release()
            with shared:
                if step.name not in abandoned:
                    _report(step, label or "failed", clock() - began, outcome, timings, shared, queued)

    for step in steps:
        plan_for = _allot(step, remaining(), quick)
        if plan_for is None:
            _report(step, "skipped", 0.0, outcome, timings, shared)
            continue
        allowed, limit = plan_for
        thread = threading.Thread(target=work, args=(step, allowed, limit), name=f"tick-{step.name}", daemon=True)
        thread.start()
        threads.append((step, thread, allowed, time.monotonic() + allowed + ABANDON_GRACE_SECONDS))
    # bounds a stage that cannot hear its timer
    for step, thread, allowed, stop_waiting in threads:
        thread.join(max(stop_waiting - time.monotonic(), 0))
        if thread.is_alive():
            with shared:
                if step.name not in outcome:
                    abandoned.add(step.name)
                    _report(step, "timeout", float(allowed), outcome, timings, shared)
                    print(f"stage abandoned: {step.name}", flush=True)


def tick(
    live,
    quick=False,
    plan=PLAN,
    stages=None,
    clock=time.monotonic,
    budget=RUN_BUDGET_SECONDS,
    timings=None,
    concurrency=None,
):
    """-> {stage: ok | failed | timeout | skipped}.

    `stages` (name -> callable(limit, live)) replaces lifeos.run in tests; `timings` collects {stage: seconds};
    `concurrency` (None = off) runs consecutive steps sharing a `group` side by side.
    """
    started, outcome = clock(), {}
    timings = {} if timings is None else timings
    previous = signal.signal(signal.SIGALRM, _alarm)
    early = {}
    try:
        if concurrency:
            for step in plan:
                if step.early and step.group:
                    thread = threading.Thread(
                        target=_after_a_moment,
                        args=(
                            _run_group,
                            [step],
                            live,
                            quick,
                            stages,
                            clock,
                            lambda: budget - (clock() - started),
                            concurrency,
                            outcome,
                            timings,
                        ),
                        name=f"tick-early-{step.name}",
                        daemon=True,
                    )
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
                        _run_group(
                            members,
                            live,
                            quick,
                            stages,
                            clock,
                            lambda: budget - (clock() - started),
                            concurrency,
                            outcome,
                            timings,
                        )
                    for c in run_of:
                        if c.name in early:
                            early[c.name].join()
                    position += len(run_of)
                    continue
            position += 1
            plan_for = _allot(step, budget - (clock() - started), quick)
            if plan_for is None:
                _report(step, "skipped", 0.0, outcome, timings)
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
            except Exception as error:  # noqa: BLE001 - one stage never stops the next
                print(f"{step.name.upper()} FAILED: {type(error).__name__}", file=sys.stderr)
            finally:
                signal.alarm(0)
                _report(step, label, clock() - stage_started, outcome, timings)
    finally:
        signal.signal(signal.SIGALRM, previous)
    return {step.name: outcome[step.name] for step in plan if step.name in outcome}


def summary_table(outcome, timings, total):
    lines = ["### Jobs tick: seconds per stage", "", "| Stage | Outcome | Seconds | Share |", "|---|---|---:|---:|"]
    for name, result in outcome.items():
        seconds = timings.get(name, 0.0)
        lines.append(
            f"| {name} | {result} | {seconds:.1f} | {100 * seconds / total:.0f}% |"
            if total > 0
            else f"| {name} | {result} | {seconds:.1f} | - |"
        )
    lines.append(f"| **total** | | **{total:.1f}** | |")
    return "\n".join(lines) + "\n"


SHADOW_WAIT_SECONDS = 20


def shadow_in_background(environ=None):
    """Start a compare-only read of the Runtime Configuration source; `collect()` waits SHADOW_WAIT_SECONDS."""
    environ = os.environ if environ is None else environ
    box = {}

    def work():
        try:
            from lifeos.platform import runtime_config  # noqa: PLC0415

            box["receipt"] = runtime_config.shadow(environ, once=True)
        except Exception as error:  # noqa: BLE001 - a compare-only read never fails the tick
            box["receipt"] = {"status": "unreadable:" + type(error).__name__}

    thread = threading.Thread(target=work, name="runtime-config-shadow", daemon=True)
    thread.start()

    def collect(wait=SHADOW_WAIT_SECONDS):
        thread.join(wait)
        return box.get("receipt") or {"status": "unreadable:timeout"}

    return collect


def planned(environ=None):
    skip = (os.environ if environ is None else environ).get("TICK_SKIP_STAGES", "").split()
    return tuple(step for step in PLAN if step.name not in skip)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--quick", action="store_true", help="small caps for a manual end-to-end proof (D206)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)
    concurrency = tick_concurrency()
    timings, begun = {}, time.monotonic()
    if concurrency:
        from lifeos.platform import read_slots  # noqa: PLC0415

        read_slots.configure("ats", concurrency["caps"]["ats"])
        print(
            "tick: concurrency on, caps",
            json.dumps(concurrency["caps"], sort_keys=True),
            "parallel",
            concurrency["parallel"],
            flush=True,
        )
    collect_shadow = shadow_in_background()
    from lifeos.platform import due  # noqa: PLC0415

    full = planned()
    plan, left_out = due.split_plan(full)
    outcome = tick(args.live, args.quick, plan=plan, timings=timings, concurrency=concurrency)
    for step, why in left_out:
        print(f"stage {step.name}: not due 0.0s ({why})", flush=True)
        outcome[step.name], timings[step.name] = "not due", 0.0
    if left_out:
        outcome = {step.name: outcome[step.name] for step in full if step.name in outcome}
    print("tick:", json.dumps(outcome, sort_keys=True))
    shadowed = collect_shadow()
    if shadowed.get("status") != "off":
        print("runtime_config:", json.dumps(shadowed, sort_keys=True), flush=True)
    if os.environ.get("GITHUB_RUN_ID"):
        print(
            "chain:",
            json.dumps(
                {"tick": os.environ["GITHUB_RUN_ID"], "platform": "pending: printed by the domains run chain job"},
                sort_keys=True,
            ),
            flush=True,
        )
    wall = time.monotonic() - begun
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a") as handle:
                handle.write(summary_table(outcome, timings, wall))
        except OSError:
            pass
    return 1 if failing_stages(outcome, full) else 0


def failing_stages(outcome, plan):
    """The stages that fail the tick: any that failed, and a required (not bulk) stage that timed out."""
    optional = {step.name for step in plan if step.bulk}
    return [
        name for name, result in outcome.items() if result == "failed" or (result == "timeout" and name not in optional)
    ]


if __name__ == "__main__":
    sys.exit(main())
