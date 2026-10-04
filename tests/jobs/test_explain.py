import unittest

from lifeos.jobs import explain


class Explain(unittest.TestCase):
    ROWS = [(1, "PUBLISHED", "web:su-revolut-ltd", 70, "ok", None),
            (2, "RESOLVED", "web:su-revolut-ltd", 62, "Fit 62 below 68", None),
            (3, "HOLD", "lensa", None, None, "http_403"),
            (4, "EXCLUDED_FIT", "web:titlewatch:x", 40, "Fit 40 below 60", "lane_exclude")]

    def test_counts_and_open_lines_carry_codes_only(self):
        out = explain.summarize(self.ROWS)
        self.assertEqual(out["by_status"], {"PUBLISHED": 1, "RESOLVED": 1, "HOLD": 1, "EXCLUDED_FIT": 1})
        self.assertEqual(out["open_total"], 2)
        self.assertEqual([j["status"] for j in out["open"]], ["RESOLVED", "HOLD"])
        self.assertEqual(out["open"][0]["why"], "Fit N below N")
        self.assertEqual(out["open"][1]["stop"], "http_N")
        self.assertEqual(out["open"][1]["via"], "lensa")
        for job in out["open"]:
            self.assertEqual(len(job["h"]), 6)
            self.assertNotIn("revolut", str(job))

    def test_needs_a_company(self):
        self.assertEqual(explain.run(1, False, environ={}), {"error": "EXPLAIN_COMPANY_missing_or_short"})
