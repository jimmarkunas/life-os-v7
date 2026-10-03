from http.client import InvalidURL
import unittest
from unittest import mock

from lifeos.platform import http


class BadRedirectTests(unittest.TestCase):
    def test_a_malformed_address_is_a_miss_not_a_crash(self):
        with mock.patch.object(http._OPENER, "open", side_effect=InvalidURL("URL can't contain control characters")):
            got = http.fetch("https://example.com/job/123456")
        self.assertEqual((got.status, got.error), (0, "bad_url"))


if __name__ == "__main__":
    unittest.main()
