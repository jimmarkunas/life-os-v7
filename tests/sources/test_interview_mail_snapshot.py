"""Interview OS reads its mail early (a read-only step beside the tick) and the late Interview job uses that snapshot
only when it is current and.
"""
import contextlib
import io
import json
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from lifeos.platform.db import StoreError
from lifeos.sources.interview import interview_evidence as ev
from lifeos.sources.interview import mail_snapshot as snap
from tests.sources.support import mail, outreach
from tests.sources.test_interview_evidence import APPLIED, ENV, Run

UTC = timezone.utc
TICK = datetime(2026, 11, 22, 12, 7, tzinfo=UTC)  # the tick started at 12:07 UTC
TAKEN = TICK + timedelta(minutes=4)  # its early read began four minutes later
NOW = TICK + timedelta(minutes=30)  # the late run
RECORDS = [mail(subject="Schedule your interview - ExampleCo Program Manager", received="2026-11-20T15:00:00Z"),
           outreach(url="https://example.test/thread/lead", received="2026-11-21T09:00:00Z", extra="Requisition 777777."),
           {**mail(subject="Newsletter"), "provider": "outlook", "id": "acct:1", "received": "2026-11-22T11:00:00Z", "folder": "JUNK"}]
STARTED = TICK.strftime("%Y-%m-%dT%H:%M:%SZ")


class Frozen(datetime):
    """ev.run reads the clock once"""
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW


DETAIL = {"gmail": {"listed": 3, "list_s": 1.0, "read": 3, "read_s": 2.0, "retries": 0, "gone": 0, "unreadable": {}}}


class Db:
    """The private store's one table in memory: it is its own connection and cursor, and records every statement."""

    def __init__(self, down=False):
        self.rows, self.sql, self.down, self.committed = {}, [], down, 0

    def __call__(self):
        if self.down:
            raise StoreError("STORE_DOWN")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def commit(self):
        self.committed += 1

    def execute(self, sql, args=()):
        self.sql.append(sql)
        self.found = []
        if sql.startswith("REPLACE"):
            hour, taken, days, complete, fingerprint, payload = args
            self.rows[hour] = (hour, taken, days, complete, fingerprint, payload)
        elif sql.startswith("DELETE"):
            self.rows = {k: v for k, v in self.rows.items() if v[1] >= args[0]}
        elif sql.startswith("SELECT"):
            self.found = sorted(self.rows.values(), key=lambda r: r[1], reverse=True)[:1]

    def fetchone(self):
        return self.found[0] if self.found else None


def saved(db, records=RECORDS, taken=TAKEN, days=14, complete=True, down=0, detail=DETAIL):
    snap.save(db, taken, days, complete, records, down, detail, {"gmail": 2.0, "outlook_1": 0.1})
    return db


def read_of(records=RECORDS, down=0, detail=DETAIL):
    def read(environ, now, gmail, outlook, days, seconds, got):
        seconds.update({"gmail": 2.0, "outlook_1": 0.1})
        got.update(detail)
        return list(records), down
    return read


class Judge(unittest.TestCase):
    """The pure decision: a snapshot is used only when it is current, complete and for the same window."""

    def row(self, **kw):
        return {"tick_hour": snap.hour_key(TAKEN), "taken_at": TAKEN, "days": 14, "complete": True, **kw}

    def test_a_complete_snapshot_taken_after_this_tick_started_is_used(self):
        self.assertEqual(snap.judge(self.row(), TICK, NOW, 14), "used")

    def test_no_row_a_stale_row_a_wrong_hour_row_an_incomplete_row_or_another_window_is_never_used(self):
        for kw, why in (({"taken_at": TICK - timedelta(hours=1), "tick_hour": snap.hour_key(TICK - timedelta(hours=1))}, "stale"),  # the previous tick's read
                        ({"taken_at": TICK - timedelta(minutes=3), "tick_hour": snap.hour_key(TICK - timedelta(minutes=3))}, "stale"),  # just outside the grace
                        ({"tick_hour": snap.hour_key(TAKEN - timedelta(hours=1))}, "wrong_hour"),  # a row filed under another hour
                        ({"complete": False}, "incomplete"),
                        ({"days": 30}, "days"),
                        ({"taken_at": NOW + timedelta(minutes=5), "tick_hour": snap.hour_key(NOW + timedelta(minutes=5))}, "future")):
            self.assertEqual(snap.judge(self.row(**kw), TICK, NOW, 14), why, kw)
        self.assertEqual(snap.judge(None, TICK, NOW, 14), "none")

    def test_the_grace_matches_the_phase_rule_two_minutes_before_the_tick_started_still_belongs_to_it(self):
        at = TICK - timedelta(minutes=2)
        self.assertEqual(snap.judge(self.row(taken_at=at, tick_hour=snap.hour_key(at)), TICK, NOW, 14), "used")


class Handoff(unittest.TestCase):
    def test_what_is_saved_comes_back_equal_and_the_fingerprint_does_not_depend_on_order(self):
        got = snap.load(saved(Db()), TICK, NOW, 14)
        self.assertEqual((got["why"], got["records"], got["down"], got["detail"]), ("used", RECORDS, 0, DETAIL))
        self.assertEqual(snap.fingerprint(RECORDS), snap.fingerprint(list(reversed(RECORDS))))
        self.assertNotEqual(snap.fingerprint(RECORDS), snap.fingerprint(RECORDS[:2]))

    def test_a_corrupt_payload_or_one_whose_fingerprint_differs_is_never_used(self):
        db = saved(Db())
        row = next(iter(db.rows.values()))
        db.rows[row[0]] = (*row[:5], b"not a payload")
        self.assertEqual(snap.load(db, TICK, NOW, 14)["why"], "corrupt")
        other = json.dumps({"records": RECORDS[:1], "down": 0, "detail": DETAIL, "seconds": {}}).encode()
        db.rows[row[0]] = (*row[:5], zlib.compress(other))
        self.assertEqual(snap.load(db, TICK, NOW, 14)["why"], "corrupt")

    def test_a_store_that_is_down_or_a_table_that_is_missing_means_no_snapshot_never_an_error(self):
        self.assertEqual(snap.load(Db(down=True), TICK, NOW, 14)["why"], "store")
        self.assertEqual(snap.load(Db(), TICK, NOW, 14)["why"], "none")

    def test_an_oversized_snapshot_is_filed_incomplete_and_never_used(self):
        with patch.object(snap, "MAX_BYTES", 10):
            db = saved(Db())
        self.assertEqual(snap.load(db, TICK, NOW, 14)["why"], "incomplete")

    def test_old_rows_are_pruned_when_a_new_one_is_saved(self):
        db = Db()
        saved(db, taken=TAKEN - timedelta(hours=10))
        saved(db, taken=TAKEN)
        self.assertEqual(len(db.rows), 1)


class EarlyStep(unittest.TestCase):
    """The read-only early step: it files one snapshot and writes nothing else, to Notion, the Ledger or anywhere."""

    def go(self, db, live=True, read=None, environ=ENV):
        with contextlib.redirect_stdout(io.StringIO()):
            return snap.stage(20, live, environ=environ, now=TAKEN, connect=db, read=read or read_of())

    def test_a_complete_read_is_saved_for_the_tick_and_counts_only_are_returned(self):
        db = Db()
        counts = self.go(db)
        self.assertEqual((counts["snapshot"], counts["records"], counts["complete"]), ("saved", 3, True))
        self.assertEqual(snap.load(db, TICK, NOW, 14)["records"], RECORDS)
        self.assertNotIn("example", json.dumps(counts))  # counts and fixed codes: no address, subject or id

    def test_a_source_read_in_part_is_filed_incomplete_so_the_late_run_reads_for_itself(self):
        db = Db()
        self.assertEqual(self.go(db, read=read_of(down=1))["snapshot"], "saved_incomplete")
        self.assertEqual(snap.load(db, TICK, NOW, 14)["why"], "incomplete")

    def test_a_failed_read_changes_nothing_and_does_not_raise(self):
        db = Db()

        def broken(*args):
            raise RuntimeError("boom with a subject line")
        counts = self.go(db, read=broken)
        self.assertEqual((counts["snapshot"], db.rows, db.sql), ("not_saved", {}, []))
        self.assertNotIn("subject", json.dumps(counts))  # a fixed code, never the error's text

    def test_a_store_that_is_down_is_a_fixed_code_not_a_failure_of_the_run(self):
        self.assertEqual(self.go(Db(down=True))["snapshot"], "not_saved")

    def test_a_dry_run_reads_and_saves_nothing(self):
        db = Db()
        self.assertEqual((self.go(db, live=False)["snapshot"], db.rows, db.sql), ("dry_run", {}, []))

    def test_it_writes_nothing_to_notion_or_the_ledger_and_touches_only_its_own_table(self):
        db = Db()
        with patch.object(ev.notion, "make_client", side_effect=AssertionError("Notion")), \
             patch.object(ev.stage, "run", side_effect=AssertionError("writer")), \
             patch.object(ev.pipeline, "read_applied", side_effect=AssertionError("Ledger")), \
             patch.object(ev, "lead_row", side_effect=AssertionError("Ledger row")), \
             patch.object(ev, "lead_job", side_effect=AssertionError("Jobs store")), \
             patch.object(ev.streaks, "save", side_effect=AssertionError("streaks")):
            self.go(db)
        self.assertTrue(db.sql)
        self.assertTrue(all("v7_interview_mail" in sql for sql in db.sql), db.sql)  # nothing but its own table


class LateRun(unittest.TestCase):
    """ev.run with and without the snapshot: the same input gives the same output."""

    def fresh_tick(self):
        """TICK_STARTED and the time of the early read: the tick began at 12:07, its early read four minutes later, and
        ev.run's clock says 12:37.
        """
        return STARTED, TAKEN

    def run_late(self, db, started, read=None, records=None):
        environ = {**ENV, "TICK_STARTED": started}
        with patch.object(ev, "datetime", Frozen), patch.object(ev, "read_mail", side_effect=read or (lambda *a, **k: (list(RECORDS), 0))) as spy:
            counts, got = Run(parents=(), kids={}, roster=[APPLIED], records=records, environ=environ, connect=db)()
        return counts, got, spy

    @staticmethod
    def output(counts):
        """The counts line with the timing and measurement fields removed."""
        return {k: v for k, v in counts.items() if k not in ("seconds", "mail_seconds", "mail_gmail", "mail_snapshot")}

    def test_a_current_complete_snapshot_is_used_and_gmail_is_not_read_again(self):
        started, taken = self.fresh_tick()
        counts, got, spy = self.run_late(saved(Db(), taken=taken), started)
        self.assertEqual(spy.call_count, 0)
        self.assertEqual(counts["mail_snapshot"], "used")
        self.assertEqual(counts["mail_gmail"], DETAIL["gmail"])  # the early read's own Gmail counts

    def test_the_output_with_the_snapshot_equals_the_output_of_the_unchanged_late_read(self):
        started, taken = self.fresh_tick()
        today_counts, today, _ = self.run_late(Db(), "")  # no TICK_STARTED: exactly today's read
        used_counts, used, spy = self.run_late(saved(Db(), taken=taken), started)
        self.assertEqual(spy.call_count, 0)
        self.assertEqual(self.output(used_counts), self.output(today_counts))
        self.assertEqual(used["evidence"], today["evidence"])
        self.assertTrue(today["evidence"])  # a real request came through, not an empty equality

    def test_stale_wrong_hour_incomplete_other_window_or_missing_falls_back_to_the_full_read_with_the_same_output(self):
        started, taken = self.fresh_tick()
        today_counts, today, _ = self.run_late(Db(), "")
        old = taken - timedelta(hours=2)
        cases = {"stale": saved(Db(), taken=old), "incomplete": saved(Db(), taken=taken, complete=False), "days": saved(Db(), taken=taken, days=30),
                 "none": Db(), "store": Db(down=True)}
        for why, db in cases.items():
            counts, got, spy = self.run_late(db, started)
            self.assertEqual((spy.call_count, counts["mail_snapshot"]), (1, why), why)  # the full read ran, and the reason is a fixed code
            self.assertEqual(self.output(counts), self.output(today_counts), why)
            self.assertEqual(got["evidence"], today["evidence"], why)

    def test_a_run_without_a_tick_start_never_looks_for_a_snapshot(self):
        db = saved(Db())
        counts, got, spy = self.run_late(db, "")
        self.assertEqual(spy.call_count, 1)
        self.assertNotIn("mail_snapshot", counts)
        self.assertEqual([s for s in db.sql if s.startswith("SELECT")], [])  # the store was not even asked

    def test_mail_that_was_handed_in_is_used_as_before_and_the_snapshot_is_not_consulted(self):
        db = saved(Db())
        started, _ = self.fresh_tick()
        counts, got, spy = self.run_late(db, started, records=list(RECORDS))
        self.assertEqual(spy.call_count, 0)
        self.assertNotIn("mail_snapshot", counts)

    def test_a_dry_run_with_a_longer_window_reads_for_itself(self):
        started, taken = self.fresh_tick()
        environ = {**ENV, "TICK_STARTED": started, "INTERVIEW_MAIL_DAYS": "30"}
        with patch.object(ev, "datetime", Frozen), patch.object(ev, "read_mail", return_value=(list(RECORDS), 0)) as spy:
            counts, _ = Run(parents=(), kids={}, roster=[APPLIED], records=None, environ=environ, live=False, connect=saved(Db(), taken=taken))()
        self.assertEqual((spy.call_count, counts["mail_snapshot"]), (1, "days"))

    def test_the_snapshots_unreadable_messages_feed_the_same_counts_as_a_read_that_found_them(self):
        started, taken = self.fresh_tick()
        detail = {"gmail": {**DETAIL["gmail"], "unreadable": {"m-1": "GMAIL_MESSAGE_INCOMPLETE"}}}
        db = saved(Db(), taken=taken, detail=detail)
        counts, _, _ = self.run_late(db, started)
        read_counts, _, _ = self.run_late(Db(), "", read=lambda environ, now, gmail, outlook, days, seconds, got: (got.update(detail), (list(RECORDS), 0))[1])
        self.assertEqual(counts["mail_causes"], read_counts["mail_causes"])


class Wiring(unittest.TestCase):
    def test_the_early_stage_is_registered_and_not_required(self):
        from lifeos import run
        from lifeos.platform import budget
        self.assertEqual(run.STAGES["interview-mail"].target, ("lifeos.sources.interview.mail_snapshot", "stage"))
        self.assertNotIn("interview-mail-shadow", run.STAGES)
        self.assertFalse(budget.budget_for("interview-mail")[1])  # a failed early read never fails the run
        self.assertTrue(budget.budget_for("interview")[1])  # the late writer is unchanged


if __name__ == "__main__":
    unittest.main()
