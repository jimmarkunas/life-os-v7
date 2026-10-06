import unittest

from lifeos.jobs import linkcheck
from lifeos.platform.http import Fetched

PAGE = "<html><title>Careers</title><body>" + ("Senior Project Manager role. Responsibilities include delivery and requirements and experience. " * 20) + "</body></html>"


def fetch_ok(url, **k):
    return Fetched(url, 200, PAGE, 0, "")


class LinkCheckTests(unittest.TestCase):
    def test_a_readable_page_is_classified_by_fixed_codes_only(self):
        got = linkcheck.classify("https://example.com/jobs/1", "Senior Project Manager", fetch_ok)
        self.assertIn("status_200", got)
        self.assertIn("w_80plus", got)
        self.assertNotIn("example.com", got)

    def test_revolut_403_retries_as_a_browser_and_reports_both(self):
        plain = lambda url, **k: Fetched(url, 403, "", 0, "http")
        browser = lambda url, **k: Fetched(url, 403, "", 0, "http")
        got = linkcheck.classify("https://www.revolut.com/en-GB/careers/position/x-1/", "Product Owner", plain, browser)
        self.assertEqual(got.split("/")[-2:], ["plain_403", "status_403"])

    def test_a_blank_page_is_a_status_code_not_text(self):
        self.assertTrue(linkcheck.classify("https://example.com/jobs/2", "Role", lambda url, **k: Fetched(url, 404, "", 0, "http")).endswith("status_404"))


if __name__ == "__main__":
    unittest.main()
