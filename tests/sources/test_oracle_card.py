import contextlib
import io
import json
import unittest
from unittest import mock

from lifeos.jobs.fit import capacity_card, edge
from lifeos.oraclecloud import card
from lifeos.sources import oracle_card
import datetime as dt

NOW = dt.datetime(2026, 10, 24, 17, 17, tzinfo=dt.timezone.utc)


def reading(five=0, week=2, extra=()):
    return {"read_at": "2026-10-24T17:17:00Z", "unavailable": [], "safety_cap": None,
            "pools": [{"provider": "openai", "stop": 50, "windows": [{"label": "5h", "used": five, "reset_at": "2026-10-24T22:17:00Z"},
                                                                       {"label": "Week", "used": week, "reset_at": "2026-10-30T17:41:00Z"}]}, *extra]}


class Mirror(unittest.TestCase):
    def test_left_is_one_hundred_minus_used_for_both_windows(self):
        self.assertEqual(oracle_card.plan_from(reading(0, 2)), {"left_5h": 100, "left_week": 98})
        self.assertEqual(oracle_card.plan_from(reading(100, 100)), {"left_5h": 0, "left_week": 0})

    def test_no_codex_pool_or_a_missing_window_is_none(self):
        self.assertIsNone(oracle_card.plan_from({"pools": [], "read_at": "x", "unavailable": [], "safety_cap": None}))
        one = reading()
        one["pools"][0]["windows"] = one["pools"][0]["windows"][:1]
        self.assertIsNone(oracle_card.plan_from(one))
        self.assertIsNone(oracle_card.plan_from(reading(extra=())["pools"] and {"pools": [{"provider": "groq", "windows": []}]}))

    def test_the_oracle_card_and_ai_capacity_show_the_same_numbers_for_every_used_pair(self):
        for five, week in ((0, 0), (0, 2), (37, 64), (99, 100), (100, 100)):
            r = reading(five, week)
            capacity = capacity_card.codex_line(r)                                   # "Codex allowance · 5-hour 37% used · weekly 64% used · Cloud Fit stops at 50%"
            plan = oracle_card.plan_from(r)
            self.assertIn(f"5-hour {five:g}% used", capacity)
            self.assertIn(f"weekly {week:g}% used", capacity)
            self.assertEqual((plan["left_5h"], plan["left_week"]), (100 - five, 100 - week))

    def test_run_hands_the_card_the_limits_reading_and_the_host(self):
        seen = {}

        def fake_card_run(limit, live, environ, plan=None, **kw):
            seen["plan"] = plan()
            return {"written": 2}
        with mock.patch.object(edge, "limits", lambda host: seen.setdefault("host", host) and reading(0, 2)), mock.patch.object(card, "run", fake_card_run):
            counts = oracle_card.run(0, True, {"IMESSAGE_ALERTS_HOST": " 100.64.0.9 "})
        self.assertEqual((counts, seen), ({"written": 2}, {"host": "100.64.0.9", "plan": {"left_5h": 100, "left_week": 98}}))

    def test_a_failed_limits_read_is_none_with_a_fixed_code_in_the_log(self):
        def boom(host):
            raise edge.EdgeError("FIT_EDGE_NO_REPLY")
        got, out = {}, io.StringIO()
        with mock.patch.object(edge, "limits", boom), mock.patch.object(card, "run", lambda limit, live, environ, plan=None, **kw: got.setdefault("plan", plan())), \
                contextlib.redirect_stdout(out):
            oracle_card.run(0, True, {"IMESSAGE_ALERTS_HOST": "h"})
        self.assertIsNone(got["plan"])
        self.assertEqual(out.getvalue().strip(), 'oracle-card: codex limits unavailable ' + json.dumps({"code": "FIT_EDGE_NO_REPLY"}))


if __name__ == "__main__":
    unittest.main()
