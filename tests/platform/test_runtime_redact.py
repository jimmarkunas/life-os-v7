import unittest

from lifeos.platform import redact, runtime


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Runtime(unittest.TestCase):
    def test_deadline_and_bounded_timeout(self):
        clock = Clock()
        ctx = runtime.RunContext.start(60, clock=clock)
        self.assertEqual((ctx.expired(), ctx.bounded_timeout(10), ctx.bounded_timeout(500)), (False, 10.0, 60.0))
        clock.now = 45
        self.assertEqual(ctx.bounded_timeout(30), 15.0)
        clock.now = 60
        self.assertTrue(ctx.expired())
        with self.assertRaises(runtime.DeadlineExceeded):
            ctx.require_time()

    def test_a_bad_timeout_is_rejected(self):
        for bad in (0, -1, runtime.MAX_RUNTIME_SECONDS + 1):
            with self.assertRaises(ValueError):
                runtime.RunContext.start(bad)

    def test_results_keep_scalars_only(self):
        result = runtime.ExecutionResult.degraded("parent_ambiguous", candidates=2, page={"id": "x"})
        self.assertEqual((result.status, result.code, result.detail), (runtime.ExecutionStatus.DEGRADED, "parent_ambiguous", {"candidates": 2, "page": "dict"}))
        self.assertEqual(runtime.ExecutionResult.passed(n=1).status, runtime.ExecutionStatus.PASS)
        self.assertEqual(runtime.ExecutionResult.failed("x").detail, {})


class Redact(unittest.TestCase):
    def test_secrets_tokens_and_emails_are_removed(self):
        out = redact.redact({"note": "Bearer abc123 sent to jim@example.com using TOPSECRET", "api_token": "x"}, secrets=["TOPSECRET"])
        self.assertNotIn("abc123", out["note"])
        self.assertNotIn("jim@example.com", out["note"])
        self.assertNotIn("TOPSECRET", out["note"])
        self.assertEqual(out["api_token"], redact.REDACTED)

    def test_meeting_credentials_never_survive(self):
        text = "Join https://acme.zoom.us/j/81234567890?pwd=AbC123xyz&uname=Jim — Meeting ID: 812 3456 7890 Passcode: 7h3Qp9"
        out = redact.redact(text)
        for secret in ("AbC123xyz", "7h3Qp9", "812 3456 7890"):
            self.assertNotIn(secret, out)
        out = redact.redact({"joinUrl": "https://x.example/j/1", "meeting_id": "123", "passcode": "9", "transcript": "..."})
        self.assertEqual(set(out.values()), {redact.REDACTED})

    def test_notion_ids_in_text_and_unknown_objects(self):
        out = redact.redact("page 3d63c5a0-5926-807b-8bf5-ee003bea4607 failed")
        self.assertNotIn("3d63c5a0", out)
        self.assertEqual(redact.redact(object()), "object")
        self.assertEqual(redact.redact(b"raw"), redact.REDACTED)

    def test_input_is_not_mutated_and_errors_are_one_scrubbed_line(self):
        data = {"a": ["Bearer zzz"]}
        redact.redact(data)
        self.assertEqual(data, {"a": ["Bearer zzz"]})
        line = redact.safe_error(ValueError("failed for https://x.example/p?token=abc and jim@example.com"))
        self.assertTrue(line.startswith("ValueError:"))
        self.assertNotIn("token=abc", line)
        self.assertNotIn("jim@example.com", line)


if __name__ == "__main__":
    unittest.main()
