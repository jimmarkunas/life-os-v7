"""Hard spending/rate guard for TinyFish (Jim's wallet is $35 total and must never be used up).

Only the FREE Fetch API is ever called (see tinyfish.py: one endpoint, no paid code path). Free limits are
150 URLs/min and 1,000 URLs/day; we stay under both with headroom and count pessimistically (every URL we SEND counts,
success or failure). The daily count lives in Hostinger so it survives across hourly runs.
"""
from datetime import datetime, timezone
import time

FETCH_DAILY_CAP = 900        # of 1,000 free URLs per day
FETCH_BATCH = 10             # API maximum per request
MIN_SECONDS_PER_BATCH = 6    # 10 URLs / 6 s = 100 per minute, under the 150/min cap


def granted(used, cap, wanted):
    """How many of `wanted` URLs may be sent given `used` so far today."""
    return max(0, min(wanted, cap - used))


def today():
    return datetime.now(timezone.utc).date()


def used_today(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT fetch_urls FROM v7_spend WHERE day = %s", (today(),))
        row = cursor.fetchone()
    return row[0] if row else 0


def reserve(connection, wanted):
    """Atomically count up to `wanted` URLs against today's cap BEFORE sending. Returns how many may be sent."""
    allowed = granted(used_today(connection), FETCH_DAILY_CAP, wanted)
    if allowed:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO v7_spend (day, fetch_urls) VALUES (%s, %s) "
                           "ON DUPLICATE KEY UPDATE fetch_urls = fetch_urls + VALUES(fetch_urls)", (today(), allowed))
    return allowed


class Pacer:
    """Keeps request batches at least MIN_SECONDS_PER_BATCH apart."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self._clock, self._sleep, self._last = clock, sleep, None

    def wait(self):
        if self._last is not None:
            remaining = MIN_SECONDS_PER_BATCH - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()
