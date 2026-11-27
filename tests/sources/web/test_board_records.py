"""a board that fails, times out on its read or times out on its save is that board's red entry"""
import datetime as dt
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from contextlib import contextmanager

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


class LostSession(MemRepo):
    """A repo on a held connection whose session drops during one board's save (MySQL 2013, as the tunnel does)."""
    _holding = True

    def __init__(self, drops):
        super().__init__()
        self.drops, self.released = drops, 0

    def release(self):
        self.released += 1

    def commit(self, o, failures, lane="US Remote"):
        if o.source["id"] == self.drops:
            import pymysql
            raise pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")
        super().commit(o, failures, lane)


class SaveLost(unittest.TestCase):
    """with one held connection, a session lost between boards fails only that board (it is not advanced and is read
    again next window)
    """

    def test_a_lost_session_fails_that_board_drops_the_connection_and_the_pass_goes_on(self):
        repo = LostSession("s2")
        got = run.run(10, True, NOW, fixed({n: GOOD for n in ("s1", "s2", "s3")}), repo, boards("s1", "s2", "s3"))
        self.assertEqual((got["complete"], got["failed"], got["failed_why"], got["save_lost"]), (2, 1, {"save_lost": 1}, 1))
        self.assertEqual(repo.released, 1)
        self.assertEqual(sorted(o.source["id"] for o, _ in repo.commits), ["s1", "s3"])

    def test_any_other_write_error_still_stops_the_pass_as_before(self):
        class Broken(LostSession):
            def commit(self, o, failures, lane="US Remote"):
                raise RuntimeError("syntax")
        with self.assertRaises(RuntimeError):
            run.run(10, True, NOW, fixed({"s1": GOOD}), Broken("none"), boards("s1"))

    def test_without_a_held_connection_a_lost_session_still_raises_as_before(self):
        repo = LostSession("s1")
        repo._holding = False
        import pymysql
        with self.assertRaises(pymysql.err.OperationalError):
            run.run(10, True, NOW, fixed({"s1": GOOD}), repo, boards("s1"))


REAL_PORT = os.environ.get("LIFEOS_TEST_MARIADB_PORT")


@unittest.skipUnless(REAL_PORT, "no throwaway local MariaDB (set LIFEOS_TEST_MARIADB_PORT and LIFEOS_TEST_MARIADB_PASSWORD_FILE)")
class RealDriverSessionLoss(unittest.TestCase):
    """the held connection of PR #1365 and the `save_lost` rule of PR #1368 on a REAL MariaDB through the real pymysql
    driver and the repository's own.
    """

    def setUp(self):
        import pymysql
        from lifeos.platform import db as platform_db
        self.pymysql, self.platform_db = pymysql, platform_db
        self.password = Path(os.environ["LIFEOS_TEST_MARIADB_PASSWORD_FILE"]).read_text().strip()
        self.name = f"t_{os.getpid()}_{id(self) % 100000}"
        self.admin = self.connect(None)
        self.admin.cursor().execute(f"CREATE DATABASE {self.name}")
        self.live_ids = []

    def tearDown(self):
        self.admin.cursor().execute(f"DROP DATABASE IF EXISTS {self.name}")
        self.admin.close()

    def connect(self, database):
        return self.pymysql.connect(host="127.0.0.1", port=int(REAL_PORT), user="root", password=self.password, database=database, charset="utf8mb4", autocommit=True,
                                    connect_timeout=5, read_timeout=10, write_timeout=10)

    def store(self):
        @contextmanager
        def connect():
            raw = self.connect(self.name)
            try:
                yield self.platform_db._Connection(raw)
            finally:
                raw.close()
        return connect

    def repo(self, kill_after=None, pause=0.0):
        outer = self

        class Repo(run.SqlRepo):
            def commit(self, o, failures, lane="US Remote"):
                try:
                    super().commit(o, failures, lane)
                finally:
                    outer.live_ids.append((o.source["id"], self._live[1].thread_id() if self._live else None))
                if o.source["id"] == kill_after and self._live:
                    victim = self._live[1].thread_id()
                    outer.admin.cursor().execute(f"KILL {victim}")
                    if pause:
                        time.sleep(pause)
        return Repo()

    def seed(self, skip=False):
        """Three boards already read once and now due (a board never read belongs to the backfill, not to this pass).
        """
        from lifeos.jobs import store
        with self.store()() as connection:
            store.ensure_schema(connection)
            for name in ("s1", "s2", "s3"):
                connection.cursor().execute("INSERT INTO v7_sources (source_id, kind, due_at, last_status, unchanged_runs, etag) VALUES (%s,'greenhouse','2026-09-30 00:00:00','SEED',%s,%s)",
                                            (name, 1 if skip else 0, "e1" if skip else None))

    def run_pass(self, repo, skip=False):
        from unittest import mock
        from lifeos.sources.web import boards as board_pass
        self.seed(skip)
        listing = lister.Listing(lister.UNCHANGED) if skip else GOOD
        seen = []
        real_lost = run.db_lost

        def spy(error):
            seen.append(f"{type(error).__name__}{error.args[:1]}")
            return real_lost(error)
        with mock.patch.object(run.store, "connect", self.store()), mock.patch.object(run, "db_lost", spy):
            counts = board_pass.run(600, True, NOW, fixed({n: listing for n in ("s1", "s2", "s3")}), repo, boards("s1", "s2", "s3"), {})
        return counts, seen

    def rows(self):
        cursor = self.admin.cursor()
        cursor.execute(f"SELECT source_id, last_status FROM {self.name}.v7_sources ORDER BY source_id")
        return dict(cursor.fetchall())

    def test_a_normal_pass_holds_one_real_session_for_every_board_and_writes_the_receipt(self):
        counts, seen = self.run_pass(self.repo())
        self.assertEqual((counts["complete"], counts.get("save_lost", 0), seen), (3, 0, []))
        self.assertEqual(len({session for _, session in self.live_ids}), 1)
        self.assertEqual(set(self.rows().values()), {"COMPLETE"})
        self.assertEqual(counts["timing"]["boards"], 3)
        self.assertIn("receipt", counts)

    def test_a_session_killed_between_boards_fails_only_the_next_board_and_the_pass_goes_on(self):
        counts, seen = self.run_pass(self.repo(kill_after="s1"), skip=True)
        print(f"\nreal-driver evidence: server-side KILL of the held session after board s1; driver error {seen}; sessions used {self.live_ids}")
        self.assertEqual((counts["complete"], counts["failed"], counts["failed_why"], counts["save_lost"]), (2, 1, {"save_lost": 1}, 1))
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].startswith("OperationalError"))
        self.assertEqual(self.rows(), {"s1": "COMPLETE", "s2": "SEED", "s3": "COMPLETE"})
        by = dict(self.live_ids)
        self.assertIsNone(by["s2"])  # the lost session was dropped, never reused
        self.assertNotIn(by["s3"], (None, by["s1"]))  # s3 committed over a different, fresh real session
        self.assertEqual(counts["timing"]["boards"], 3)  # the receipt and its timing are still written
        self.assertIn("receipt", counts)

    def test_a_session_idle_more_than_three_seconds_is_pinged_and_reconnected_without_losing_a_board(self):
        counts, seen = self.run_pass(self.repo(kill_after="s1", pause=run.SqlRepo.HELD_IDLE_SECONDS + 0.7), skip=True)
        self.assertEqual((counts["complete"], counts.get("save_lost", 0), seen), (3, 0, []))
        self.assertEqual(set(self.rows().values()), {"COMPLETE"})


class SaveLimit(unittest.TestCase):
    def test_a_save_that_stalls_fails_that_board_and_is_never_run_beside_the_next_save(self):
        repo = SlowSave("s2")
        try:
            got = run.run(10, True, NOW, fixed({n: GOOD for n in ("s1", "s2", "s3")}), repo, boards("s1", "s2", "s3"), save_seconds=0.2)
        finally:
            repo.release.set()
        self.assertEqual((got["complete"], got["failed"], got["failed_why"], got["save_timeout"], got["save_blocked"]), (1, 2, {"save_timeout": 1, "save_blocked": 1}, 1, 1))
        self.assertEqual([o.source["id"] for o, _ in repo.commits if o.source["id"] != "s2"], ["s1"])

    def test_one_stuck_save_does_not_fail_the_stage_through_the_boards_queued_behind_it(self):
        repo = SlowSave("s1")
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
        return run.run(50, True, NOW, fixed(lookup), MemRepo(), boards(*names)), ""

    def test_one_bad_board_among_many_does_not_fail_the_stage(self):
        got, _ = self.fails(ok=4, bad=1)
        self.assertEqual((got["failed"], got["failed_ids"]), (1, ["b0"]))

    def test_three_failures_that_are_less_than_half_of_the_boards_due_do_not_fail_it(self):
        got, _ = self.fails(ok=7, bad=3)
        self.assertEqual(got["failed"], 3)

    def test_two_failures_of_two_do_not_fail_it_because_three_is_the_minimum(self):
        got, _ = self.fails(ok=0, bad=2)
        self.assertEqual(got["failed"], 2)

    def test_board_failures_never_fail_the_stage_however_many(self):
        """3 flaky outside sites out of 5 or 6 due boards failed the 11 PM and 12 AM CT ticks."""
        for ok, bad, reason in ((3, 3, "http_521"), (2, 3, "bad_shape"), (0, 6, "rate_limited")):
            got, _ = self.fails(ok=ok, bad=bad, reason=reason)
            self.assertEqual((got["failed"], got["failed_why"], got["complete"]), (bad, {reason: bad}, ok))

    def test_the_stage_failure_class_is_gone(self):
        self.assertFalse(hasattr(run, "BoardsFailing"))

    def saves(self, stalled, bad=()):
        """Six boards due"""
        names = [f"s{i}" for i in range(6)]
        lookup = {n: lister.Listing(lister.FAILED, reason="http_521") if n in bad else GOOD for n in names}

        class Stalls(MemRepo):
            def commit(self, o, failures, lane="US Remote"):
                if o.source["id"] in stalled:
                    time.sleep(0.15)
                super().commit(o, failures, lane)
        return run.run(50, True, NOW, fixed(lookup), Stalls(), boards(*names), save_seconds=0.1)

    def test_slow_saves_alone_never_fail_the_stage_but_are_counted_and_named(self):
        got = self.saves({"s0", "s2", "s4"})
        self.assertEqual((got["failed"], got["save_timeout"], got["failed_ids"], got["complete"]), (3, 3, ["s0", "s2", "s4"], 3))

    def test_slow_saves_and_real_board_failures_together_never_fail_the_stage(self):
        self.assertEqual(self.saves({"s0", "s2"}, bad={"s1", "s3"})["failed"], 4)
        self.assertEqual(self.saves({"s0"}, bad={"s1", "s3", "s4"})["failed"], 4)

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
        self.assertEqual(items_after_first, {k: dict(v) for k, v in repo.items_by.items()})
        self.assertEqual((first["failed"], second["failed"], second["added"]), (1, 1, 0))
        self.assertEqual(repo.state["s2"]["failures"], 2)


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
