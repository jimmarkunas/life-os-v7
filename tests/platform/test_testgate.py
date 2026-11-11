"""LIFE-570: the fail-closed unit-test gate. The tests are skipped only on a confirmed green pr-tests push run on main for this exact commit; every doubt runs them."""
import datetime as dt
import io
import json
import os
import tempfile
import unittest
import urllib.error

from lifeos.platform import testgate as tg

SHA = "a" * 40
NOW = dt.datetime(2026, 11, 11, 7, 0, tzinfo=dt.timezone.utc)
GREEN = {"id": 5, "run_attempt": 1, "head_sha": SHA, "head_branch": "main", "event": "push", "path": tg.PATH, "status": "completed", "conclusion": "success",
         "updated_at": "2026-11-11T06:50:00Z"}


class Decide(unittest.TestCase):
    def test_a_recent_green_push_run_on_this_commit_skips(self):
        skip, why = tg.decide([GREEN], SHA, NOW)
        self.assertTrue(skip)
        self.assertEqual(why, "pr-tests green on SHA aaaaaaaaaaaa (finished 10 min ago)")

    def test_every_other_case_runs_the_tests(self):
        cases = {"none": [], "unreadable": None, "other sha": [{**GREEN, "head_sha": "b" * 40}], "feature branch": [{**GREEN, "head_branch": "x"}],
                 "pull request": [{**GREEN, "event": "pull_request"}], "other workflow": [{**GREEN, "path": ".github/workflows/hourly.yml"}],
                 "red": [{**GREEN, "conclusion": "failure"}], "cancelled": [{**GREEN, "conclusion": "cancelled"}], "running": [{**GREEN, "status": "in_progress", "conclusion": None}],
                 "newer red": [GREEN, {**GREEN, "id": 6, "conclusion": "failure"}], "newer running": [GREEN, {**GREEN, "id": 6, "status": "queued", "conclusion": None}],
                 "too old": [{**GREEN, "updated_at": "2026-11-11T00:00:00Z"}], "no time": [{k: v for k, v in GREEN.items() if k != "updated_at"}],
                 "bad time": [{**GREEN, "updated_at": "yesterday"}], "future time": [{**GREEN, "updated_at": "2026-11-11T09:00:00Z"}], "junk": ["x", 3, None]}
        for name, runs in cases.items():
            self.assertFalse(tg.decide(runs, SHA, NOW)[0], name)

    def test_a_newer_green_run_after_a_red_one_skips_and_a_bad_sha_never_does(self):
        self.assertTrue(tg.decide([{**GREEN, "id": 4, "conclusion": "failure"}, GREEN], SHA, NOW)[0])
        for sha in ("", "main", "A" * 40, "a" * 39, None, 5):
            self.assertFalse(tg.decide([GREEN], sha, NOW)[0], sha)


class Main(unittest.TestCase):
    def run_main(self, answer):
        out, summary = tempfile.mktemp(), tempfile.mktemp()
        env = {"GITHUB_SHA": SHA, "GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "GITHUB_OUTPUT": out, "GITHUB_STEP_SUMMARY": summary}

        def opener(request, timeout=None):
            if isinstance(answer, Exception):
                raise answer
            return io.BytesIO(json.dumps(answer).encode())
        self.addCleanup(lambda: [os.path.exists(p) and os.remove(p) for p in (out, summary)])
        self.assertEqual(tg.main(env, opener, NOW), 0)
        return open(out).read(), open(summary).read()

    def test_green_writes_skip_true_and_the_reason(self):
        out, summary = self.run_main({"workflow_runs": [GREEN]})
        self.assertEqual(out, "skip=true\n")
        self.assertIn("tests skipped: pr-tests green on SHA aaaaaaaaaaaa", summary)

    def test_a_failing_lookup_runs_the_tests(self):
        for answer in (urllib.error.URLError("down"), urllib.error.HTTPError("u", 500, "x", {}, None), ValueError("junk"), {"workflow_runs": []}, {"workflow_runs": None}):
            out, summary = self.run_main(answer)
            self.assertEqual(out, "skip=false\n", answer)
            self.assertIn("tests run:", summary)

    def test_a_missing_environment_runs_the_tests(self):
        self.assertEqual(tg.main({}, lambda *a, **k: (_ for _ in ()).throw(AssertionError("no lookup without a commit")), NOW), 0)


if __name__ == "__main__":
    unittest.main()
