"""A per-caller call limit for the governed MCP: at most LIMIT calls per WINDOW seconds per verified `sub`, in memory.

A caller over the limit is refused with HTTP 429 before any tool or gate runs; only a count of recent call times per
key is kept, and keys are the server's own verified subjects.
"""
import threading
import time
from collections import defaultdict, deque

LIMIT, WINDOW = 60, 60.0
_CALLS, _LOCK = defaultdict(deque), threading.Lock()


def allow(key, now=None):
    """True when this call is within the limit (and counts it); False when the caller already made LIMIT calls in the
    last WINDOW seconds.
    """
    now = time.monotonic() if now is None else now
    with _LOCK:
        recent = _CALLS[key]
        while recent and now - recent[0] >= WINDOW:
            recent.popleft()
        if len(recent) >= LIMIT:
            return False
        recent.append(now)
        return True


def reset():
    with _LOCK:
        _CALLS.clear()
