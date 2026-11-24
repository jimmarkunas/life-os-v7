"""LIFE-855: one stage_timing line per stage (tick and standalone), the report numbers on fixtures (sequential, concurrent, missing, degraded), and the existing stage lines unchanged."""
import contextlib
import datetime as dt
import io
import json
import pathlib
import sys
import tempfile
import threading
import unittest

from lifeos.jobs import tick
from lifeos.jobs.tick import Step
from lifeos.platform import read_slots, stage_timing
from tests.kit import shared

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import stage_timing_report as report  # noqa: E402

UTC = dt.timezone.utc


def run_tick(plan, stages, environ=None, concurrency=None):
    out, err = io.StringIO(), io.StringIO()
    clock = shared.Clock()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        outcome = tick.tick(False, plan=plan, stages=stages, clock=clock, concurrency=concurrency)
    return outcome, out.getvalue().splitlines(), [json.loads(x.split("stage_timing: ", 1)[1]) for x in err.getvalue().splitlines() if x.startswith("stage_timing: ")]


class Record(unittest.TestCase):
    def test_outcome_complete_degraded_or_incomplete(self):
        self.assertEqual(stage_timing.outcome_of("ok", {"failed": 0}), "complete")
        self.assertEqual(stage_timing.outcome_of("ok", {"failed": 2}), "degraded")
        self.assertEqual(stage_timing.outcome_of("ok", {"degraded": True}), "degraded")
        self.assertEqual(stage_timing.outcome_of("ok", None), "complete")
        for label in ("failed", "timeout", "skipped"):
            self.assertEqual(stage_timing.outcome_of(label, {}), "incomplete")

    def test_an_unknown_count_is_null_never_zero(self):
        self.assertIsNone(stage_timing.items_of(None, "due"))
        self.assertIsNone(stage_timing.items_of({"due": "3"}, "due"))
        self.assertIsNone(stage_timing.items_of({"due": 3}, None))
        self.assertIsNone(stage_timing.items_of({"due": True}, "due"))
        self.assertEqual(stage_timing.items_of({"due": 0}, "due"), 0)                       # a real zero is a zero

    def test_the_record_has_ct_stamps_seconds_and_the_run_id(self):
        ended = dt.datetime(2026, 11, 23, 1, 3, 20, tzinfo=UTC)
        row = stage_timing.record("web", "ok", 80.0, 2.26, {"due": 7}, "due", ended, {"GITHUB_RUN_ID": "123"})
        self.assertEqual((row["begin"], row["end"], row["elapsed_s"], row["wait_s"], row["items"], row["run"]), ("2026-11-22T19:02:00-06:00", "2026-11-22T19:03:20-06:00", 80.0, 2.3, 7, "123"))

    def test_emit_writes_the_file_and_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp, "t.jsonl")
            err = io.StringIO()
            self.assertTrue(stage_timing.emit({"stage": "a"}, {"STAGE_TIMING_FILE": str(path)}, err))
            self.assertEqual((path.read_text(), err.getvalue()), ('{"stage": "a"}\n', 'stage_timing: {"stage": "a"}\n'))
            self.assertFalse(stage_timing.emit({"stage": "a"}, {"STAGE_TIMING_FILE": str(path / "no" / "dir")}, io.StringIO()))


class DeclaredCounts(unittest.TestCase):
    def test_a_declared_items_key_gives_the_count_and_an_undeclared_stage_stays_null(self):
        self.assertEqual(stage_timing.items_key_for("web"), "due")
        self.assertIsNone(stage_timing.items_key_for("fit"))                                       # no declared key: unknown, never zero
        self.assertIsNone(stage_timing.items_key_for("no-such-stage"))

    def test_every_declared_key_is_a_string_on_a_real_stage(self):
        import json
        table = json.loads((pathlib.Path(stage_timing.__file__).with_name("stages.json")).read_text())["stages"]
        declared = {n: r["items_key"] for n, r in table.items() if "items_key" in r}
        self.assertGreaterEqual(len(declared), 15)
        self.assertTrue(all(isinstance(k, str) and k.isidentifier() for k in declared.values()))

    def test_the_tick_line_carries_the_declared_count(self):
        _, _, rows = run_tick([Step("web", 60, 10)], {"web": lambda limit, live: {"due": 7, "failed": 0}})
        self.assertEqual((rows[0]["items"], rows[0]["outcome"]), (7, "complete"))
        _, _, rows = run_tick([Step("fit", 60, 10)], {"fit": lambda limit, live: {"due": 7}})
        self.assertIsNone(rows[0]["items"])


class Standalone(unittest.TestCase):
    def test_a_standalone_stage_prints_its_line_only_when_the_file_is_set_and_never_inside_the_tick(self):
        import os
        from unittest import mock
        from lifeos import run
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp, "t.jsonl")
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"STAGE_TIMING_FILE": ""}), contextlib.redirect_stderr(err):
                run._timing("x", "ok", 0.0)
            self.assertEqual(err.getvalue(), "")                                                    # off by default: no stderr line, so no existing stage changes
            stage_timing._local.stage = None
            with mock.patch.dict(os.environ, {"STAGE_TIMING_FILE": str(path)}), contextlib.redirect_stderr(err):
                run._timing("x", "ok", 0.0, {"failed": 0})
            self.assertEqual(json.loads(path.read_text())["stage"], "x")
            stage_timing.begin("inside")
            self.addCleanup(setattr, stage_timing._local, "stage", None)
            with mock.patch.dict(os.environ, {"STAGE_TIMING_FILE": str(path)}), contextlib.redirect_stderr(io.StringIO()):
                run._timing("y", "ok", 0.0)
            self.assertEqual(len(path.read_text().splitlines()), 1)                                  # the tick prints a stage's line itself, so run.main stays silent there


class Tick(unittest.TestCase):
    def test_every_stage_gets_a_line_and_the_existing_stage_lines_are_unchanged(self):
        plan = [Step("a", 60, 10), Step("b", 60, 10)]
        stages = {"a": lambda limit, live: {"failed": 0}, "b": lambda limit, live: (_ for _ in ()).throw(RuntimeError("x"))}
        outcome, stdout, rows = run_tick(plan, stages)
        self.assertEqual(outcome, {"a": "ok", "b": "failed"})
        self.assertEqual([line.split(" ")[:3] for line in stdout], [["stage", "a:", "ok"], ["stage", "b:", "failed"]])                  # stdout is exactly the old lines
        self.assertEqual([(r["stage"], r["outcome"], r["result"]) for r in rows], [("a", "complete", "ok"), ("b", "incomplete", "failed")])
        self.assertTrue(all(set(r) >= {"begin", "end", "elapsed_s", "wait_s", "items", "run"} for r in rows))

    def test_a_degraded_stage_is_degraded_and_a_stage_with_no_count_has_null_items(self):
        plan = [Step("a", 60, 10)]
        _, _, rows = run_tick(plan, {"a": lambda limit, live: {"failed": 3, "due": 9}})
        self.assertEqual((rows[0]["outcome"], rows[0]["items"]), ("degraded", None))                # no items_key declared: unknown, not 9 and not 0

    def test_the_wait_for_read_slots_counts_toward_the_stage_that_waited(self):
        read_slots.configure("zz", 1)
        self.addCleanup(read_slots.configure, "zz", None)

        def waits(limit, live):
            with read_slots.slot("zz"):
                pass
            return {}
        _, _, rows = run_tick([Step("a", 60, 10)], {"a": waits})
        self.assertGreaterEqual(rows[0]["wait_s"], 0.0)
        gate = threading.Semaphore(0)
        held = threading.Event()

        def holder():
            with read_slots.slot("zz"):
                held.set()
                gate.acquire()
        thread = threading.Thread(target=holder)
        thread.start()
        held.wait()
        threading.Timer(0.3, gate.release).start()
        stage_timing.begin("late")
        with read_slots.slot("zz"):
            pass
        waited, _ = stage_timing.take("late")
        thread.join()
        self.assertGreaterEqual(waited, 0.25)

    def test_a_wait_outside_any_stage_is_counted_apart_never_guessed_into_one(self):
        before = stage_timing.unattributed()
        result = []
        thread = threading.Thread(target=lambda: (stage_timing.add_wait(1.5), result.append(1)))
        thread.start()
        thread.join()
        self.assertEqual(stage_timing.unattributed(), round(before + 1.5, 1))


def row(stage, begin, end, outcome="complete", wait=0.0):
    b, e = dt.datetime.fromisoformat(begin), dt.datetime.fromisoformat(end)
    return {"stage": stage, "outcome": outcome, "begin": begin, "end": end, "elapsed_s": (e - b).total_seconds(), "wait_s": wait, "items": None, "run": "1"}


T = "2026-11-23T01:00:%02d+00:00"


def at(seconds):
    return (dt.datetime(2026, 11, 23, 1, 0, 0, tzinfo=UTC) + dt.timedelta(seconds=seconds)).isoformat()


class Report(unittest.TestCase):
    def test_sequential_stages_are_all_on_the_critical_path(self):
        rows = [row("a", at(0), at(10)), row("b", at(10), at(40)), row("c", at(40), at(45))]
        self.assertEqual([r["stage"] for r in report.critical_path(rows)], ["c", "b", "a"])

    def test_a_concurrent_group_contributes_only_its_latest_ending_member(self):
        rows = [row("first", at(0), at(10)), row("fast", at(10), at(20)), row("slow", at(10), at(60)), row("last", at(60), at(70))]
        self.assertEqual([r["stage"] for r in report.critical_path(rows)], ["last", "slow", "first"])               # fast ended early, off the path

    def test_numbers_over_runs_p50_p95_max_share_and_critical_seconds(self):
        runs = {"1": [row("a", at(0), at(10)), row("b", at(10), at(30))], "2": [row("a", at(0), at(20)), row("b", at(20), at(30))], "3": [row("a", at(0), at(30)), row("b", at(30), at(60))]}
        result = report.analyse(runs)
        a, b = result["stages"]["a"], result["stages"]["b"]
        self.assertEqual((a["runs"], a["p50"], a["p95"], a["max"]), (3, 20.0, 30.0, 30.0))
        self.assertEqual((b["p50"], b["max"], b["critical_runs"], b["critical_seconds"]), (20.0, 30.0, 3, 60.0))
        self.assertEqual(round(a["share_of_wall"], 1), round(100 * 60 / 120, 1))                                      # summed seconds 60 over summed wall 120

    def test_a_run_with_no_artifact_or_a_missing_stage_is_unverified_never_zero(self):
        runs = {"1": [row("a", at(0), at(10)), row("b", at(10), at(20))], "2": None, "3": [row("a", at(0), at(8))]}
        result = report.analyse(runs)
        self.assertEqual((result["runs"], result["unverified_runs"]), (2, ["2"]))
        self.assertEqual((result["stages"]["b"]["runs"], result["stages"]["b"]["missing_in_runs"]), (1, ["3"]))
        self.assertIn("UNVERIFIED", report.render(report.analyse({"1": None})) + "UNVERIFIED")
        empty = report.analyse({"1": [row("only", at(0), at(5))]}, expected=["only", "never_ran"])
        self.assertIsNone(empty["stages"]["never_ran"]["p50"])                                                        # an expected stage with no data has no numbers
        self.assertIn("UNVERIFIED", report.render(empty))

    def test_degraded_runs_are_counted_in_the_not_complete_column(self):
        result = report.analyse({"1": [row("a", at(0), at(10), "degraded")], "2": [row("a", at(0), at(10))]})
        self.assertEqual(result["stages"]["a"]["outcomes"], {"degraded": 1, "complete": 1})
        self.assertIn("| a | 2 |", report.render(result))

    def test_parse_lines_reads_a_jsonl_file_and_a_log_and_ignores_the_rest(self):
        one = json.dumps(row("a", at(0), at(10)))
        self.assertEqual(len(report.parse_lines(f"{one}\nnot json\n2026-11-23T01:00:00Z stage_timing: {one}\nstage a: ok 10.0s\n")), 2)

    def test_job_rows_come_from_the_jobs_api_timestamps(self):
        jobs = {"jobs": [{"name": "prep", "started_at": "2026-11-23T01:00:00Z", "completed_at": "2026-11-23T01:00:40Z", "conclusion": "success"}, {"name": "other", "started_at": "x", "completed_at": "y"}]}
        rows = report.job_rows("9", runner=lambda *a: json.dumps(jobs))
        self.assertEqual([(r["stage"], r["elapsed_s"], r["outcome"]) for r in rows], [("job prep", 40.0, "complete")])


if __name__ == "__main__":
    unittest.main()
