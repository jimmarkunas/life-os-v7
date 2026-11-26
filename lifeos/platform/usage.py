"""Hard spending and rate guard for TinyFish: only the FREE Fetch API is called. Free limits are 150 URLs/min and 1,000
URLs/day; stay under both and count every URL sent.
"""
from datetime import datetime, timezone
import time

FETCH_DAILY_CAP = 900
FETCH_BATCH = 10
MIN_SECONDS_PER_BATCH = 6


def granted(used, cap, wanted):
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

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self._clock, self._sleep, self._last = clock, sleep, None

    def wait(self):
        if self._last is not None:
            remaining = MIN_SECONDS_PER_BATCH - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()
