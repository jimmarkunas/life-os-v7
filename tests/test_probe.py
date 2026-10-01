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


class Discover(unittest.TestCase):
    def test_finds_a_listable_board_or_an_own_site_and_never_prints_urls(self):
        sources = [{"id": "su-a", "company": "Acme Robotics Ltd"}, {"id": "su-b", "company": "Beta Foods Limited"}, {"id": "su-c", "company": "Gamma Labs Ltd"}]
        results = {
            "Acme Robotics Ltd careers jobs UK": [{"url": "https://uk.linkedin.com/company/acme", "title": "Acme Robotics", "snippet": ""},
                                                   {"url": "https://boards.greenhouse.io/acmerobotics", "title": "Acme Robotics jobs", "snippet": ""}],
            "Beta Foods Limited careers jobs UK": [{"url": "https://www.betafoods.co.uk/careers", "title": "Careers at Beta Foods", "snippet": ""}],
            "Gamma Labs Ltd careers jobs UK": [{"url": "https://www.indeed.com/cmp/gamma", "title": "Gamma Labs", "snippet": ""}],
        }
        out = probe.discover(lambda query: results[query], sources)
        self.assertEqual(out, {"su-a": "greenhouse:acmerobotics", "su-b": "own:betafoods.co.uk", "su-c": None})

    def test_an_unrelated_result_is_not_a_match(self):
        out = probe.discover(lambda q: [{"url": "https://boards.greenhouse.io/other", "title": "Other Co", "snippet": ""}], [{"id": "su-a", "company": "Acme Robotics Ltd"}])
        self.assertEqual(out, {"su-a": None})
