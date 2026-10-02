import datetime as dt
import json
import unittest

from lifeos.jobs import alerts as jobs_alerts
from lifeos.platform import alerts

NOW = dt.datetime(2026, 10, 25, 12, 0)
QUIET = {"published_24h": 5, "sources_failing": 0, "stuck_jobs": 0}


class Detect(unittest.TestCase):
    def keys(self, counts, expiries=None, now=NOW):
        return sorted(a.key for a in jobs_alerts.detect(counts, now, expiries))

    def test_healthy_counts_raise_nothing(self):
        self.assertEqual(self.keys(QUIET), [])

    def test_each_condition(self):
        self.assertEqual(self.keys({**QUIET, "published_24h": 0}), ["quiet_24h"])
        self.assertEqual(self.keys({**QUIET, "sources_failing": 2}), ["sources_failing"])
        self.assertEqual(self.keys({**QUIET, "stuck_jobs": 24}), [])
        self.assertEqual(self.keys({**QUIET, "stuck_jobs": 25}), ["stuck_jobs"])

    def test_expiry_pages_a_week_before_and_after(self):
        exp = {"timer_token": {"label": "Timer token", "expires": "2026-10-31", "renew": "renew it"}}
        self.assertEqual(self.keys(QUIET, exp, dt.datetime(2026, 10, 23)), [])
        a = jobs_alerts.detect(QUIET, dt.datetime(2026, 10, 24), exp)[0]
        self.assertEqual((a.key, a.severity), ("expiry_timer_token", alerts.PAGE))
        self.assertIn("expired", jobs_alerts.detect(QUIET, dt.datetime(2026, 11, 2), exp)[0].title)

    def test_the_real_expiry_file_is_valid(self):
        for item in json.loads(jobs_alerts.EXPIRIES.read_text()).values():
            dt.date.fromisoformat(item["expires"])
            self.assertTrue(item["label"] and item["renew"])


if __name__ == "__main__":
    unittest.main()
