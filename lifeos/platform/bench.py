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
