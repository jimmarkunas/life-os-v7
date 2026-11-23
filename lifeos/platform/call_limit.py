"""LIFE-874: a per-caller call limit for the governed MCP: at most LIMIT calls per WINDOW seconds for one verified user or credential (its `sub`), in memory, per process.

A caller over the limit is refused with HTTP 429 before any tool or gate runs; nothing is stored and no value is kept, only a count of recent call times per key. Keys are the server's own verified subjects, never a caller-supplied value."""
import threading
import time
from collections import defaultdict, deque

LIMIT, WINDOW = 60, 60.0
_CALLS, _LOCK = defaultdict(deque), threading.Lock()


def allow(key, now=None):
    """True when this call is within the limit (and counts it); False when the caller already made LIMIT calls in the last WINDOW seconds."""
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
