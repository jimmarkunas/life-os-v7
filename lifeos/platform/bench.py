"""LIFE-577: benchmark mode's frozen input. When the workflow sets BENCH_SNAPSHOT (a benchmark dispatch only), the board stages read exactly the boards named in
lifeos/bench/snapshot.json instead of whichever boards are due, with no stored state (no 304 skips), so two runs do the same work. A benchmark run is always a dry run
(LIVE false): nothing is written. Identifiers only; no personal data. Unset (every scheduled tick, every other run): nothing here applies."""
import json
import os


class FrozenInputMissing(RuntimeError):
    """A frozen board id is not in the registry the stage reads: the benchmark would silently read fewer boards, so the run stops instead."""


def frozen(key, environ=os.environ):
    """-> the set of source ids frozen for `key` (a lane name, plus "/boards" for the employer-board pass), or None when benchmark mode is off."""
    path = (environ.get("BENCH_SNAPSHOT") or "").strip()
    if not path:
        return None
    with open(path) as handle:
        lanes = json.load(handle)["lanes"]
    return set(lanes.get(key, ()))


def lane_key(lane, select):
    return lane + ("/boards" if select == "known" else "")


def tick_settings(concurrency, environ=os.environ):
    """LIFE-577 (moved here by LIFE-604) -> None outside benchmark mode, else the one-line settings in force: concurrency switch, caps, parallel limit, the frozen snapshot and its sizes.
    `concurrency` is what lifeos.jobs.tick.concurrency_from_env returned (None = off)."""
    path = (environ.get("BENCH_SNAPSHOT") or "").strip()
    if not path:
        return None
    with open(path) as handle:
        lanes = json.load(handle)["lanes"]
    return {"TICK_CONCURRENCY": "on" if concurrency else "off", "caps": concurrency["caps"] if concurrency else "n/a (sequential)",
            "parallel": concurrency["parallel"] if concurrency else 1, "snapshot": path, "frozen": {key: len(ids) for key, ids in sorted(lanes.items())}, "mode": "dry (nothing is written)"}


def tick_totals(outcome, timings, total):
    return f"benchmark: sum of stage seconds {sum(timings.get(n, 0.0) for n in outcome):.1f}, Jobs tick wall seconds {total:.1f}"


def tick_summary(settings, outcome, timings, total):
    """LIFE-577: the one-line table for the run summary page: settings in force, the sum of stage seconds and the Jobs tick wall time. The per-stage table follows it."""
    spent = sum(timings.get(name, 0.0) for name in outcome)
    failed = [name for name, result in outcome.items() if result in ("failed", "timeout")]
    return ("### Benchmark\n\n| TICK_CONCURRENCY | Frozen boards | Sum of stage seconds | Jobs tick wall seconds | Stages failed |\n|---|---:|---:|---:|---|\n"
            f"| {settings['TICK_CONCURRENCY']} | {settings['frozen'].get('US Remote/boards', 0)} | {spent:.1f} | {total:.1f} | {', '.join(failed) or 'none'} |\n\n"
            f"Settings in force: `{json.dumps(settings, sort_keys=True)}`\n\n")
