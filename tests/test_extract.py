import unittest

from pipeline import extract
from pipeline.parsers import lensa


class FakeGmail:
    def __init__(self, messages):
        self.messages, self.relabeled = messages, []

    def label_id(self, name, create=False):
        return "PROC"

    def list_ids(self, query, limit=5000):
        return list(self.messages)[:limit]

    def message(self, message_id):
        return self.messages[message_id]

    def relabel(self, ids, add=(), remove=()):
        self.relabeled.append((list(ids), list(add)))


class FakeCursor:
    def __init__(self, db):
        self.db, self.rowcount = db, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=()):
        if sql.startswith("INSERT IGNORE INTO v7_jobs"):
            key = args[0]
            self.rowcount = 0 if key in self.db["jobs"] else 1
            self.db["jobs"].add(key)
        else:
            self.rowcount = 1
            self.db["sources"] += 1


class FakeConn:
    def __init__(self):
        self.db = {"jobs": set(), "sources": 0}

    def cursor(self):
        return FakeCursor(self.db)


HTML = ('<a href="https://email.lensa.com/f/a/J1"><table><tr><td>Acme</td></tr><tr><td>Project Manager</td></tr>'
        '<tr><td>Remote</td></tr></table></a>'
        '<a href="https://email.lensa.com/f/a/J2"><table><tr><td>Globex</td></tr><tr><td>Program Lead</td></tr>'
        '<tr><td>Remote</td></tr></table></a>')
import time
NOW = int(time.time())
MESSAGES = {"m1": ("Lensa <jobalert@lensa.com>", HTML, NOW - 3600),
            "m2": ("someone@example.com", "<p>not a newsletter sender</p>", NOW)}


class ExtractTests(unittest.TestCase):
    def test_dry_run_counts_and_writes_nothing(self):
        gmail = FakeGmail(MESSAGES)
        counts = extract.extract(gmail, live=False, limit=10)
        self.assertEqual((counts["messages"], counts["cards"], counts["unsupported_sender"]), (1, 2, 1))
        self.assertEqual(gmail.relabeled, [])

    def test_live_saves_then_marks_only_supported_messages_and_is_repeat_safe(self):
        gmail, conn = FakeGmail(MESSAGES), FakeConn()
        first = extract.extract(gmail, True, 10, conn)
        self.assertEqual((first["new_jobs"], first["marked_processed"]), (2, 1))
        self.assertEqual(gmail.relabeled, [(["m1"], ["PROC"])])
        second = extract.extract(gmail, True, 10, conn)           # same links again: nothing new
        self.assertEqual((second["new_jobs"], second["repeat_links"]), (0, 2))

    def test_no_cards_message_stays_pending_and_stale_jobs_are_excluded(self):
        gmail, conn = FakeGmail({"m3": ("Lensa <jobalert@lensa.com>", "<p>layout we cannot read</p>", NOW)}), FakeConn()
        counts = extract.extract(gmail, True, 10, conn)
        self.assertEqual((counts["no_cards"], counts["marked_processed"]), (1, 0))
        self.assertEqual(gmail.relabeled, [])
        old = lensa.Card("A", "B", None, "Remote", "u", age_days=15)
        self.assertEqual(extract.status_for(lensa.Card("A", "B", None, "R", "u", age_days=5), mail_age=10), "EXCLUDED_STALE")
        self.assertEqual(extract.status_for(lensa.Card("A", "B", None, "R", "u"), mail_age=14), "NEW")
        self.assertEqual(extract.status_for(lensa.Card("A", "B", None, "R", "u"), mail_age=15), "EXCLUDED_STALE")
        fresh = lensa.Card("A", "B", None, "Remote", "u", age_days=14)
        unknown = lensa.Card("A", "B", None, "Remote", "u")
        self.assertEqual([extract.status_for(c) for c in (old, fresh, unknown)], ["EXCLUDED_STALE", "NEW", "NEW"])

    def test_mail_older_than_window_is_skipped_but_accounted(self):
        old = {"m9": ("Lensa <jobalert@lensa.com>", HTML, NOW - 15 * 86400)}
        gmail, conn = FakeGmail(old), FakeConn()
        counts = extract.extract(gmail, True, 10, conn)
        self.assertEqual((counts["stale_mail"], counts["new_jobs"], counts["marked_processed"]), (1, 0, 1))

    def test_fuzzy_key_ignores_case_and_punctuation(self):
        a = lensa.Card("Acme, Inc.", "Project  Manager", None, "Remote", "u1")
        b = lensa.Card("ACME INC", "project manager", None, "remote", "u2")
        self.assertEqual(extract.fuzzy_key(a), extract.fuzzy_key(b))


if __name__ == "__main__":
    unittest.main()
