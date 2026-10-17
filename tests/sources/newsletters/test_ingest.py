import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from lifeos.sources.newsletters import config, ingest
from lifeos.sources.newsletters.parsers import dice, lensa
from tests.kit.db import FakeConn
from tests.kit.gmail import FakeGmail
from tests.sources.newsletters.parsers.test_dice_reed import DICE, DICE_DAY2


def store_double():
    """In-memory v7_jobs / v7_job_sources: a URL key is new once; no reposts in these fixtures."""
    db = {"jobs": set(), "sources": 0, "source_hashes": []}

    def handler(sql, args, cursor):
        if sql.startswith("INSERT IGNORE INTO v7_jobs"):
            cursor.rowcount = 0 if args[0] in db["jobs"] else 1
            db["jobs"].add(args[0])
        elif sql.startswith("INSERT IGNORE INTO v7_job_sources"):
            cursor.rowcount = 1
            db["sources"] += 1
            db["source_hashes"].append(args[1])
        elif sql.startswith(("SELECT", "UPDATE v7_jobs")):
            cursor.rowcount = 0
            cursor.row = None
        else:
            cursor.rowcount = 1
    return FakeConn(handler=handler), db


HTML = ('<a href="https://email.lensa.com/f/a/J1"><table><tr><td>Acme</td></tr><tr><td>Project Manager</td></tr>'
        '<tr><td>Remote</td></tr></table></a>'
        '<a href="https://email.lensa.com/f/a/J2"><table><tr><td>Globex</td></tr><tr><td>Program Lead</td></tr>'
        '<tr><td>Remote</td></tr></table></a>')
NOW = int(time.time())
MESSAGES = {"m1": ("Lensa <jobalert@lensa.com>", HTML, NOW - 3600),
            "m2": ("someone@example.com", "<p>not a newsletter sender</p>", NOW)}


class ExtractTests(unittest.TestCase):
    """The extract mechanics (parse, save, mark Processed, stale and no-card handling), driven by the Lensa card layout as a fixture. Lensa itself is retired (D201),
    so these tests re-enable its parser for themselves; RetiredSenderTests below is the production behaviour for real Lensa mail."""
    def setUp(self):
        for patch in (mock.patch.dict(config.RETIRED_SENDERS, clear=True), mock.patch.dict(ingest.PARSERS, {"lensa": lensa.parse}),
                      mock.patch.dict(ingest.LOOKS_LIKE_JOBS, {"lensa": lensa.looks_like_jobs})):
            patch.start()
            self.addCleanup(patch.stop)

    def test_dice_tracking_urls_dedupe_by_company_and_title_across_alerts(self):
        conn, db = store_double()
        first, second = dice.parse(DICE)[0], dice.parse(DICE_DAY2)[0]
        first_counts = ingest.save_cards(conn, "dice", "m1", [first], NOW)
        second_counts = ingest.save_cards(conn, "dice", "m2", [second], NOW)
        self.assertEqual((first_counts, second_counts), ((1, 0), (0, 1)))
        self.assertEqual((len(db["jobs"]), db["sources"]), (1, 2))
        self.assertNotEqual(db["source_hashes"][0], db["source_hashes"][1])

    def test_dry_run_counts_and_writes_nothing(self):
        gmail = FakeGmail(messages=MESSAGES)
        counts = ingest.extract(gmail, live=False, limit=10)
        self.assertEqual((counts["messages"], counts["cards"], counts["unsupported_sender"]), (1, 2, 1))
        self.assertEqual(gmail.calls, [])

    def test_live_saves_then_marks_only_supported_messages_and_is_repeat_safe(self):
        gmail, conn = FakeGmail(messages=MESSAGES), store_double()[0]
        first = ingest.extract(gmail, True, 10, conn)
        self.assertEqual((first["new_jobs"], first["marked_processed"]), (2, 1))
        self.assertEqual(gmail.calls, [(["m1"], ["LBL"], [])])
        second = ingest.extract(gmail, True, 10, conn)           # same links again: nothing new
        self.assertEqual((second["new_jobs"], second["repeat_links"]), (0, 2))

    def test_no_cards_message_stays_pending_and_stale_jobs_are_excluded(self):
        gmail, conn = FakeGmail(messages={"m3": ("Lensa <jobalert@lensa.com>", "<p>$100K / yr. layout we cannot read</p>", NOW)}), store_double()[0]
        counts = ingest.extract(gmail, True, 10, conn)
        self.assertEqual((counts["no_cards"], counts["marked_processed"]), (1, 0))
        self.assertEqual(gmail.calls, [])
        old = lensa.Card("A", "B", None, "Remote", "u", age_days=15)
        self.assertEqual(ingest.status_for(lensa.Card("A", "B", None, "R", "u", age_days=5), mail_age=10), "EXCLUDED_STALE")
        self.assertEqual(ingest.status_for(lensa.Card("A", "B", None, "R", "u"), mail_age=14), "NEW")
        self.assertEqual(ingest.status_for(lensa.Card("A", "B", None, "R", "u"), mail_age=15), "EXCLUDED_STALE")
        fresh = lensa.Card("A", "B", None, "Remote", "u", age_days=14)
        unknown = lensa.Card("A", "B", None, "Remote", "u")
        self.assertEqual([ingest.status_for(c) for c in (old, fresh, unknown)], ["EXCLUDED_STALE", "NEW", "NEW"])

    def test_mail_older_than_window_is_skipped_but_accounted(self):
        old = {"m9": ("Lensa <jobalert@lensa.com>", HTML, NOW - 15 * 86400)}
        gmail, conn = FakeGmail(messages=old), store_double()[0]
        counts = ingest.extract(gmail, True, 10, conn)
        self.assertEqual((counts["stale_mail"], counts["new_jobs"], counts["marked_processed"]), (1, 0, 1))


class RetiredSenderTests(unittest.TestCase):
    """D201: Lensa mail still leaves the inbox, but it is closed out at once as retired_sender_lensa: no card is parsed, no job is written, and it never holds a pending slot."""
    def test_a_dry_run_counts_lensa_mail_as_retired_and_writes_nothing(self):
        gmail = FakeGmail(messages=MESSAGES)
        counts = ingest.extract(gmail, live=False, limit=10)
        self.assertEqual((counts["retired_sender_lensa"], counts["messages"], counts["cards"], counts["unsupported_sender"]), (1, 0, 0, 1))
        self.assertEqual(gmail.calls, [])

    def test_live_marks_lensa_mail_processed_without_reading_a_card_or_writing_a_job(self):
        gmail, (conn, db) = FakeGmail(messages=MESSAGES), store_double()
        counts = ingest.extract(gmail, True, 10, conn)
        self.assertEqual((counts["retired_sender_lensa"], counts["new_jobs"], counts["marked_processed"], len(db["jobs"])), (1, 0, 1, 0))

    def test_lensa_is_no_longer_reconciled(self):
        self.assertNotIn("lensa", config.RECONCILE_QUERY)
        self.assertNotIn("lensa", ingest.PARSERS)


class SourceAccountingTests(unittest.TestCase):
    """LIFE-64: every parsed card leaves a source row for ITS message, over the real store and its UNIQUE (source_url_hash)."""
    def world(self, **mails):
        from tests.jobs.test_newsletter_golden_path import Mailbox, alert
        from tests.kit.jobsdb import JobsDB
        box = Mailbox({i: ("Jobright <alerts@jobright.ai>", alert(*ids), time.time() - 60) for i, ids in mails.items()})
        for labels in box.labels.values():
            labels.add("j-newsletters")
        return box, JobsDB()

    def rows(self, db, message_id):
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) FROM v7_job_sources WHERE gmail_message_id=%s", (message_id,))
        return cursor.fetchone()[0]

    def test_a_vacancy_repeated_in_a_later_mail_is_accounted_for_that_mail_and_holds_it_open(self):
        from lifeos.sources.newsletters import finalize
        box, db = self.world(m1=("v1", "v2"), m2=("v2", "v3"))
        counts = ingest.extract(box, True, 50, db)
        self.assertEqual((self.rows(db, "m1"), self.rows(db, "m2")), (2, 2))
        self.assertEqual((counts["new_jobs"], counts["repeat_links"], counts["suppressed"]), (3, 1, 0))
        cursor = db.cursor()
        cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT' WHERE source_url NOT LIKE %s", ("%/v2",))
        self.assertEqual(finalize.blockers(db, ["m1", "m2"]), {"m1": "in_flight", "m2": "in_flight"})   # v2 is unfinished in BOTH mails

    def test_a_mail_whose_cards_were_all_seen_before_still_has_its_rows_and_a_replay_adds_none(self):
        box, db = self.world(m1=("v1", "v2"), m2=("v1", "v2"))
        ingest.extract(box, True, 50, db)
        self.assertEqual((self.rows(db, "m1"), self.rows(db, "m2")), (2, 2))
        ingest.save_cards(db, "jobright", "m2", ingest.PARSERS["jobright"](box.messages["m2"][1]), time.time())
        ingest.save_cards(db, "jobright", "m1", ingest.PARSERS["jobright"](box.messages["m1"][1]), time.time())
        self.assertEqual((self.rows(db, "m1"), self.rows(db, "m2")), (2, 2))

    def test_a_card_a_tombstone_suppressed_is_a_job_less_row_counted_apart_from_repeats(self):
        from datetime import timedelta
        from lifeos.jobs import identity, tombstone
        from lifeos.sources.newsletters import finalize
        box, db = self.world(m1=("v1", "v2"))
        tombstone.write(db.cursor(), identity.url_hash("https://jobright.ai/jobs/info/v2"), "f" * 64, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
        counts = ingest.extract(box, True, 50, db)
        self.assertEqual((counts["new_jobs"], counts["repeat_links"], counts["suppressed"], self.rows(db, "m1")), (1, 0, 1, 2))
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) FROM v7_job_sources WHERE job_id IS NULL")
        self.assertEqual(cursor.fetchone()[0], 1)
        cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT'")
        self.assertEqual(finalize.blockers(db, ["m1"]), {})                    # suppressed on purpose: accounted, not in flight

    def partial_world(self):
        """Mail saved by the old code: m2's repeat of v2 has no row of its own. Both are Processed; both are Done only if the test says so."""
        box, db = self.world(m1=("v1", "v2"), m2=("v2", "v3"))
        parse = ingest.PARSERS["jobright"]
        ingest.save_cards(db, "jobright", "m1", parse(box.messages["m1"][1]), time.time())
        ingest.save_cards(db, "jobright", "m2", parse(box.messages["m2"][1])[1:], time.time())      # only v3: what the old code left (v2 collided with m1)
        for labels in box.labels.values():
            labels.add("j-newsletters-processed")
        return box, db

    def test_reconcile_recovers_the_missing_row_of_a_partly_recorded_jobright_mail(self):
        box, db = self.partial_world()
        self.assertEqual((self.rows(db, "m1"), self.rows(db, "m2")), (2, 1))
        counts = ingest.reconcile(box, lambda: db, 50)
        self.assertEqual((counts["checked"], counts["recovered_messages"], counts["recovered_jobs"]), (2, 1, 0))
        self.assertEqual((self.rows(db, "m1"), self.rows(db, "m2")), (2, 2))
        self.assertEqual(ingest.reconcile(box, lambda: db, 50)["recovered_messages"], 0)                    # idempotent

    def test_reconcile_reopens_done_mail_with_a_vacancy_unfinished_and_leaves_finished_mail_done(self):
        box, db = self.partial_world()
        for labels in box.labels.values():
            labels.add("j-newsletters-done")
        cursor = db.cursor()
        cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT' WHERE source_url NOT LIKE %s", ("%/v2",))      # v2 stays NEW: in flight in both mails
        counts = ingest.reconcile(box, lambda: db, 50)
        self.assertEqual(counts["reopened"], 2)
        self.assertTrue(all("j-newsletters-done" not in box.labels[i] for i in ("m1", "m2")))
        cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT'")
        for labels in box.labels.values():
            labels.add("j-newsletters-done")
        self.assertEqual(ingest.reconcile(box, lambda: db, 50)["reopened"], 0)                              # everything finished: stays Done
        self.assertTrue(all("j-newsletters-done" in box.labels[i] for i in ("m1", "m2")))

    def test_reconcile_holds_no_connection_while_it_reads_mail(self):
        """The first live run lost its idle connection: reading the window's mail takes over a minute, so the store is opened only to read the row counts and to write."""
        box, db = self.partial_world()
        for labels in box.labels.values():
            labels.add("j-newsletters-done")
        open_now, read_while_open = [0], []

        class Tracked:
            def __enter__(self):
                open_now[0] += 1
                return db

            def __exit__(self, *exc):
                open_now[0] -= 1
                return False
        real = box.message
        box.message = lambda message_id: (read_while_open.append(open_now[0]), real(message_id))[1]
        cursor = db.cursor()
        cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT' WHERE source_url NOT LIKE %s", ("%/v2",))
        counts = ingest.reconcile(box, Tracked, 50)
        self.assertEqual((counts["checked"], counts["recovered_messages"], counts["reopened"]), (2, 1, 2))
        self.assertEqual(read_while_open, [0, 0])                                        # both messages were read with no connection open
        self.assertEqual(open_now[0], 0)


if __name__ == "__main__":
    unittest.main()
