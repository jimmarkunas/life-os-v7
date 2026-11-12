"""LIFE-555: the run-time profile reads only job and step names and start/end times from GitHub's run records, and computes percentiles and the critical path from them."""
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from lifeos.sources.devtools import profile_runs as pr

# A saved, trimmed run record: names are the workflow's own step names; every time is made up.
RECORD = {"jobs": [
    {"name": "prep", "conclusion": "success", "startedAt": "2026-01-01T10:00:00Z", "completedAt": "2026-01-01T10:02:00Z", "steps": [
        {"name": "Set up job", "conclusion": "success", "startedAt": "2026-01-01T10:00:00Z", "completedAt": "2026-01-01T10:00:02Z"},
        {"name": "Load credentials from Bitwarden", "conclusion": "success", "startedAt": "2026-01-01T10:00:02Z", "completedAt": "2026-01-01T10:00:06Z"},
        {"name": "Unit tests", "conclusion": "success", "startedAt": "2026-01-01T10:00:06Z", "completedAt": "2026-01-01T10:01:46Z"},
        {"name": "An optional step", "conclusion": "skipped", "startedAt": "2026-01-01T10:01:46Z", "completedAt": "2026-01-01T10:01:46Z"}]},
    {"name": "finish", "conclusion": "success", "startedAt": "2026-01-01T10:02:00Z", "completedAt": "2026-01-01T10:12:00Z", "steps": [
        {"name": "Jobs tick", "conclusion": "success", "startedAt": "2026-01-01T10:02:00Z", "completedAt": "2026-01-01T10:10:00Z"}]},
    {"name": "interview", "conclusion": "skipped", "startedAt": "2026-01-01T10:00:00Z", "completedAt": "2026-01-01T10:00:00Z", "steps": []},
    {"name": "lensa", "conclusion": "success", "startedAt": "2026-01-01T10:02:00Z", "completedAt": "2026-01-01T10:05:00Z", "steps": []}]}
NEEDS = {"prep": [], "lensa": ["prep"], "finish": ["prep"], "report": ["prep", "lensa", "finish"], "interview": ["prep"]}


class Parts(unittest.TestCase):
    def test_a_record_keeps_seconds_per_job_and_step_and_leaves_skipped_ones_out(self):
        record = pr.run_record(RECORD)
        self.assertEqual(record["wall"], 720)
        self.assertEqual(set(record["jobs"]), {"prep", "finish", "lensa"})
        self.assertEqual(record["jobs"]["prep"]["steps"], {"Set up job": 2, "Load credentials from Bitwarden": 4, "Unit tests": 100})
        self.assertEqual(record["jobs"]["finish"]["seconds"], 600)

    def test_percentiles_are_nearest_rank(self):
        values = list(range(1, 11))
        self.assertEqual((pr.percentile(values, 50), pr.percentile(values, 95), pr.percentile([7], 95)), (5, 10, 7))
        self.assertEqual(pr.stats([]), {"n": 0, "p50": 0, "p95": 0})

    def test_a_missing_or_backwards_time_is_not_a_duration(self):
        self.assertIsNone(pr.span(None, "2026-01-01T10:00:00Z"))
        self.assertIsNone(pr.span("2026-01-01T10:00:05Z", "2026-01-01T10:00:00Z"))
        self.assertEqual(pr.span("2026-01-01T10:00:00Z", "2026-01-01T10:01:05Z"), 65)

    def test_the_critical_path_is_the_longest_chain_through_needs(self):
        seconds = {name: job["seconds"] for name, job in pr.run_record(RECORD)["jobs"].items()}
        total, chain = pr.critical_path(NEEDS, seconds)
        self.assertEqual((total, chain), (120 + 600, ["prep", "finish", "report"]))
        self.assertEqual(pr.critical_path({}, {}), (0, []))

    def test_summary_gives_p50_p95_and_the_startup_and_unit_test_shares(self):
        slow = pr.run_record(RECORD)
        fast = pr.run_record({"jobs": [{"name": "prep", "conclusion": "success", "startedAt": "2026-01-01T10:00:00Z", "completedAt": "2026-01-01T10:01:00Z", "steps": [
            {"name": "Unit tests", "conclusion": "success", "startedAt": "2026-01-01T10:00:00Z", "completedAt": "2026-01-01T10:00:50Z"}]}]})
        s = pr.summarize([slow, fast])
        self.assertEqual((s["runs"], s["wall"]["p50"], s["wall"]["p95"]), (2, 60, 720))
        self.assertEqual(s["jobs"]["prep"]["steps"]["Unit tests"], {"n": 2, "p50": 50, "p95": 100})
        self.assertGreater(s["unit_test_share"], 0)
        self.assertGreater(s["startup_share"], 0)

    def test_the_workflow_file_gives_the_needs_graph(self):
        needs = pr.needs_from_workflow(pr.ROOT / ".github" / "workflows" / "hourly.yml")
        self.assertIn("prep", needs["report"])
        self.assertEqual(needs["prep"], [])


class Picking(unittest.TestCase):
    RUNS = [{"databaseId": i, "conclusion": c, "event": e, "displayTitle": t} for i, (c, e, t) in enumerate([
        ("success", "workflow_dispatch", "tick"), ("cancelled", "workflow_dispatch", "tick"), ("failure", "workflow_dispatch", "tick"), ("success", "workflow_dispatch", "manual"),
        ("success", "workflow_dispatch", "tick"), ("skipped", "workflow_run", "domains (after tick)"), ("success", "workflow_dispatch", "tick")], 1)]

    def test_cancelled_and_skipped_are_left_out_and_kinds_and_limits_are_kept_apart(self):
        chosen = pr.pick(self.RUNS, "hourly", last=2, failed=1)
        self.assertEqual(chosen, {("success", "tick"): [1, 5], ("failure", "tick"): [3], ("success", "manual"): [4]})

    def test_domains_runs_are_after_tick_or_manual(self):
        runs = [{"databaseId": 1, "conclusion": "success", "event": "workflow_run", "displayTitle": "domains"}, {"databaseId": 2, "conclusion": "success", "event": "workflow_dispatch", "displayTitle": "domains"}]
        self.assertEqual(set(pr.pick(runs, "domains", 5, 5)), {("success", "after tick"), ("success", "manual")})


class Command(unittest.TestCase):
    def test_collect_reads_the_list_then_each_chosen_run_and_render_prints_only_names_and_seconds(self):
        calls = []

        def fake(args):
            calls.append(args[:2])
            return [{"databaseId": 7, "conclusion": "success", "event": "workflow_dispatch", "displayTitle": "tick"}] if args[0] == "run" and args[1] == "list" else RECORD
        groups = pr.collect("hourly", 5, 5, fetch=fake)
        self.assertEqual(calls, [["run", "list"], ["run", "view"]])
        text = pr.render("hourly", groups, NEEDS)
        self.assertIn("critical path", text)
        self.assertIn("Unit tests: n=1 p50 100 s", text)

    def test_an_unreadable_record_is_a_fixed_line_not_a_traceback(self):
        out = io.StringIO()
        with mock.patch.object(pr, "collect", side_effect=RuntimeError("gh run failed")), redirect_stdout(out):
            self.assertEqual(pr.main(["--workflow", "hourly"]), 1)
        self.assertEqual(out.getvalue().strip(), "profile_runs: could not read the run records")


if __name__ == "__main__":
    unittest.main()
