"""LIFE-577: benchmark mode's frozen input, its refusal to overlap, and its settings line."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest import mock

from lifeos.jobs import tick
from lifeos.platform import bench, bench_gate


def run(id, workflow="hourly.yml", status="completed", title="tick", start="2026-11-11T18:00:00Z", end="2026-11-11T18:10:00Z"):
    return {"id": id, "workflow": workflow, "status": status, "display_title": title, "run_started_at": start, "created_at": start, "updated_at": end}


def snapshot_file(tmp):
    path = os.path.join(tmp, "snap.json")
    with open(path, "w") as handle:
        json.dump({"version": 1, "lanes": {"US Remote": ["a", "b"], "US Remote/boards": ["x", "y", "z"], "Scale-Up": ["s"]}}, handle)
    return path


class Frozen(unittest.TestCase):
    def test_off_unless_the_workflow_names_a_snapshot(self):
        self.assertIsNone(bench.frozen("US Remote", {}))
        self.assertIsNone(bench.frozen("US Remote", {"BENCH_SNAPSHOT": "  "}))

    def test_the_ids_come_from_the_lane_and_the_employer_board_pass_has_its_own_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BENCH_SNAPSHOT": snapshot_file(tmp)}
            self.assertEqual(bench.frozen(bench.lane_key("US Remote", None), env), {"a", "b"})
            self.assertEqual(bench.frozen(bench.lane_key("US Remote", "known"), env), {"x", "y", "z"})
            self.assertEqual(bench.frozen(bench.lane_key("Scale-Up", None), env), {"s"})
            self.assertEqual(bench.frozen("Nothing", env), set())

    def test_the_committed_snapshot_has_the_frozen_sizes_and_only_identifiers(self):
        path = os.path.join(os.path.dirname(bench.__file__), "..", "bench", "snapshot.json")
        with open(path) as handle:
            data = json.load(handle)
        self.assertEqual({k: len(v) for k, v in data["lanes"].items()}, {"US Remote": 12, "US Remote/boards": 350, "Scale-Up": 12})
        for ids in data["lanes"].values():
            self.assertTrue(all(isinstance(i, str) and i.replace("-", "").replace("_", "").replace(".", "").isalnum() for i in ids))
        self.assertNotIn("@", json.dumps(data))


class BoardsStageReadsTheFrozenSet(unittest.TestCase):
    def sources(self):
        return [{"id": f"gh-{n}", "kind": "greenhouse", "company": n, "status": "ready", "tier": "employer", "slug": n} for n in ("c", "a", "d", "b")]

    def run_stage(self, frozen_ids):
        from lifeos.sources.web import lister, run as web_run
        asked = []

        def fake_lister(source):
            asked.append(source["id"])
            return lister.Listing(lister.COMPLETE, [])
        repo = mock.MagicMock()
        repo.states.return_value = {"gh-a": {"due_at": datetime(2099, 1, 1), "etag": "e"}}      # gh-a would not be due, and has a validator
        repo.items.return_value = {}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"BENCH_SNAPSHOT": snapshot_file(tmp)}):
            with open(os.environ["BENCH_SNAPSHOT"], "w") as handle:
                json.dump({"lanes": {"US Remote/boards": frozen_ids}}, handle)
            try:
                counts = web_run.run(900, False, now=datetime(2026, 11, 11, 12, 0), lister_fn=fake_lister, repo=repo, sources=self.sources(), select="known")
            finally:
                self.asked, self.repo = asked, repo
        return counts

    def test_a_frozen_run_reads_exactly_the_named_boards_in_id_order_whatever_is_due(self):
        counts = self.run_stage(["gh-d", "gh-a"])
        self.assertEqual(self.asked, ["gh-a", "gh-d"])
        self.assertEqual(counts["due"], 2)
        self.repo.commit.assert_not_called()                           # dry run: nothing written

    def test_a_frozen_id_the_registry_does_not_have_fails_the_run_loudly_and_reads_nothing(self):
        """LIFE-604 item 7: the benchmark used to shrink silently when a frozen id was gone from the registry."""
        with self.assertRaises(bench.FrozenInputMissing) as caught:
            self.run_stage(["gh-d", "gh-a", "gh-zzz"])
        self.assertIn("BENCH_FROZEN_IDS_MISSING", str(caught.exception))
        self.assertIn("1 of 3", str(caught.exception))                  # counts only, no board name
        self.assertNotIn("zzz", str(caught.exception))
        self.assertEqual(self.asked, [])


class CommittedSnapshotMatchesTheRegistry(unittest.TestCase):
    def test_every_frozen_id_is_a_board_the_stage_will_find(self):
        from lifeos.sources.web import lister, registry
        path = os.path.join(os.path.dirname(bench.__file__), "..", "bench", "snapshot.json")
        with open(path) as handle:
            lanes = json.load(handle)["lanes"]
        universe = {"US Remote": registry.for_lane("US Remote"), "Scale-Up": registry.for_lane("Scale-Up"), "US Remote/boards": registry.boards()}
        for key, ids in lanes.items():
            with self.subTest(lane=key):
                have = {s["id"] for s in universe[key] if s["kind"] in lister.READERS}
                self.assertEqual(sorted(set(ids) - have), [])


class Gate(unittest.TestCase):
    def test_a_benchmark_starts_when_nothing_else_runs(self):
        code, message = bench_gate.before([run(1), run(2, "domains.yml", title="domains (after tick)")], 9)
        self.assertEqual(code, 0)
        self.assertIn("starting", message)

    def test_it_refuses_while_a_tick_or_a_domains_run_is_running_or_waiting(self):
        for status in ("in_progress", "queued", "pending", "waiting"):
            code, message = bench_gate.before([run(1, status=status)], 9)
            self.assertEqual(code, 1, status)
            self.assertIn("BENCHMARK_REFUSED", message)
        code, message = bench_gate.before([run(2, "domains.yml", "in_progress", "domains (after tick)")], 9)
        self.assertEqual(code, 1)
        self.assertIn("domains.yml", message)

    def test_it_does_not_count_itself(self):
        self.assertEqual(bench_gate.before([run(9, status="in_progress", title="manual")], 9)[0], 0)

    def test_main_before_exits_one_and_prints_to_stderr(self):
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = bench_gate.main(["before"], {"GITHUB_RUN_ID": "9"}, fetch=lambda: [run(1, status="in_progress")])
        self.assertEqual((code, out.getvalue()), (1, ""))
        self.assertIn("BENCHMARK_REFUSED", err.getvalue())

    def test_overlap_is_found_when_running_times_intersect_and_not_otherwise(self):
        start = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)
        end = datetime(2026, 11, 11, 18, 12, tzinfo=timezone.utc)
        before_run = run(1, end="2026-11-11T17:59:00Z", start="2026-11-11T17:40:00Z")
        during = run(2, "domains.yml", "completed", "domains (after tick)", "2026-11-11T18:05:00Z", "2026-11-11T18:20:00Z")
        still_running = run(3, status="in_progress", start="2026-11-11T18:11:00Z")
        self.assertEqual(bench_gate.overlapping([before_run], 9, start, end), [])
        self.assertEqual(bench_gate.overlapping([during, still_running], 9, start, end), [("domains.yml", "domains (after tick)"), ("hourly.yml", "tick")])
        self.assertIn("NOT COMPARABLE", bench_gate.after([during], 9, start, end))
        self.assertIn("comparable", bench_gate.after([before_run], 9, start, end))

    def test_main_after_writes_the_summary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "summary.md")
            with redirect_stdout(io.StringIO()):
                code = bench_gate.main(["after"], {"GITHUB_RUN_ID": "9", "GITHUB_STEP_SUMMARY": path}, fetch=lambda: [], own_start=lambda run_id: "2026-11-11T18:00:00Z",
                                       now=datetime(2026, 11, 11, 18, 12, tzinfo=timezone.utc))
            self.assertEqual(code, 0)
            with open(path) as handle:
                self.assertIn("comparable", handle.read())


class TickSettings(unittest.TestCase):
    def test_none_outside_benchmark_mode(self):
        self.assertIsNone(tick.benchmark_settings({}))

    def test_settings_name_the_switch_caps_snapshot_and_dry_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            off = tick.benchmark_settings({"BENCH_SNAPSHOT": snapshot_file(tmp)})
            on = tick.benchmark_settings({"BENCH_SNAPSHOT": snapshot_file(tmp), "TICK_CONCURRENCY": "on", "TICK_CAP_ATS": "8"})
        self.assertEqual((off["TICK_CONCURRENCY"], off["mode"]), ("off", "dry (nothing is written)"))
        self.assertEqual((on["TICK_CONCURRENCY"], on["caps"]["ats"], on["frozen"]["US Remote/boards"]), ("on", 8, 3))

    def test_the_summary_is_one_line_of_settings_and_totals(self):
        settings = {"TICK_CONCURRENCY": "off", "frozen": {"US Remote/boards": 350}}
        text = tick.benchmark_summary(settings, {"a": "ok", "b": "failed"}, {"a": 10.0, "b": 5.5}, 20.0)
        self.assertIn("| off | 350 | 15.5 | 20.0 | b |", text)

    def test_a_live_benchmark_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"BENCH_SNAPSHOT": snapshot_file(tmp)}), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(tick.main(["--live"]), 2)
        self.assertIn("BENCHMARK_REFUSED_LIVE", err.getvalue())


def job(name, start, end, conclusion="success"):
    return {"name": name, "started_at": start, "completed_at": end, "conclusion": conclusion}


class DomainsSummary(unittest.TestCase):
    def test_seconds_per_job_longest_first_and_the_wall_time_from_first_start_to_last_end(self):
        jobs = [job("jira", "2026-11-11T18:00:00Z", "2026-11-11T18:01:05Z"), job("recruiters", "2026-11-11T18:00:00Z", "2026-11-11T18:13:02Z"),
                job("report", "2026-11-11T18:13:03Z", "2026-11-11T18:13:03Z", "skipped"), job("agenda", "2026-11-11T18:01:07Z", "2026-11-11T18:03:48Z")]
        text = bench_gate.jobs_summary(jobs)
        self.assertEqual([r[0] for r in bench_gate.job_seconds(jobs)], ["recruiters", "agenda", "jira"])
        self.assertIn("(first job start to last job end): 782**", text)
        self.assertIn("sum of job seconds: 1008", text)
        self.assertIn("| recruiters | 782 | success |", text)

    def test_nothing_finished_says_so(self):
        self.assertIn("No job finished", bench_gate.jobs_summary([]))

    def test_main_jobs_writes_the_summary_and_the_overlap_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "summary.md")
            jobs = {"jobs": [job("jira", "2026-11-11T18:00:00Z", "2026-11-11T18:01:05Z"), job("bench_report", "2026-11-11T18:02:00Z", "2026-11-11T18:02:05Z")]}
            with mock.patch.object(bench_gate.gate, "_api", return_value=jobs), redirect_stdout(io.StringIO()):
                code = bench_gate.main(["jobs"], {"GITHUB_RUN_ID": "9", "GITHUB_STEP_SUMMARY": path}, fetch=lambda: [], own_start=lambda run_id: "2026-11-11T18:00:00Z",
                                       now=datetime(2026, 11, 11, 18, 3, tzinfo=timezone.utc))
            self.assertEqual(code, 0)
            with open(path) as handle:
                text = handle.read()
        self.assertIn("| jira | 65 | success |", text)
        self.assertNotIn("bench_report |", text)
        self.assertIn("comparable", text)


if __name__ == "__main__":
    unittest.main()
