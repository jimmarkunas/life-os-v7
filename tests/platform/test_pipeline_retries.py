"""A run no longer fails for what the next run would fix: Gmail's rate limit, a GitHub API 5xx, a dropped git fetch."""

import contextlib
import glob
import io
import re
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from lifeos.platform import gate, stage_timing
from lifeos.platform.gmail import GmailError, error_code, is_rate_limit
from lifeos.sources.newsletters import finalize, ingest

ROOT = Path(__file__).resolve().parents[2]
LIMIT = GmailError("GET https://gmail.googleapis.com/gmail/v1/users/me -> HTTP 403 rateLimitExceeded")
DENIED = GmailError("GET https://gmail.googleapis.com/gmail/v1/users/me -> HTTP 403 insufficientPermissions")


def run_main(module, error):
    stderr, stdout = io.StringIO(), io.StringIO()
    with (
        mock.patch.object(module.Gmail, "from_env", side_effect=error),
        contextlib.redirect_stderr(stderr),
        contextlib.redirect_stdout(stdout),
    ):
        code = module.main(["--live"])
    return code, stderr.getvalue(), stdout.getvalue(), stage_timing.take("x")[1]


class RateLimit(unittest.TestCase):
    def test_only_the_rate_limit_that_outlasted_every_retry_counts(self):
        self.assertEqual(error_code(LIMIT), "HTTP_403_RATELIMITEXCEEDED")
        self.assertTrue(is_rate_limit(LIMIT))
        self.assertTrue(is_rate_limit(GmailError("POST https://gmail.googleapis.com/x -> HTTP 429")))
        self.assertTrue(is_rate_limit(GmailError("GET https://x -> HTTP 403 userRateLimitExceeded")))
        for other in (DENIED, GmailError("GET https://x -> HTTP 500 backendError"), GmailError("missing secret 'X'")):
            self.assertFalse(is_rate_limit(other), str(other)[-30:])

    def test_finalize_and_extract_defer_to_the_next_tick_and_report_degraded(self):
        for module, name in ((finalize, "FINALIZE"), (ingest, "EXTRACT")):
            code, err, out, counts = run_main(module, LIMIT)
            self.assertEqual((code, counts), (0, {"degraded": True}), name)
            self.assertIn(f"{name} DEGRADED: HTTP_403_RATELIMITEXCEEDED", err)
            self.assertIn("::warning", out)
            self.assertNotIn("googleapis", err)  # a fixed code, never a URL

    def test_any_other_gmail_error_still_fails_the_stage(self):
        for module, name in ((finalize, "FINALIZE"), (ingest, "EXTRACT")):
            code, err, _, counts = run_main(module, DENIED)
            self.assertEqual((code, counts), (1, None), name)
            self.assertIn(f"{name} FAILED", err)

    def test_a_degraded_stage_is_reported_degraded_not_complete(self):
        self.assertEqual(stage_timing.outcome_of("ok", {"degraded": True}), "degraded")


class GateApi(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"})
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = mock.patch.object(gate.time, "sleep")
        self.slept = sleeper.start()
        self.addCleanup(sleeper.stop)

    @staticmethod
    def reply(body=b'{"ok": 1}'):
        answer = mock.MagicMock()
        answer.__enter__.return_value.read.return_value = body
        return answer

    @staticmethod
    def http(code):
        return urllib.error.HTTPError("https://api.github.com/x", code, "x", None, None)

    def test_a_read_that_gets_a_500_twice_then_works_is_repeated(self):
        with mock.patch.object(
            gate.urllib.request, "urlopen", side_effect=[self.http(500), self.http(502), self.reply()]
        ) as call:
            self.assertEqual(gate._api("issues"), {"ok": 1})
        self.assertEqual((call.call_count, self.slept.call_count), (3, 2))

    def test_a_read_that_keeps_failing_raises_the_last_error_after_three_tries(self):
        with mock.patch.object(gate.urllib.request, "urlopen", side_effect=[self.http(500)] * 3) as call:
            with self.assertRaises(urllib.error.HTTPError):
                gate._api("issues")
        self.assertEqual(call.call_count, 3)

    def test_a_client_error_is_never_repeated_and_a_write_is_never_repeated(self):
        with mock.patch.object(gate.urllib.request, "urlopen", side_effect=[self.http(404)]) as call:
            with self.assertRaises(urllib.error.HTTPError):
                gate._api("issues")
        self.assertEqual(call.call_count, 1)
        with mock.patch.object(gate.urllib.request, "urlopen", side_effect=[self.http(500)]) as call:
            with self.assertRaises(urllib.error.HTTPError):
                gate._api("issues", "POST", {"title": "t"})
        self.assertEqual(call.call_count, 1)


class Checkout(unittest.TestCase):
    def test_every_workflow_checkout_fetch_is_repeated_before_it_fails(self):
        fetch = re.compile(r"git -c \"http\.extraheader=[^\"]*\" fetch -q --no-tags --depth=1 origin \"\$GITHUB_SHA\"")
        found = bare = 0
        for path in glob.glob(str(ROOT / ".github/workflows-src/*/*.yml")):
            for line in Path(path).read_text().split("\n"):
                if fetch.search(line):
                    found += 1
                    bare += "for try in 1 2 3" not in line
        self.assertGreater(found, 0)
        self.assertEqual(bare, 0, "a checkout fetch with no retry")


if __name__ == "__main__":
    unittest.main()
