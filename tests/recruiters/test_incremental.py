"""LIFE-610: the Recruiters mail step reads only mail new to the system (fake mailboxes, fake ledger), and the first read after 09:00 CT is the old full scan."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lifeos.platform import mail_seen as seen
from lifeos.recruiters import scan, stage
from tests.recruiters.fakes import FakeRecruiters, ReadOnlyGmail, ReadOnlyOutlook, mail
from tests.recruiters.test_recruiters import NOW, SIGNED, failed_run

ROW = {"every_hours": 3, "full_scan_ct": ["09:00"]}
MORNING = datetime(2026, 10, 8, 14, 30, tzinfo=timezone.utc)        # 9:30 AM CDT
AFTERNOON = datetime(2026, 10, 8, 20, 30, tzinfo=timezone.utc)      # 3:30 PM CDT


class FakeLedger:
    def __init__(self, keys=(), last_read=None, kind=None, last_full=None, fail_read=False, fail_commit=False):
        self.keys, self.state, self.fail_read, self.fail_commit, self.commits = set(keys), (last_read, kind, last_full), fail_read, fail_commit, []

    def read(self):
        if self.fail_read:
            raise seen.LedgerError("MAIL_LEDGER_UNREADABLE")
        return (set(self.keys), *self.state)

    def commit(self, keys, kind, now):
        if self.fail_commit:
            raise seen.LedgerError("MAIL_LEDGER_NOT_SAVED")
        self.commits.append((set(keys), kind, now))


class CountingGmail(ReadOnlyGmail):
    def __init__(self, records):
        super().__init__(records)
        self.fetched = []

    def mail_record(self, message_id):
        self.fetched.append(message_id)
        return super().mail_record(message_id)


def signed(ident, **kw):
    return mail(body=SIGNED, ident=ident, url=f"https://mail.example.com/m/{ident}", **kw)


def go(records, ledger, live=False, notion=None, now=AFTERNOON, environ=None, outlook=None):
    gmail = CountingGmail(records)
    notion = notion or FakeRecruiters()
    with contextlib.redirect_stdout(io.StringIO()):
        counts = stage.run(0, live, environ=environ or {}, gmail=gmail, outlook_accounts=outlook or [], notion=notion, now=now, ledger=ledger)
    return counts, gmail, notion


def after_a_full_scan_at_9(ids=()):
    return FakeLedger({seen.key("gmail", i) for i in ids}, last_read=MORNING, kind=seen.FULL_SCAN, last_full=MORNING)


class PlanTests(unittest.TestCase):
    def test_no_ledger_or_no_recorded_full_scan_means_the_full_scan_and_says_why(self):
        self.assertEqual(scan.plan(AFTERNOON, ROW, None, True), ("read", seen.FULL_SCAN, "no ledger: full scan"))
        self.assertEqual(scan.plan(AFTERNOON, ROW, (None, None, None), True)[:2], ("read", seen.FULL_SCAN))
        self.assertEqual(scan.plan(AFTERNOON, ROW, (MORNING, seen.NEW_MAIL, None), True)[:2], ("read", seen.FULL_SCAN))

    def test_the_first_read_after_nine_am_central_is_the_full_scan_even_inside_the_three_hours(self):
        yesterday = MORNING - timedelta(days=1)
        action, kind, _ = scan.plan(MORNING, ROW, (MORNING - timedelta(hours=1), seen.NEW_MAIL, yesterday), True)
        self.assertEqual((action, kind), ("read", seen.FULL_SCAN))
        self.assertEqual(scan.plan(AFTERNOON, ROW, (MORNING, seen.FULL_SCAN, MORNING), False)[:2], ("read", seen.NEW_MAIL))     # after today's scan: new mail again

    def test_a_scheduled_tick_inside_three_hours_is_skipped_and_a_manual_run_never_is(self):
        state = (AFTERNOON - timedelta(hours=1), seen.NEW_MAIL, MORNING)
        self.assertEqual(scan.plan(AFTERNOON, ROW, state, True)[0], "skip")
        self.assertEqual(scan.plan(AFTERNOON, ROW, state, False)[:2], ("read", seen.NEW_MAIL))
        self.assertEqual(scan.plan(AFTERNOON, ROW, (AFTERNOON - timedelta(hours=2, minutes=50), seen.NEW_MAIL, MORNING), True)[0], "read")      # 15 minutes of slack, as the due table

    def test_the_row_is_in_the_due_table(self):
        self.assertEqual(scan.step_row(), ROW)


class IncrementalTests(unittest.TestCase):
    def test_only_mail_new_to_the_system_is_read_and_the_log_counts_scanned_and_new(self):
        ledger = after_a_full_scan_at_9(["old1", "old2"])
        counts, gmail, notion = go([signed("old1"), signed("old2"), signed("new1", received="2026-10-08T15:00:00+00:00")], ledger, live=True)
        self.assertEqual(gmail.fetched, ["new1"])                                          # a message already recorded is not read again
        self.assertEqual((counts["scan"], counts["scanned"], counts["new"], counts["messages"], counts["ledger"]), ("new mail", 3, 1, 1, "ok"))
        self.assertEqual(counts["created"], 1)

    def test_a_read_message_that_is_new_to_the_system_is_included_unread_status_is_not_used(self):          # the reader never looks at read or unread, only at the id
        ledger = after_a_full_scan_at_9(["old1"])
        counts, gmail, _ = go([signed("old1"), signed("read-but-new")], ledger)
        self.assertEqual(gmail.fetched, ["read-but-new"])

    def test_a_message_moved_from_junk_to_inbox_keeps_its_id_and_is_not_new(self):
        ledger = after_a_full_scan_at_9(["moved"])
        counts, gmail, _ = go([signed("moved", folder="INBOX")], ledger)                   # it was recorded while in Junk; the id is the same
        self.assertEqual((gmail.fetched, counts["new"]), ([], 0))

    def test_a_live_complete_run_records_what_it_read_and_a_dry_run_records_nothing(self):
        ledger = after_a_full_scan_at_9()
        go([signed("n1")], ledger, live=False)
        self.assertEqual(ledger.commits, [])
        go([signed("n1")], ledger, live=True)
        self.assertEqual(ledger.commits[0][:2], ({seen.key("gmail", "n1")}, seen.NEW_MAIL))

    def test_a_failed_write_records_nothing_so_the_message_is_read_again(self):
        ledger = after_a_full_scan_at_9()
        notion = FakeRecruiters(drop_writes=True)
        failed_run(lambda: stage.run(0, True, environ={}, gmail=CountingGmail([signed("n1")]), outlook_accounts=[], notion=notion, now=AFTERNOON, ledger=ledger))
        self.assertEqual(ledger.commits, [])

    def test_a_ledger_that_cannot_be_saved_is_reported_and_the_run_still_succeeds(self):
        ledger = after_a_full_scan_at_9()
        ledger.fail_commit = True
        counts, _, notion = go([signed("n1")], ledger, live=True)
        self.assertEqual((counts["ledger"], counts["created"]), ("not_saved", 1))

    def test_an_unreadable_ledger_runs_the_full_scan_and_says_so(self):
        ledger = FakeLedger(fail_read=True)
        counts, gmail, _ = go([signed("a"), signed("b")], ledger)
        self.assertEqual((counts["scan"], counts["ledger"], gmail.fetched), ("14-day scan", "unavailable", ["a", "b"]))

    def test_no_ledger_configured_behaves_exactly_as_before(self):
        counts, gmail, _ = go([signed("a")], None)
        self.assertEqual((counts["scan"], counts["ledger"], gmail.fetched), ("14-day scan", "off", ["a"]))

    def test_the_ledger_holds_no_id_only_a_hash(self):
        self.assertNotIn("abc123", seen.key("gmail", "abc123"))
        self.assertEqual(len(seen.key("gmail", "abc123")), 64)
        self.assertNotEqual(seen.key("gmail", "x"), seen.key("outlook", "x"))               # the same text in two mailboxes is two messages


class NineAmScanTests(unittest.TestCase):
    RECORDS = lambda self: [signed("m1"), signed("m2", sender_name="Sam Sample", address="sam@example.com", received="2026-10-07T10:00:00+00:00")]          # noqa: E731

    def test_the_nine_am_scan_gives_the_same_rows_as_the_old_full_scan_on_a_replayed_mailbox(self):
        old_notion, new_notion = FakeRecruiters(), FakeRecruiters()
        go(self.RECORDS(), None, live=True, notion=old_notion, now=MORNING)                                     # today's behaviour: no ledger, everything read
        yesterday = MORNING - timedelta(days=1)
        ledger = FakeLedger({seen.key("gmail", "m1"), seen.key("gmail", "m2")}, last_read=MORNING - timedelta(hours=18), kind=seen.NEW_MAIL, last_full=yesterday)
        counts, gmail, _ = go(self.RECORDS(), ledger, live=True, notion=new_notion, now=MORNING)                 # the 9 AM scan, with every message already recorded
        self.assertEqual((counts["scan"], gmail.fetched), ("14-day scan", ["m1", "m2"]))                          # it reads them all again, as the old scan did
        rows = lambda n: sorted(json.dumps(w[1], sort_keys=True) for w in n.writes)                              # noqa: E731
        self.assertEqual(rows(new_notion), rows(old_notion))
        self.assertEqual(ledger.commits[0][1], seen.FULL_SCAN)


class SkipTests(unittest.TestCase):
    def test_a_scheduled_tick_inside_three_hours_reads_no_mail_and_reports_the_last_read(self):
        ledger = FakeLedger({seen.key("gmail", "a")}, last_read=AFTERNOON - timedelta(hours=1), kind=seen.NEW_MAIL, last_full=MORNING)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "out"
            counts, gmail, notion = go([signed("a")], ledger, environ={"GITHUB_EVENT_NAME": "workflow_run", "GITHUB_OUTPUT": str(out)})
            text = out.read_text()
        self.assertEqual((counts["scan"], gmail.calls, notion.queries, notion.writes), ("skipped", [], 0, []))
        self.assertIn("scan_kind=new mail", text)
        self.assertIn("scan_at=2:30 PM CT", text)

    def test_a_read_reports_its_own_kind_and_time(self):
        ledger = after_a_full_scan_at_9()
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "out"
            go([signed("a")], ledger, environ={"GITHUB_OUTPUT": str(out)})
            text = out.read_text()
        self.assertIn("scan_kind=new mail", text)
        self.assertIn("scan_at=3:30 PM CT", text)


class OutlookIncrementalTests(unittest.TestCase):
    def outlook(self, count_new):
        class Graph(ReadOnlyOutlook):
            def __init__(self, inbox):
                super().__init__(inbox=inbox)
                self.gets, self.field_sets = [], []

            def messages(self, folder="inbox", since=None, limit=5000, fields=None, time_field="receivedDateTime", prefer=""):
                self.field_sets.append((folder, fields))
                return super().messages(folder, since, limit, fields, time_field, prefer)

            def get(self, url, params=None, prefer=""):
                self.gets.append(url)
                ident = url.rsplit("/", 1)[1]
                return next(m for m in self.inbox if m["id"] == ident)

        box = [{"id": f"o{i}", "receivedDateTime": "2026-10-07T15:00:00Z", "from": {"emailAddress": {"name": "Lee Outlook", "address": "lee@example.com"}}, "subject": "Opportunity",
                "body": {"content": SIGNED.replace("Pat Example", "Lee Outlook")}, "webLink": f"https://mail.example.com/o/{i}", "internetMessageHeaders": []} for i in range(count_new + 1)]
        return Graph(box)

    def test_only_new_outlook_messages_are_read_one_by_one_after_an_ids_only_listing(self):
        graph = self.outlook(2)
        known = {seen.key("outlook", "work:o0")}
        ledger = FakeLedger(known, last_read=MORNING, kind=seen.FULL_SCAN, last_full=MORNING)
        counts, _, _ = go([], ledger, outlook=[("work", graph)])
        self.assertEqual(graph.gets, ["/me/messages/o1", "/me/messages/o2"])
        self.assertEqual(graph.field_sets[0], ("inbox", "id,receivedDateTime"))
        self.assertEqual((counts["scanned"], counts["new"], counts["messages"]), (3, 2, 2))

    def test_a_flood_of_new_outlook_mail_uses_one_full_listing_instead_of_hundreds_of_reads(self):
        graph = self.outlook(150)
        ledger = FakeLedger(set(), last_read=MORNING, kind=seen.FULL_SCAN, last_full=MORNING)
        counts, _, _ = go([], ledger, outlook=[("work", graph)])
        self.assertEqual(graph.gets, [])
        self.assertEqual(counts["messages"], 151)


class LedgerStorageTests(unittest.TestCase):
    def connection(self, tables=2, rows=()):
        statements = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, args=()):
                statements.append(" ".join(sql.split()))
                self.last = sql

            def executemany(self, sql, args):
                statements.append(("many", " ".join(sql.split()), len(args)))

            def fetchone(self):
                return (tables,)

            def fetchall(self):
                return [(r,) for r in rows] if "key_hash" in self.last else [("full", datetime(2026, 10, 8, 14, 0)), ("incremental", datetime(2026, 10, 8, 18, 0))]

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return Cursor()

        return Connection(), statements

    def test_missing_tables_are_an_empty_ledger_and_reading_creates_nothing(self):
        connection, statements = self.connection(tables=0)
        keys, last_read, kind, last_full = seen.Ledger(lambda: connection).read()
        self.assertEqual((keys, last_read, kind, last_full), (set(), None, None, None))
        self.assertFalse(any("CREATE" in str(s) for s in statements))

    def test_read_returns_the_keys_and_the_last_read_of_either_kind(self):
        connection, _ = self.connection(rows=["k1", "k2"])
        keys, last_read, kind, last_full = seen.Ledger(lambda: connection).read()
        self.assertEqual((keys, kind), ({"k1", "k2"}, seen.NEW_MAIL))
        self.assertEqual((last_read.hour, last_full.hour), (18, 14))

    def test_commit_creates_the_tables_records_the_keys_and_drops_old_rows(self):
        connection, statements = self.connection()
        seen.Ledger(lambda: connection).commit({"k1", "k2"}, seen.FULL_SCAN, NOW)
        text = json.dumps(statements, default=str)
        self.assertIn("CREATE TABLE IF NOT EXISTS v7_mail_seen", text)
        self.assertIn("INSERT IGNORE INTO v7_mail_seen", text)
        self.assertEqual(text.count("INSERT INTO v7_mail_scan"), 2)                           # a 14-day scan is also the latest read
        self.assertIn("DELETE FROM v7_mail_seen", text)

    def test_a_database_failure_is_a_fixed_code(self):
        def boom():
            raise RuntimeError("tunnel down with secret-looking text")
        with self.assertRaises(seen.LedgerError) as caught:
            seen.Ledger(boom).read()
        self.assertEqual(str(caught.exception), "MAIL_LEDGER_UNREADABLE")


if __name__ == "__main__":
    unittest.main()
