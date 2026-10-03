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
        if "FROM v7_jobs j" in sql:
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

    def test_read_only(self):
        _, sql = self.go()
        self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in sql))

    def test_bands(self):
        self.assertEqual([funnel.band(s) for s in (None, 59, 60, 67, 68, 71, 72, 79, 80)], ["unscored", "lt60", "60_67", "60_67", "68_71", "68_71", "72_79", "72_79", "80_up"])


if __name__ == "__main__":
    unittest.main()
