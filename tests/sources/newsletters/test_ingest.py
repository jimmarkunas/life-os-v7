import unittest

from lifeos.sources.newsletters import ingest
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
import time
NOW = int(time.time())
MESSAGES = {"m1": ("Lensa <jobalert@lensa.com>", HTML, NOW - 3600),
            "m2": ("someone@example.com", "<p>not a newsletter sender</p>", NOW)}


class ExtractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
