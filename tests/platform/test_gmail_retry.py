import io
import json
import unittest
import urllib.error
from unittest import mock

from lifeos.platform import gmail


def http_error(code, reason):
    body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode() if reason else b""
    return urllib.error.HTTPError("https://gmail.googleapis.com/x", code, "x", {}, io.BytesIO(body))


class Ok:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"ok": 1}'


class RetryTests(unittest.TestCase):
    def call(self, failures):
        g = gmail.Gmail("id", "secret", "refresh")
        g._token = "t"
        side = list(failures) + [Ok()]
        with mock.patch("urllib.request.urlopen", side_effect=side), mock.patch("time.sleep") as slept:
            try:
                return g._request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/1"), [c.args[0] for c in slept.call_args_list]
            except gmail.GmailError as e:
                return str(e), [c.args[0] for c in slept.call_args_list]

    def test_a_per_user_rate_limit_is_waited_out_for_about_a_minute(self):
        result, waits = self.call([http_error(403, "rateLimitExceeded") for _ in range(5)])
        self.assertEqual(result, {"ok": 1})
        self.assertEqual([int(w) for w in waits], [2, 4, 8, 16, 32])
        self.assertTrue(all(0 <= w - int(w) < 1 for w in waits))

    def test_a_rate_limit_that_outlasts_six_tries_still_fails_with_its_reason(self):
        result, waits = self.call([http_error(429, "") for _ in range(6)])
        self.assertIn("HTTP 429", result)
        self.assertEqual(len(waits), 5)

    def test_server_errors_keep_the_shorter_schedule_and_other_errors_do_not_retry(self):
        result, waits = self.call([http_error(503, "backendError") for _ in range(4)])
        self.assertIn("HTTP 503", result)
        self.assertEqual(waits, [2, 4, 8])
        result, waits = self.call([http_error(404, "notFound")])
        self.assertIn("HTTP 404", result)
        self.assertEqual(waits, [])


if __name__ == "__main__":
    unittest.main()
