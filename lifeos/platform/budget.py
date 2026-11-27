"""A time budget for every stage: one that spends it stops, records one fixed code, and fails the run only if required.

`lifeos/platform/stage_budgets.json` holds one row per stage (seconds, required). `guard(seconds)` is an alarm around
one stage; its exception is a BaseException, so a stage's own `except Exception` cannot swallow it and `finally`
blocks still run. Where a run already has an alarm (the Jobs tick gives each stage its own), the guard stays out of
the way. Counts carry the fixed code and the two numbers only.
"""

import json
import os
import signal
import threading
from contextlib import contextmanager
from pathlib import Path

CODE = "STAGE_BUDGET_EXCEEDED"
TABLE = Path(__file__).with_name("stage_budgets.json")


class StageBudgetExceeded(BaseException):
    """Raised by the alarm when a stage has spent its budget."""


def load(path=TABLE):
    return json.loads(Path(path).read_text())


def budget_for(stage, table=None):
    """-> (seconds, required) for a stage; a stage with no row gets the table's default and is optional."""
    table = load() if table is None else table
    row = (table.get("stages") or {}).get(stage)
    if row:
        return int(row["seconds"]), bool(row["required"])
    return int(table["default_seconds"]), False


def _alarm(_signum, _frame):
    raise StageBudgetExceeded()


@contextmanager
def guard(seconds, environ=None):
    """Run the block under an alarm of `seconds`. No alarm when STAGE_BUDGETS is off, off the main thread, or where
    another alarm is armed.
    """
    environ = os.environ if environ is None else environ
    if (
        environ.get("STAGE_BUDGETS") == "off"
        or not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
        or signal.getsignal(signal.SIGALRM) not in (signal.SIG_DFL, signal.SIG_IGN, None)
        or signal.getitimer(signal.ITIMER_REAL)[0] > 0
    ):
        yield
        return
    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, max(0.01, float(seconds)))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def over_budget(seconds, elapsed, required):
    """-> (counts, exit code) for a stage the alarm stopped: required stages fail the run with the fixed code, optional
    ones record it and pass.
    """
    counts = {
        "stage_budget_exceeded": 1,
        "code": CODE,
        "budget_seconds": int(seconds),
        "elapsed_seconds": int(elapsed),
        "required": bool(required),
    }
    return counts, (1 if required else 0)
