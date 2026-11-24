"""LIFE-569: process-wide limits on how many reads of one kind are in flight at once, shared by every stage of a Jobs tick that runs side by side.

Unconfigured (the default) a slot is a no-op, so nothing changes unless the tick configures it (the side-by-side runner always does). Stdlib only; no job knowledge."""
import threading
import time
from contextlib import contextmanager

_slots = {}


def configure(name, count):
    """Allow at most `count` reads named `name` at once (None or 0 removes the limit)."""
    _slots[name] = threading.BoundedSemaphore(count) if count else None


@contextmanager
def slot(name):
    semaphore = _slots.get(name)
    if semaphore is None:
        yield
        return
    began = time.monotonic()
    semaphore.acquire()
    from lifeos.platform import stage_timing                                          # noqa: PLC0415 - LIFE-855: the wait counts toward the stage whose thread waited
    stage_timing.add_wait(time.monotonic() - began)
    try:
        yield
    finally:
        semaphore.release()
