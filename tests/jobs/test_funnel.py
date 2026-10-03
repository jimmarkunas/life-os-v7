import unittest
from datetime import datetime
from unittest import mock

from lifeos.jobs import funnel

NOW = datetime(2026, 10, 10, 12, 0)


class FakeCur:
    def __init__(self):
        self.sql, self.rows = [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        self.sql.append(sql)
        if "j.enrich_attempts" in sql:
            self.rows = [("jobright", "PUBLISHED", "", 0, 1, "ats", "title", "Admitted", 81, 2), ("jobright", "HOLD", "http_403", 3, 0, "", "", None, None, 5),
                         ("lensa", "NEW", "", 0, 0, "", "", None, None, 1)]
        elif "FROM v7_jobs j" in sql:
            self.rows = [("jobright", "Passed / Review", "Fit 67 below 68", 67, 4), ("jobright", "Admitted", "", 81, 2), ("lensa", None, None, None, 1)]
        elif "TIMESTAMPDIFF" in sql:
            self.rows = [("jobright", 3, 5), ("lensa", 30, None)]
        else:
            self.rows = [("jobright", "PUBLISHED", 2), ("jobright", "HOLD", 7), ("unknown", "NEW", 1)]

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self):
        self.cur = FakeCur()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cur


class FunnelTests(unittest.TestCase):
    def go(self):
        c = Conn()
        with mock.patch.object(funnel.store, "connect", return_value=c), mock.patch.object(funnel.store, "ensure_schema"):
            return funnel.run(0, False, now=NOW), c.cur.sql

    def test_report_is_counts_by_source_status_band_and_reason(self):
        out, _ = self.go()
        day = out["windows"]["24h"]
        self.assertEqual(day["status"], {"jobright": {"PUBLISHED": 2, "HOLD": 7}, "unknown": {"NEW": 1}})
        self.assertEqual(day["fit_by_band"]["jobright"], {"Passed / Review:60_67": 4, "Admitted:80_up": 2})
        self.assertEqual(day["fit_by_reason"]["jobright"], {"Passed / Review:Fit N below N": 4, "Admitted:none": 2})
        self.assertEqual(day["fit_by_band"]["lensa"], {"none:unscored": 1})
        self.assertEqual(set(out["windows"]), {"24h", "168h"})
        self.assertEqual(out["freshness_hours"], {"jobright": {"since_found": 3, "since_published": 5}, "lensa": {"since_found": 30, "since_published": None}})

    def test_the_canary_gives_each_source_a_verdict_and_the_trace_of_its_newest_jobs(self):
        out, _ = self.go()
        canary = out["canary"]
        self.assertEqual(canary["jobright"]["verdict"], "flowing")                      # a job published in the last 24 hours
        self.assertEqual(canary["jobright"]["newest"][0], {"stage": "published", "status": "PUBLISHED", "reason": "none", "resolve_attempts": 0,
                                                           "enrich_attempts": 1, "link": "ats", "proof": "title", "fit": "Admitted:80_up", "age_h": 2})
        self.assertEqual(canary["jobright"]["newest"][1]["stage"], "stuck")
        self.assertEqual(canary["unknown"]["verdict"], "stuck_at_link")                 # only NEW jobs, none published
        self.assertEqual(canary["lensa"]["newest"][0]["stage"], "found")

    def test_verdicts(self):
        self.assertEqual(funnel.verdict({}), "silent")
        self.assertEqual(funnel.verdict({"NEW": 5}), "stuck_at_link")
        self.assertEqual(funnel.verdict({"RESOLVED": 4, "EXCLUDED_FIT": 1}), "stuck_at_read")
        self.assertEqual(funnel.verdict({"EXCLUDED_FIT": 5}), "found_none_published")
        self.assertEqual(funnel.verdict({"EXCLUDED_FIT": 5, "PUBLISHED": 1}), "flowing")

    def test_read_only(self):
        _, sql = self.go()
        self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in sql))

    def test_bands(self):
        self.assertEqual([funnel.band(s) for s in (None, 59, 60, 67, 68, 71, 72, 79, 80)], ["unscored", "lt60", "60_67", "60_67", "68_71", "68_71", "72_79", "72_79", "80_up"])


if __name__ == "__main__":
    unittest.main()
