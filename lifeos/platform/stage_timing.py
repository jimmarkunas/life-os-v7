"""One `stage_timing` JSON line per stage: begin and end (CT), elapsed and waiting seconds, item count, outcome.

Written to stderr, and appended to STAGE_TIMING_FILE when set. Waiting seconds = wait for the services' slots before
the stage's budget plus its own thread's wait in read_slots. An unknown item count is null, never zero.
"""
import datetime as dt
import json
import os
import sys
import threading
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
_local = threading.local()
_lock = threading.Lock()
_waits = {}
_unattributed = {"seconds": 0.0}


def begin(stage):
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
    return getattr(_local, "stage", None) is not None


def note_counts(counts):
    _local.counts = counts if isinstance(counts, dict) else None


def take(stage):
    """-> (seconds this stage's thread waited in read_slots, its returned counts or None); forgets both."""
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
    """-> complete | degraded | incomplete: unfinished is incomplete; failed items or a degraded flag is degraded."""
    if label != "ok":
        return "incomplete"
    if isinstance(counts, dict):
        failed = counts.get("failed")
        if counts.get("degraded") is True or (isinstance(failed, int) and not isinstance(failed, bool) and failed > 0):
            return "degraded"
    return "complete"


_KEYS = {}


def items_key_for(stage):
    if not _KEYS:
        try:
            table = json.load(open(os.path.join(os.path.dirname(__file__), "stages.json")))["stages"]
            _KEYS.update({name: row["items_key"] for name, row in table.items() if isinstance(row, dict) and isinstance(row.get("items_key"), str)})
        except (OSError, ValueError, KeyError):
            _KEYS["__none__"] = None
    return _KEYS.get(stage)


def items_of(counts, key):
    value = counts.get(key) if isinstance(counts, dict) and key else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def record(stage, label, seconds, wait_seconds=0.0, counts=None, items_key=None, ended=None, environ=os.environ):
    ended = ended or dt.datetime.now(dt.timezone.utc)
    return {"stage": stage, "outcome": outcome_of(label, counts), "result": label, "begin": stamp(ended - dt.timedelta(seconds=seconds)), "end": stamp(ended),
            "elapsed_s": round(seconds, 1), "wait_s": round(wait_seconds, 1), "items": items_of(counts, items_key), "run": str(environ.get("GITHUB_RUN_ID") or "") or None}


def emit(row, environ=os.environ, err=None):
    text = json.dumps(row, sort_keys=True)
    try:
        print(f"stage_timing: {text}", file=err or sys.stderr, flush=True)
        path = environ.get("STAGE_TIMING_FILE")
        if path:
            with _lock, open(path, "a") as handle:
                handle.write(text + "\n")
    except Exception:  # noqa: BLE001
        return False
    return True
