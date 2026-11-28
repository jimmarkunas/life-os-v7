"""One Gmail budget for every process: a token bucket, an in-flight cap and a lease kept in the shared store.

Gmail counts quota units per user per minute, and every LIFE OS job reads the same mailbox from its own runner, so a
limit kept inside one process cannot hold. The lease (one row in the Hostinger MySQL store) lets one process at a time
spend; the one holding it spends through its bucket at the rate in `limits.py`, which keeps the whole platform under
the provider limit. The gate never fails a call: when the store is unreachable, unconfigured or the wait is too long,
the call goes ahead exactly as it did before the gate existed, and `gmail_lease_open` counts it.

`spend(method, url)` wraps one HTTP attempt. Stage tallies (units, peak units in any second, peak in flight, seconds
waited) are added to `api_counts` and so reach each stage's timing row."""

from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
import os
import secrets
import threading
import time
import urllib.parse

from lifeos.platform import api_counts, limits

TABLE = "v7_gmail_gate"
CREATE = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} (k TINYINT NOT NULL PRIMARY KEY, holder VARCHAR(24) NOT NULL, "
    "until_at DATETIME(3) NOT NULL) ENGINE=InnoDB"
)
SEED = f"INSERT IGNORE INTO {TABLE} (k, holder, until_at) VALUES (1, '', NOW(3) - INTERVAL 1 SECOND)"
TAKE = (
    f"UPDATE {TABLE} SET holder = %s, until_at = NOW(3) + INTERVAL %s SECOND "
    "WHERE k = 1 AND (holder = %s OR until_at < NOW(3))"
)
WHO = f"SELECT holder FROM {TABLE} WHERE k = 1"
GIVE = f"UPDATE {TABLE} SET until_at = NOW(3) - INTERVAL 1 SECOND WHERE k = 1 AND holder = %s"
HEAVY = ("full", "raw", "html")


def units(method: str, url: str) -> int:
    """-> the quota units Gmail charges for this call (a call it does not know is priced high, never low)."""
    path, _, query = url.partition("/users/me/")[2].partition("?")
    parts = path.split("/")
    heavy = urllib.parse.parse_qs(query).get("format", ["full"])[0] in HEAVY
    last, first = parts[-1], parts[0]
    if last == "send":
        return limits.GMAIL_UNIT_SEND
    if last == "batchModify":
        return limits.GMAIL_UNIT_BATCH_MODIFY
    if first == "profile":
        return limits.GMAIL_UNIT_PROFILE
    if first == "history":
        return limits.GMAIL_UNIT_HISTORY
    if first == "labels":
        return limits.GMAIL_UNIT_LABEL_READ if method == "GET" else limits.GMAIL_UNIT_LABEL_WRITE
    if first == "threads":
        if len(parts) == 1:
            return limits.GMAIL_UNIT_THREAD_LIST
        if last in ("modify", "untrash"):
            return limits.GMAIL_UNIT_THREAD_LIST
        if last == "trash":
            return limits.GMAIL_UNIT_TRASH
        return limits.GMAIL_UNIT_THREAD_FULL if heavy else limits.GMAIL_UNIT_THREAD_LIGHT
    if first == "messages":
        if len(parts) == 1:
            return limits.GMAIL_UNIT_LIST
        if "attachments" in parts:
            return limits.GMAIL_UNIT_ATTACHMENT
        if last == "trash":
            return limits.GMAIL_UNIT_TRASH
        if last in ("modify", "untrash"):
            return limits.GMAIL_UNIT_MODIFY
        if method == "GET":
            return limits.GMAIL_UNIT_MESSAGE_FULL if heavy else limits.GMAIL_UNIT_MESSAGE_LIGHT
    return limits.GMAIL_UNIT_DEFAULT


class Bucket:
    """Tokens refill at `per_minute` / 60 a second up to `burst`; `take` returns how long the caller must wait."""

    def __init__(self, per_minute: float, burst: float, now: float) -> None:
        self.rate, self.burst = per_minute / 60.0, float(burst)
        self.tokens, self.at = float(burst), now

    def take(self, amount: float, now: float) -> float:
        self.tokens = min(self.burst, self.tokens + max(now - self.at, 0.0) * self.rate)
        self.at = now
        self.tokens -= amount
        return max(-self.tokens / self.rate, 0.0)

    def slow(self, factor: float, floor_per_minute: float) -> None:
        self.rate = max(self.rate * factor, floor_per_minute / 60.0)


class Gate:
    def __init__(self, enabled: bool | None = None) -> None:
        # `LIFEOS_GMAIL_GATE=off` is the kill switch (an incident, or unit tests that fake Gmail): no wait, no lease.
        self._enabled = os.environ.get("LIFEOS_GMAIL_GATE") != "off" if enabled is None else enabled
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(limits.GMAIL_MAX_IN_FLIGHT)
        self._bucket = Bucket(limits.GMAIL_UNITS_PER_MINUTE, limits.GMAIL_BURST_UNITS, time.monotonic())
        self._me = secrets.token_hex(8)
        self._window: deque[tuple[float, int]] = deque()
        self._in_flight = 0
        self._renewed = float("-inf")  # monotonic time of the last successful take of the lease
        self._held_since = float("-inf")  # monotonic time this unbroken hold of the lease began
        self._open_until = float("-inf")  # after giving up a wait, go ahead without the lease until then
        self._lease_lock = threading.Lock()  # one thread at a time asks for, waits for or gives up the lease
        self._ready = False  # the table exists
        self._off = False  # the store is unusable for this process: go ahead without a lease
        self._exit_hooked = False

    def _store(self) -> bool:
        return not self._off and bool(os.environ.get("LIFEOS_ACQ_DB_PASSWORD"))

    def _lease(self) -> None:
        with self._lease_lock:
            self._lease_held()

    def _lease_held(self) -> None:
        """Hold the lease, waiting for another process's to lapse; fail open on any trouble.

        A holder that has had it for `GMAIL_LEASE_HOLD_MAX_SECONDS` without a break lets go and rests, so a process
        waiting behind a busy holder is served in turn instead of waiting out the cap. A caller that gave up waiting
        goes ahead without the lease for `GMAIL_LEASE_OPEN_SECONDS`, so its later calls do not each wait the cap again."""
        now = time.monotonic()
        if not self._store() or now < self._open_until:
            return
        holding = now - self._renewed <= limits.GMAIL_LEASE_SECONDS
        if holding and now - self._held_since >= limits.GMAIL_LEASE_HOLD_MAX_SECONDS:
            self.release()
            time.sleep(limits.GMAIL_LEASE_REST_SECONDS)
            api_counts.add("gmail_lease_yield", 1)
        elif now - self._renewed < limits.GMAIL_LEASE_RENEW_SECONDS:
            return
        waited = 0.0
        try:
            from lifeos.platform import db  # noqa: PLC0415

            with db.connect() as connection, connection.cursor() as cursor:
                if not self._ready:
                    cursor.execute(CREATE)
                    cursor.execute(SEED)
                    self._ready = True
                while True:
                    before = self._renewed
                    cursor.execute(TAKE, (self._me, limits.GMAIL_LEASE_SECONDS, self._me))
                    cursor.execute(WHO)
                    row = cursor.fetchone()
                    if row and row[0] == self._me:
                        now = time.monotonic()
                        if now - before > limits.GMAIL_LEASE_SECONDS:
                            self._held_since = now  # a new hold, not a renewal of the last one
                        self._renewed = now
                        if not self._exit_hooked:
                            import atexit  # noqa: PLC0415

                            atexit.register(self.release)
                            self._exit_hooked = True
                        break
                    if waited >= limits.GMAIL_LEASE_WAIT_MAX_SECONDS:
                        api_counts.add("gmail_lease_open", 1)
                        self._open_until = time.monotonic() + limits.GMAIL_LEASE_OPEN_SECONDS
                        break
                    time.sleep(limits.GMAIL_LEASE_POLL_SECONDS)
                    waited += limits.GMAIL_LEASE_POLL_SECONDS
        except Exception:  # noqa: BLE001
            self._off = True
            api_counts.add("gmail_lease_open", 1)
        if waited:
            api_counts.add("gmail_lease_wait_s", round(waited))

    def release(self) -> None:
        """Give the lease back (at exit), so the next process need not wait for it to lapse."""
        if self._renewed == float("-inf") or self._off:
            return
        try:
            from lifeos.platform import db  # noqa: PLC0415

            with db.connect() as connection, connection.cursor() as cursor:
                cursor.execute(GIVE, (self._me,))
        except Exception:  # noqa: BLE001, S110
            pass
        self._renewed = float("-inf")

    def slow(self) -> None:
        """Gmail said rate limit: spend more slowly for the rest of this process."""
        with self._lock:
            self._bucket.slow(limits.GMAIL_RATE_SLOWDOWN, limits.GMAIL_MIN_UNITS_PER_MINUTE)
        api_counts.add("gmail_slowdowns", 1)

    @contextmanager
    def spend(self, method: str, url: str) -> Iterator[None]:
        if not self._enabled:
            yield
            return
        cost = units(method, url)
        self._slots.acquire()
        try:
            self._lease()
            with self._lock:
                now = time.monotonic()
                pause = self._bucket.take(cost, now)
                self._window.append((now + pause, cost))
                self._in_flight += 1
                flying = self._in_flight
            if pause:
                time.sleep(pause)
                api_counts.add("gmail_wait_s", round(pause, 1))
            with self._lock:
                edge = time.monotonic() - 1.0
                while self._window and self._window[0][0] < edge:
                    self._window.popleft()
                busy = sum(amount for _, amount in self._window)
            api_counts.add("gmail_units", cost)
            api_counts.add("gmail_peak_units_per_s", busy)
            api_counts.add("gmail_peak_in_flight", flying)
            yield
        finally:
            with self._lock:
                self._in_flight = max(self._in_flight - 1, 0)
            self._slots.release()


GATE = Gate()
