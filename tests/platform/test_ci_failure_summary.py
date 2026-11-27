"""The compact CI failure summary, on a saved log of a real failed run and on small synthetic logs."""

import contextlib
import io
import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import ci_failure_summary as summary  # noqa: E402

LOG = ROOT / "tests" / "fixtures" / "ci_failed_run.txt"
SAMPLE = LOG.read_text()
STEP = "Unit tests"
BAR = "=" * 70
DASH = "-" * 70


def block(test, line, said):
    folder, name = test.split(".")[1:3]
    rows = [
        BAR,
        f"FAIL: {test.rsplit('.', 1)[1]} ({test})",
        DASH,
        "Traceback (most recent call last):",
        f'  File "/home/runner/work/r/r/tests/{folder}/{name}.py", line {line}, in x',
        f"AssertionError: {said}",
        "",
    ]
    return [f"unit-tests\t{STEP}\t2026-11-27T22:05:47.5Z {x}" for x in rows]


class RealFailedRun(unittest.TestCase):
    def setUp(self):
        self.lines = summary.summarize(SAMPLE, "0123456789abcdef", "46074315620")

    def test_it_fits_one_screen_under_thirty_lines(self):
        self.assertLess(len(self.lines), 30)

    def test_it_names_the_sha_the_failed_job_each_failed_test_and_its_line(self):
        text = "\n".join(self.lines)
        self.assertIn("head sha: 0123456789abcdef", text)
        self.assertIn("failed jobs: unit-tests", text)
        self.assertIn("tests.contracts.test_privacy.PrivacyGuard.test_no_live_notion_ids", text)
        self.assertIn("tests/contracts/test_privacy.py:181", text)
        self.assertIn("tests/contracts/test_privacy.py:188", text)
        self.assertIn("AssertionError: Lists differ", text)
        self.assertIn("gh run view 46074315620 --log-failed", text)

    def test_the_per_module_shard_seconds_record_is_not_in_the_summary(self):
        self.assertIn("shard-seconds {", SAMPLE)
        self.assertNotIn("shard-seconds", "\n".join(self.lines))
        self.assertNotIn("test_frozen_clock", "\n".join(self.lines))

    def test_the_cli_reads_a_saved_log(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = summary.main(["--log", str(LOG), "--sha", "abc123"])
        self.assertEqual(code, 0)
        self.assertIn("head sha: abc123", out.getvalue())


class SmallLogs(unittest.TestCase):
    def test_a_second_job_with_the_same_failures_is_one_line(self):
        lines = summary.summarize(SAMPLE + SAMPLE.replace("unit-tests\t", "unit-tests-second-date\t"), "s", "1")
        self.assertIn("unit-tests-second-date: 2 failed, same as unit-tests", lines)
        self.assertEqual(sum("test_no_live_notion_ids" in x for x in lines), 1)

    def test_a_step_that_is_not_a_test_run_shows_its_error_lines(self):
        log = "\n".join(
            [
                "lint\tRuff format check\t2026-11-27T22:05:47.5Z Would reformat: lifeos/a.py",
                "lint\tRuff format check\t2026-11-27T22:05:47.5Z 1 file would be reformatted",
                "lint\tRuff format check\t2026-11-27T22:05:47.5Z ##[error]Process completed with exit code 1.",
                "types\tMypy\t2026-11-27T22:05:47.5Z lifeos/b.py:3: error: Incompatible types [assignment]",
            ]
        )
        text = "\n".join(summary.summarize(log, "s", "1"))
        self.assertIn("lint / Ruff format check: failed", text)
        self.assertIn("Would reformat: lifeos/a.py", text)
        self.assertIn("lifeos/b.py:3: error: Incompatible types", text)
        self.assertNotIn("Process completed with exit code", text)

    def test_many_failures_are_cut_to_one_screen_with_a_pointer_to_the_full_log(self):
        rows = []
        for n in range(40):
            rows += block(f"tests.contracts.test_m{n}.Case.test_x", 10 + n, "boom")
        lines = summary.summarize("\n".join(rows), "s", "9")
        self.assertLess(len(lines), 30)
        self.assertTrue(any("more line(s) in the full log" in x for x in lines))
        self.assertTrue(lines[-1].startswith("full log: gh run view 9"))

    def test_an_error_while_importing_a_test_module_is_listed(self):
        rows = [
            "ERROR: tests.x.test_y (unittest.loader._FailedTest.tests.x.test_y)",
            DASH,
            '  File "/w/tests/x/test_y.py", line 2, in <module>',
            "ImportError: Failed to import test module: tests.x.test_y",
        ]
        log = "\n".join(f"unit-tests\t{STEP}\t2026-11-27T22:05:47.5Z {x}" for x in rows)
        text = "\n".join(summary.summarize(log, "s", "1"))
        self.assertIn("tests/x/test_y.py:2", text)
        self.assertIn("ImportError: Failed to import test module", text)

    def test_a_log_with_nothing_failed_says_so(self):
        text = "\n".join(summary.summarize("", "s", "1"))
        self.assertIn("none found in this log", text)


class WorkflowKeepsRecordsOutOfFailures(unittest.TestCase):
    def test_both_unit_test_jobs_drop_shard_seconds_from_the_printed_log_and_print_them_only_on_green(self):
        doc = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-tests.yml").read_text())
        for job in ("unit-tests", "unit-tests-second-date"):
            run = [s["run"] for s in doc["jobs"][job]["steps"] if "Unit tests" in s.get("name", "")][0]
            self.assertIn("| grep -v '^shard-seconds ' || true", run, job)
            self.assertRegex(run, r'if \[ "\$status" -eq 0 \]; then echo "::group::per-module shard seconds', job)


if __name__ == "__main__":
    unittest.main()
