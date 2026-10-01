import unittest
from unittest import mock

from lifeos.jobs import enrich


class Page:
    def __init__(self, status=200, html=""):
        self.status, self.html = status, html


JOB = ("<html><head><title>Senior Data Engineer</title><script type='application/ld+json'>"
       '{"@type":"JobPosting","title":"Senior Data Engineer","datePosted":"%s","description":"%s"}'
       "</script></head><body>x</body></html>")
LONG = "<p>" + ("Responsibilities: build pipelines and own data quality across the platform. You will need experience with SQL and Python skills. " * 4) + "</p>"


class EnrichTests(unittest.TestCase):
    def _read(self, html, title="Senior Data Engineer", status=200):
        with mock.patch.object(enrich, "fetch", return_value=Page(status, html)):
            return enrich.read_page("https://careers.example.com/j/1", title)

    def test_fresh_posting_is_ready(self):
        today = enrich._now().date().isoformat()
        self.assertEqual(self._read(JOB % (today, LONG.replace('"', "'")))["outcome"], "ready")

    def test_old_posting_is_stale(self):
        self.assertEqual(self._read(JOB % ("2020-01-01", LONG.replace('"', "'")))["outcome"], "stale")

    def test_dead_page_is_closed(self):
        self.assertEqual(self._read("", status=404)["outcome"], "closed")

    def test_other_job_is_a_mismatch(self):
        today = enrich._now().date().isoformat()
        self.assertEqual(self._read(JOB % (today, LONG.replace('"', "'")), title="Registered Nurse Manager")["outcome"],
                         "mismatch")

    def test_blocked_page_is_retryable(self):
        self.assertEqual(self._read("", status=403)["outcome"], "blocked")

    def test_title_check_is_lenient_but_not_blind(self):
        self.assertTrue(enrich._title_ok("Sr. Data Engineer (Remote)", "Senior Data Engineer - Acme"))
        self.assertFalse(enrich._title_ok("Data Engineer", "Account Executive"))


class BlockedReportTests(unittest.TestCase):
    def test_counts_reasons_and_host_families_without_naming_anyone(self):
        results = [(1, {"outcome": "blocked", "reason": "http_403"}), (2, {"outcome": "blocked", "reason": "http_403"}),
                   (3, {"outcome": "blocked", "reason": "jd_thin"}), (4, {"outcome": "ready"})]
        urls = {1: "https://acme.wd5.myworkdayjobs.com/en/job/1", 2: "https://careers.example.com/j/2",
                3: "https://www.linkedin.com/jobs/view/3", 4: "https://boards.greenhouse.io/x/jobs/4"}
        report = enrich.blocked_report(results, urls)
        self.assertEqual(report["reasons"], {"http_403": 2, "jd_thin": 1})
        self.assertEqual(report["families"], {"myworkdayjobs.com": 1, "employer_site": 1, "linkedin": 1})
        self.assertNotIn("acme", str(report))


class FallbackOnceTests(unittest.TestCase):
    def test_empty_pages_get_one_browser_fetch_then_are_marked_and_skipped(self):
        from unittest import mock
        from tests.kit.db import FakeConn
        rows = [(1, "https://a.example/j/1", "PM", "ats", None), (2, "https://b.example/j/2", "PM", "ats", enrich.TRIED_REASON)]
        results = [(1, {"outcome": "blocked", "reason": "description_empty"}),
                   (2, {"outcome": "blocked", "reason": enrich.TRIED_REASON})]
        counts = {"outcome": {"blocked": 2}}
        asked = []
        with mock.patch.object(enrich.store, "connect", FakeConn), \
                mock.patch.object(enrich.usage, "reserve", lambda c, n: n), \
                mock.patch.object(enrich.tinyfish, "fetch_many", lambda urls, **kw: (asked.extend(urls) or ({}, []))):
            enrich._fallback(results, rows, counts)
        self.assertEqual(asked, ["https://a.example/j/1"])                 # the already-tried page was not sent again
        self.assertEqual(results[0][1]["reason"], enrich.TRIED_REASON)
        self.assertEqual(counts["fallback_attempted"], 1)


class EmbeddedDataTests(unittest.TestCase):
    def test_a_shell_page_with_the_posting_in_next_data_is_read(self):
        body = ("Responsibilities: build pipelines and own data quality. You will need experience with SQL and Python skills. " * 5)
        page = ('<html><title>Senior Data Engineer | Acme</title><body><div id="root"></div>'
                '<script id="__NEXT_DATA__" type="application/json">{"props": {"job": {"description": "' + body + '"}}}</script></body></html>')
        result = enrich.parse_html("https://careers.example.com/j/1", "Senior Data Engineer", page)
        self.assertEqual((result["outcome"], result["source_kind"]), ("ready", "next_data"))


if __name__ == "__main__":
    unittest.main()
