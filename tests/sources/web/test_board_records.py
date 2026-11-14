"""LIFE-616 (Jim approved the rule and the 20-second save limit, comment 12836): a board that fails, times out on its read or times out on its save is that board's red entry; the stage fails
only when at least 3 boards AND at least half of the boards due failed (LIFE-676: a slow save is counted and named but is not one of them). Per-board seconds are recorded and printed. Fake boards and an in-memory repo; counts and public ids only."""
import datetime as dt
import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from lifeos.jobs import alerts as jobs_alerts
from lifeos.sources.web import lister, run
from tests.sources.web.test_web import MemRepo, fixed

NOW = datetime(2026, 10, 1, 12, 0)
GOOD = lister.Listing(lister.COMPLETE, [])


def boards(*names):
    return [{"id": n, "company": n.upper(), "kind": "greenhouse", "slug": n} for n in names]


class SlowSave(MemRepo):
    """A repo whose save for one board stalls until released."""

    def __init__(self, stuck):
        super().__init__()
        self.stuck, self.release = stuck, threading.Event()

    def commit(self, o, failures, lane="US Remote"):
        if o.source["id"] == self.stuck:
            self.release.wait(10)
        super().commit(o, failures, lane)


class SaveLimit(unittest.TestCase):
    def test_a_save_that_stalls_fails_that_board_and_is_never_run_beside_the_next_save(self):
        repo = SlowSave("s2")
        try:
            got = run.run(10, True, NOW, fixed({n: GOOD for n in ("s1", "s2", "s3")}), repo, boards("s1", "s2", "s3"), save_seconds=0.2)
        finally:
            repo.release.set()
        self.assertEqual((got["complete"], got["failed"], got["failed_why"], got["save_timeout"], got["save_blocked"]), (1, 2, {"save_timeout": 1, "save_blocked": 1}, 1, 1))   # s3 waited its own limit for the stuck write, never wrote: save_blocked
        self.assertEqual([o.source["id"] for o, _ in repo.commits if o.source["id"] != "s2"], ["s1"])

    def test_one_stuck_save_does_not_fail_the_stage_through_the_boards_queued_behind_it(self):
        repo = SlowSave("s1")                                                     # Nov 13 2026: one slow Greenhouse save left 3 boards behind it red and failed every tick
        try:
            got = run.run(10, True, NOW, fixed({n: GOOD for n in ("s1", "s2", "s3", "s4", "s5", "s6")}), repo, boards("s1", "s2", "s3", "s4", "s5", "s6"), save_seconds=0.1)
        finally:
            repo.release.set()
        self.assertEqual((got["failed_why"].get("save_timeout"), got["save_blocked"]), (1, 5))

    def test_once_the_stalled_save_finishes_the_next_board_commits_normally(self):
        repo = SlowSave("s2")
        threading.Timer(0.1, repo.release.set).start()
        got = run.run(10, True, NOW, fixed({n: GOOD for n in ("s1", "s2", "s3")}), repo, boards("s1", "s2", "s3"), save_seconds=0.3)
        self.assertEqual((got["complete"], got["failed"]), (3, 0))

    def test_an_error_inside_a_save_still_surfaces(self):
        class Broken(MemRepo):
            def commit(self, o, failures, lane="US Remote"):
                raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            run.run(10, True, NOW, fixed({"s1": GOOD}), Broken(), boards("s1"), save_seconds=1)

    def test_a_dry_run_saves_nothing_and_never_times_out(self):
        got = run.run(10, False, NOW, fixed({"s1": GOOD}), SlowSave("s1"), boards("s1"), save_seconds=0.1)
        self.assertEqual((got["complete"], got["failed"], got["save_timeout"]), (1, 0, 0))

    def test_the_limit_is_twenty_seconds(self):
        self.assertEqual(run.SAVE_SECONDS, 20)


class StageRule(unittest.TestCase):
    def fails(self, ok, bad, reason="rate_limited", dead=0):
        names = [f"g{i}" for i in range(ok)] + [f"b{i}" for i in range(bad)] + [f"d{i}" for i in range(dead)]
        lookup = {n: GOOD for n in names if n.startswith("g")} | {n: lister.Listing(lister.FAILED, reason=reason) for n in names if n.startswith("b")} \
            | {n: lister.Listing(lister.FAILED, reason="not_found") for n in names if n.startswith("d")}
        out = io.StringIO()
        with redirect_stdout(out):
            try:
                return run.run(50, True, NOW, fixed(lookup), MemRepo(), boards(*names)), out.getvalue()
            except run.BoardsFailing as error:
                return str(error), out.getvalue()

    def test_one_bad_board_among_many_does_not_fail_the_stage(self):
        got, _ = self.fails(ok=4, bad=1)
        self.assertEqual((got["failed"], got["failed_ids"]), (1, ["b0"]))

    def test_three_failures_that_are_less_than_half_of_the_boards_due_do_not_fail_it(self):
        got, _ = self.fails(ok=7, bad=3)
        self.assertEqual(got["failed"], 3)

    def test_two_failures_of_two_do_not_fail_it_because_three_is_the_minimum(self):
        got, _ = self.fails(ok=0, bad=2)
        self.assertEqual(got["failed"], 2)

    def test_at_least_three_failures_and_at_least_half_fail_the_stage_after_the_counts_are_printed(self):
        code, printed = self.fails(ok=3, bad=3)
        self.assertEqual(code, "WEB_BOARDS_FAILING")
        self.assertIn('"failed": 3', printed)

    def saves(self, stalled, bad=()):
        """Six boards due; the `stalled` ones each take longer than the save limit on their own (not queued behind one stuck write), the `bad` ones fail to read."""
        names = [f"s{i}" for i in range(6)]
        lookup = {n: lister.Listing(lister.FAILED, reason="http_521") if n in bad else GOOD for n in names}

        class Stalls(MemRepo):
            def commit(self, o, failures, lane="US Remote"):
                if o.source["id"] in stalled:
                    time.sleep(0.15)
                super().commit(o, failures, lane)
        return run.run(50, True, NOW, fixed(lookup), Stalls(), boards(*names), save_seconds=0.1)

    def test_slow_saves_alone_never_fail_the_stage_but_are_counted_and_named(self):   # LIFE-676: 3 of 6 saves past the limit failed every tick on Nov 13 2026
        got = self.saves({"s0", "s2", "s4"})
        self.assertEqual((got["failed"], got["save_timeout"], got["failed_ids"], got["complete"]), (3, 3, ["s0", "s2", "s4"], 3))

    def test_slow_saves_do_not_add_to_real_board_failures_but_three_real_ones_still_fail_the_stage(self):
        self.assertEqual(self.saves({"s0", "s2"}, bad={"s1", "s3"})["failed"], 4)       # two real failures: under the minimum of three
        with self.assertRaises(run.BoardsFailing):
            self.saves({"s0"}, bad={"s1", "s3", "s4"})

    def test_boards_that_answer_not_found_never_count_toward_the_stage_rule(self):
        got, _ = self.fails(ok=1, bad=0, dead=5)
        self.assertEqual(got["failed_why"], {"not_found": 5})


class Records(unittest.TestCase):
    def test_every_board_gets_read_and_save_seconds_and_the_ten_slowest_are_in_the_counts(self):
        got = run.run(10, True, NOW, fixed({n: GOOD for n in ("s1", "s2")}), MemRepo(), boards("s1", "s2"))
        self.assertEqual(sorted(row[0] for row in got["slowest"]), ["s1", "s2"])
        self.assertTrue(all(isinstance(row[1], float) and isinstance(row[2], float) for row in got["slowest"]))
        json.dumps(got)

    def test_the_job_summary_table_lists_the_slowest_and_every_failed_board(self):
        rows = [(f"s{i}", "COMPLETE", float(i), 0.1) for i in range(12)] + [("bad", "rate_limited", 0.0, 0.0)]
        text = run.summary_markdown("US Remote", rows)
        self.assertIn("| s11 | COMPLETE | 11.0 | 0.1 |", text)
        self.assertNotIn("| s0 |", text)
        self.assertIn("| bad | rate_limited |", text)

    def test_the_table_is_written_to_the_step_summary_when_there_is_one(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "summary.md"
            os.environ["GITHUB_STEP_SUMMARY"] = str(path)
            try:
                run.run(10, True, NOW, fixed({"s1": GOOD}), MemRepo(), boards("s1"))
            finally:
                del os.environ["GITHUB_STEP_SUMMARY"]
            self.assertIn("| s1 | COMPLETE |", path.read_text())


class Idempotent(unittest.TestCase):
    def test_a_board_run_twice_in_a_row_writes_no_duplicates_and_a_failed_board_stays_one_failure(self):
        job = {"id": "1", "title": "Program Manager", "url": "https://example.test/1", "location": "Remote - US", "posted": None, "content": ""}
        board = lister.Listing(lister.COMPLETE, [job])
        repo = MemRepo()
        lookup = {"s1": board, "s2": lister.Listing(lister.FAILED, reason="rate_limited"), "s3": board}
        first = run.run(10, True, NOW, fixed(lookup), repo, boards("s1", "s2", "s3"))
        items_after_first = {k: dict(v) for k, v in repo.items_by.items()}
        second = run.run(10, True, NOW + dt.timedelta(hours=20), fixed(lookup), repo, boards("s1", "s2", "s3"))
        self.assertEqual(items_after_first, {k: dict(v) for k, v in repo.items_by.items()})              # nothing new was written for the boards that read in full
        self.assertEqual((first["failed"], second["failed"], second["added"]), (1, 1, 0))
        self.assertEqual(repo.state["s2"]["failures"], 2)                                                # the failed board counts one failure per run, not more


class Alert(unittest.TestCase):
    def daily(self):
        today = dt.date(2026, 11, 12)
        return {"board_reads": {today.isoformat(): 60, (today - dt.timedelta(days=1)).isoformat(): 60}, "board_failed": {today.isoformat(): 20, (today - dt.timedelta(days=1)).isoformat(): 20}}

    def alert(self, **extra):
        found = jobs_alerts.detect({"daily": self.daily(), **extra}, dt.datetime(2026, 11, 12, 18, 0))
        return next(a for a in found if a.key == "board_reads_failing")

    def test_the_existing_alert_names_up_to_three_failing_boards(self):
        text = self.alert(failing_boards=["ab-one", "gh-two", "lv-three", "wk-four"]).detail
        self.assertIn("Failing now: ab-one, gh-two, lv-three.", text)
        self.assertNotIn("wk-four", text)

    def test_without_names_the_alert_text_is_unchanged_and_no_new_key_exists(self):
        alert = self.alert()
        self.assertNotIn("Failing now", alert.detail)
        keys = {a.key for a in jobs_alerts.detect({"daily": self.daily(), "failing_boards": ["x"]}, dt.datetime(2026, 11, 12, 18, 0))}
        self.assertIn("board_reads_failing", keys)

    def test_gather_reads_the_failing_boards_excluding_not_found(self):
        statements = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=()):
                statements.append(" ".join(sql.split()))
                self.last = sql

            def fetchone(self):
                return (0,)

            def fetchall(self):
                return [("ab-one",), ("gh-two",)] if "last_status = 'FAILED'" in self.last else []

        class Conn:
            def cursor(self):
                return Cursor()

        got = jobs_alerts.gather(Conn(), dt.datetime(2026, 11, 12, 18, 0))
        self.assertEqual(got["failing_boards"], ["ab-one", "gh-two"])
        self.assertTrue(any("<> 'not_found'" in s and "LIMIT 3" in s for s in statements))


if __name__ == "__main__":
    unittest.main()
