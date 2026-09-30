import unittest
from unittest import mock

from pipeline import enrich


class Page:
    def __init__(self, status=200, html=""):
        self.status, self.html = status, html


JOB = ("<html><head><title>Senior Data Engineer</title><script type='application/ld+json'>"
       '{"@type":"JobPosting","title":"Senior Data Engineer","datePosted":"%s","description":"%s"}'
       "</script></head><body>x</body></html>")
LONG = "<p>" + ("Build pipelines and own data quality across the platform. " * 8) + "</p>"


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


if __name__ == "__main__":
    unittest.main()
