"""LIFE-855: one `stage_timing` JSON line per stage: begin and end (CT), elapsed and waiting seconds, item count, outcome; names and numbers only.

The tick (lifeos/jobs/tick.py) and a standalone stage (lifeos/run.py) call `line()`; the existing `stage <name>: <outcome> <seconds>s` line is not touched. The line goes to stderr and, when STAGE_TIMING_FILE is set,
is appended to that file (the hourly workflow uploads it as an artifact). Waiting seconds = the time a stage waited for its services' slots before its own budget started plus the time its own thread waited in
read_slots; a wait in a worker thread the stage started is counted under `unattributed_wait_s` for the whole process, never guessed into a stage. An unknown item count is null, never zero. Stdlib only."""
import datetime as dt
import json
import os
import sys
import threading
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
_local = threading.local()
_lock = threading.Lock()
_waits = {}                                         # stage -> seconds its own thread waited in read_slots
_unattributed = {"seconds": 0.0}


def begin(stage):
    """Mark the calling thread as running `stage`, so waits in read_slots count toward it."""
    _local.stage = stage
    _local.counts = None
    with _lock:
        _waits.pop(stage, None)


def add_wait(seconds):
    stage = getattr(_local, "stage", None)
    with _lock:
        if stage is None:
            _unattributed["seconds"] += seconds
        else:
            _waits[stage] = _waits.get(stage, 0.0) + seconds


def in_tick():
    """True on a thread the tick started for a stage: the tick prints that stage's line, so the stage itself must not print a second one."""
    return getattr(_local, "stage", None) is not None


def note_counts(counts):
    """The counts a stage just returned, kept for the calling thread's own report line."""
    _local.counts = counts if isinstance(counts, dict) else None


def take(stage):
    """-> (seconds the stage's own thread waited in read_slots, the counts it returned or None); forgets both."""
    with _lock:
        waited = _waits.pop(stage, 0.0)
    counts, _local.counts = getattr(_local, "counts", None), None
    return waited, counts


def unattributed():
    with _lock:
        return round(_unattributed["seconds"], 1)


def stamp(moment):
    return moment.astimezone(CENTRAL).isoformat(timespec="seconds")


def outcome_of(label, counts):
    """-> complete | degraded | incomplete. A stage that did not finish (failed, timed out, skipped) is incomplete; one that finished but reports failed items or says degraded is degraded."""
    if label != "ok":
        return "incomplete"
    if isinstance(counts, dict):
        failed = counts.get("failed")
        if counts.get("degraded") is True or (isinstance(failed, int) and not isinstance(failed, bool) and failed > 0):
            return "degraded"
    return "complete"


def items_of(counts, key):
    """-> the declared item count, or None (unknown, never zero): only a whole number under the stage's own declared key counts."""
    value = counts.get(key) if isinstance(counts, dict) and key else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def record(stage, label, seconds, wait_seconds=0.0, counts=None, items_key=None, ended=None, environ=os.environ):
    ended = ended or dt.datetime.now(dt.timezone.utc)
    return {"stage": stage, "outcome": outcome_of(label, counts), "result": label, "begin": stamp(ended - dt.timedelta(seconds=seconds)), "end": stamp(ended),
            "elapsed_s": round(seconds, 1), "wait_s": round(wait_seconds, 1), "items": items_of(counts, items_key), "run": str(environ.get("GITHUB_RUN_ID") or "") or None}


def emit(row, environ=os.environ, err=None):
    """Print `stage_timing: {json}` to stderr and append the JSON to STAGE_TIMING_FILE when set. Never raises: a timing line must not fail a stage."""
    text = json.dumps(row, sort_keys=True)
    try:
        print(f"stage_timing: {text}", file=err or sys.stderr, flush=True)
        path = environ.get("STAGE_TIMING_FILE")
        if path:
            with _lock, open(path, "a") as handle:
                handle.write(text + "\n")
    except Exception:                                                           # noqa: BLE001 - best effort only
        return False
    return True
