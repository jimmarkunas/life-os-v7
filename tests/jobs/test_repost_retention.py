from datetime import datetime, timedelta
import unittest

from lifeos.jobs import retention, repost

NOW = datetime(2026, 10, 1, 12, 0)


class FakeCursor:
    def __init__(self, found):
        self.found, self.sql = found, []

    def execute(self, sql, args=()):
        self.sql.append((sql.split()[0], args))

    def fetchone(self):
        return self.found


class RepostTests(unittest.TestCase):
    def test_repost_becomes_duplicate_of_the_original(self):
        cursor = FakeCursor((7,))
        self.assertEqual(repost.link(cursor, "k", "f", NOW), 7)
        kinds = [k for k, _ in cursor.sql]
        self.assertEqual(kinds, ["SELECT", "UPDATE", "UPDATE"])

    def test_no_match_is_a_new_job(self):
        cursor = FakeCursor(None)
        self.assertIsNone(repost.link(cursor, "k", "f", NOW))
        self.assertEqual(len(cursor.sql), 1)

    def test_ghost_is_evidence_only(self):
        young = NOW - timedelta(days=10)
        self.assertTrue(repost.is_ghost(3, young, NOW.date(), NOW))          # 3 sightings in the window
        self.assertFalse(repost.is_ghost(2, young, NOW.date(), NOW))
        self.assertTrue(repost.is_ghost(1, NOW - timedelta(days=30), None, NOW))   # undated for weeks
        self.assertFalse(repost.is_ghost(1, NOW - timedelta(days=30), NOW.date(), NOW))

    def test_sync_props_note_only_for_ghosts(self):
        self.assertNotIn("Review Reason", repost.sync_props(NOW, False))
        self.assertIn("Review Reason", repost.sync_props(NOW, True))


class PurgeTests(unittest.TestCase):
    def test_actions_by_age(self):
        self.assertIsNone(retention.action(NOW - timedelta(days=5), None, NOW))
        self.assertEqual(retention.action(NOW - timedelta(days=20), "Today", NOW), "expire")
        self.assertIsNone(retention.action(NOW - timedelta(days=20), "Expired", NOW))
        self.assertEqual(retention.action(NOW - timedelta(days=91), "Expired", NOW), "trash")

    def test_query_never_selects_acted_on_rows(self):
        props = [f["property"] for f in retention.query_filter(NOW, "Newsletter")["and"]]
        for needed in ("Applied", "Saturn Decision", "Lifecycle", "Visible Lane", "Created At"):
            self.assertIn(needed, props)


if __name__ == "__main__":
    unittest.main()
