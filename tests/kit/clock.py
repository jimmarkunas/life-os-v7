"""LIFE-692: the one clock a test may read. A test that asks the real clock for "now" passes or fails depending on the day it runs (a date-bound test stopped the jobs pipeline from publishing on
Nov 6 2026, LIFE-349; an earlier one failed the same way, LIFE-264), so a test takes its time from here: a fixed moment, or the moment named by the environment variable LIFEOS_TEST_NOW
(an ISO date or time, UTC when no zone is given), which CI sets to two different dates to prove no result depends on today's date.

    from tests.kit import clock
    clock.now()                       # an aware UTC datetime; clock.now(tz) converts it
    clock.today(), clock.time()       # the same moment as a date and as epoch seconds
    clock.naive_utc()                 # the same moment without a zone, for code that stores naive UTC
    mock.patch.object(module, "datetime", clock.FrozenDateTime)   # a module that did `from datetime import datetime` and asks for now()
    mock.patch.object(module, "date", clock.FrozenDate)           # ... and for date.today()

tests/contracts/test_frozen_clock.py fails a test file that calls the real clock (`datetime.now`, `date.today`, `time.time`, `datetime.utcnow`, `datetime.today`) unless the file is listed with a reason in
docs/platform/scheduled-checks.md (the allowlist)."""
import datetime as dt
import os

DEFAULT = "2026-11-14T18:00:00+00:00"


def _start():
    raw = os.environ.get("LIFEOS_TEST_NOW", "").strip() or DEFAULT
    value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


START = _start()


def now(tz=None):
    return START if tz is None else START.astimezone(tz)


def naive_utc():
    return START.astimezone(dt.timezone.utc).replace(tzinfo=None)


def today():
    return START.date()


def time():
    return START.timestamp()


class FrozenDateTime(dt.datetime):
    """`datetime` with now(), utcnow() and today() fixed at the test clock; every other method is the real one."""

    @classmethod
    def now(cls, tz=None):
        moment = now(tz)
        return cls(moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second, moment.microsecond, tzinfo=moment.tzinfo) if tz is not None \
            else cls(*naive_utc().timetuple()[:6])

    @classmethod
    def utcnow(cls):
        return cls(*naive_utc().timetuple()[:6])

    @classmethod
    def today(cls):
        return cls.now()


class FrozenDate(dt.date):
    @classmethod
    def today(cls):
        day = today()
        return cls(day.year, day.month, day.day)
