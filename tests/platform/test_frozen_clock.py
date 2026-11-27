"""the test clock reads LIFEOS_TEST_NOW, is the same moment in every form, and its frozen datetime and date classes
answer now and today from it.
"""

import datetime as dt
import importlib
import os
import unittest
from unittest import mock

from tests.kit import clock


def reload_with(value):
    env = {"LIFEOS_TEST_NOW": value} if value is not None else {}
    with mock.patch.dict(os.environ, env, clear=False):
        if value is None:
            os.environ.pop("LIFEOS_TEST_NOW", None)
        return importlib.reload(clock)


class TestClock(unittest.TestCase):
    def setUp(self):
        self.original = os.environ.get("LIFEOS_TEST_NOW")

    def tearDown(self):
        os.environ.pop("LIFEOS_TEST_NOW", None)
        if self.original is not None:
            os.environ["LIFEOS_TEST_NOW"] = self.original
        importlib.reload(clock)

    def test_the_default_is_a_fixed_moment_and_the_variable_moves_it(self):
        default = reload_with(None)
        self.assertEqual(default.now(), dt.datetime(2026, 11, 14, 18, 0, tzinfo=dt.timezone.utc))
        moved = reload_with("2027-03-09")
        self.assertEqual(
            (moved.now(), moved.today()), (dt.datetime(2027, 3, 9, tzinfo=dt.timezone.utc), dt.date(2027, 3, 9))
        )
        zoned = reload_with("2027-03-14T07:30:00Z")
        self.assertEqual(zoned.now().isoformat(), "2027-03-14T07:30:00+00:00")

    def test_every_form_is_the_same_moment(self):
        c = reload_with("2026-12-31T23:59:30+00:00")
        self.assertEqual(c.time(), c.now().timestamp())
        self.assertEqual(c.naive_utc(), dt.datetime(2026, 12, 31, 23, 59, 30))
        self.assertEqual(c.now(dt.timezone(dt.timedelta(hours=-6))).isoformat(), "2026-12-31T17:59:30-06:00")

    def test_the_frozen_classes_answer_now_and_today_and_keep_everything_else(self):
        c = reload_with("2027-03-09T12:00:00+00:00")
        self.assertEqual(c.FrozenDateTime.now(dt.timezone.utc), dt.datetime(2027, 3, 9, 12, tzinfo=dt.timezone.utc))
        self.assertEqual(c.FrozenDateTime.utcnow(), dt.datetime(2027, 3, 9, 12))
        self.assertEqual(c.FrozenDateTime.now(), dt.datetime(2027, 3, 9, 12))
        self.assertEqual(c.FrozenDate.today(), dt.date(2027, 3, 9))
        self.assertEqual(
            c.FrozenDateTime.strptime("2026-01-02", "%Y-%m-%d"), dt.datetime(2026, 1, 2)
        )  # the real methods still work
        self.assertEqual(c.FrozenDateTime(2026, 1, 2) + dt.timedelta(days=1), dt.datetime(2026, 1, 3))


if __name__ == "__main__":
    unittest.main()
