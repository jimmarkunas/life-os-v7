"""the one clock a test may read."""

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
    """`datetime` with now, utcnow and today fixed at the test clock"""

    @classmethod
    def now(cls, tz=None):
        moment = now(tz)
        return (
            cls(
                moment.year,
                moment.month,
                moment.day,
                moment.hour,
                moment.minute,
                moment.second,
                moment.microsecond,
                tzinfo=moment.tzinfo,
            )
            if tz is not None
            else cls(*naive_utc().timetuple()[:6])
        )

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
