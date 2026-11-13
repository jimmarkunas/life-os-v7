import unittest

from lifeos import probe
from lifeos.platform.http import Fetched


class ProbeTests(unittest.TestCase):
    def test_open_jobs_reports_key_names_only(self):
        def fetcher(url, **kw):
            return Fetched(url, 200, '{"generation": "g1", "days": [{"date": "2026-10-01", "n": 3}]}')
        out = probe.open_jobs(fetcher)
        self.assertEqual(out["manifest.json"]["shape"]["generation"], "str")
        self.assertNotIn("g1", str(out))

    def test_teamtailor_counts(self):
        html = '<a href="/jobs/123-pm">x</a><a href="/jobs/123-pm">x</a><a href="/jobs/456-eng">y</a>'
        out = probe.teamtailor(lambda url, **kw: Fetched(url, 200, html))
        self.assertEqual(len(out), 6)
        self.assertEqual(next(iter(out.values()))["job_links"], 2)

    def test_dice_without_links_is_a_clean_count(self):
        out = probe.dice(lambda url, **kw: Fetched(url, 403, ""))
        self.assertEqual((out["search_status"], out["detail_links"], [p["status"] for p in out["pages"]]), (403, 0, [403]))
