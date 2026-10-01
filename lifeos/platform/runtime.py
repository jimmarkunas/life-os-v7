"""Bounded execution primitives (ported, trimmed, from the V2 runtime kernel). Not a workflow engine: a deadline, a run id and a terminal result shape.

Consumers: the resolve stage (`lifeos.jobs.resolve.stage`) for its batch deadline; Interview OS stages are the second (docs/INTERVIEW_HANDOFF.md).
No registry, no hooks, no pipeline graph. One run at a time stays a workflow concern (the concurrency group), not code."""
from dataclasses import dataclass, field
from enum import Enum
import time
from uuid import uuid4

MAX_RUNTIME_SECONDS = 3600.0          # one Actions job; a stage asks for less


class DeadlineExceeded(TimeoutError):
    """Raised when a RunContext has no usable time budget left."""


class ExecutionStatus(str, Enum):
    PASS = "PASS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


_SIMPLE = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class ExecutionResult:
    """Terminal result of a stage. `detail` holds only scalars (counts, fixed codes): anything else is reduced to its type name."""
    status: ExecutionStatus
    code: str = "ok"
    detail: dict = field(default_factory=dict)

    @classmethod
    def of(cls, status, code="ok", **detail):
        return cls(status, code, {k: v if isinstance(v, _SIMPLE) else type(v).__name__ for k, v in detail.items()})

    @classmethod
    def passed(cls, **detail):
        return cls.of(ExecutionStatus.PASS, "ok", **detail)

    @classmethod
    def degraded(cls, code, **detail):
        return cls.of(ExecutionStatus.DEGRADED, code, **detail)

    @classmethod
    def failed(cls, code, **detail):
        return cls.of(ExecutionStatus.FAILED, code, **detail)


class RunContext:
    def __init__(self, timeout_seconds, run_id=None, clock=time.monotonic):
        timeout = float(timeout_seconds)
        if timeout <= 0 or timeout > MAX_RUNTIME_SECONDS:
            raise ValueError("timeout_seconds must be positive and within the platform maximum")
        self.run_id = run_id or uuid4().hex
        self.timeout_seconds = timeout
        self._clock = clock
        self._started = clock()

    @classmethod
    def start(cls, timeout_seconds, run_id=None, clock=time.monotonic):
        return cls(timeout_seconds, run_id, clock)

    def elapsed_seconds(self):
        return max(0.0, self._clock() - self._started)

    def remaining_seconds(self):
        return max(0.0, self.timeout_seconds - self.elapsed_seconds())

    def expired(self):
        return self.remaining_seconds() <= 0.0

    def require_time(self, minimum_seconds=0.0):
        remaining = self.remaining_seconds()
        if remaining <= max(0.0, float(minimum_seconds)):
            raise DeadlineExceeded("execution deadline exhausted")
        return remaining

    def bounded_timeout(self, requested_seconds):
        """A per-call timeout that never outlives the run: min(requested, remaining). Raises when nothing is left."""
        return min(float(requested_seconds), self.require_time())
