"""Bounded execution primitives (ported, trimmed, from the V2 runtime kernel). Not a workflow engine: a deadline and a run id.

Consumers: the resolve stage (`lifeos.jobs.resolve.stage`) for its batch deadline; Interview OS stages are the second (docs/INTERVIEW_HANDOFF.md).
No registry, no hooks, no pipeline graph. One run at a time stays a workflow concern (the concurrency group), not code."""
from __future__ import annotations

from collections.abc import Callable
import time
from uuid import uuid4

MAX_RUNTIME_SECONDS = 3600.0          # one Actions job; a stage asks for less


class DeadlineExceeded(TimeoutError):
    """Raised when a RunContext has no usable time budget left."""


class RunContext:
    def __init__(self, timeout_seconds: float, run_id: str | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        timeout = float(timeout_seconds)
        if timeout <= 0 or timeout > MAX_RUNTIME_SECONDS:
            raise ValueError("timeout_seconds must be positive and within the platform maximum")
        self.run_id = run_id or uuid4().hex
        self.timeout_seconds = timeout
        self._clock = clock
        self._started = clock()

    @classmethod
    def start(cls, timeout_seconds: float, run_id: str | None = None, clock: Callable[[], float] = time.monotonic) -> RunContext:
        return cls(timeout_seconds, run_id, clock)

    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started)

    def remaining_seconds(self) -> float:
        return max(0.0, self.timeout_seconds - self.elapsed_seconds())

    def expired(self) -> bool:
        return self.remaining_seconds() <= 0.0

    def require_time(self, minimum_seconds: float = 0.0) -> float:
        remaining = self.remaining_seconds()
        if remaining <= max(0.0, float(minimum_seconds)):
            raise DeadlineExceeded("execution deadline exhausted")
        return remaining
