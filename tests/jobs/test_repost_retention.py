from datetime import datetime, timedelta
import unittest

from lifeos.jobs import progression, retention, repost, tombstone
from tests.kit.db import FakeCursor

NOW = datetime(2026, 10, 1, 12, 0)


class RepostTests(unittest.TestCase):
    def test_repost_becomes_duplicate_of_the_original(self):
        cursor = FakeCursor(rowcount=0, script={"SELECT": (7,)})
        self.assertEqual(repost.link(cursor, "k", "f", NOW), 7)
        kinds = cursor.verbs()
        self.assertEqual(kinds, ["SELECT", "UPDATE", "UPDATE"])

    def test_no_match_is_a_new_job(self):
        cursor = FakeCursor(rowcount=0)
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
    TODAY = NOW.date()

    def test_retire_only_stale_unapplied_unprotected(self):
        d = lambda n: self.TODAY - timedelta(days=n)
        self.assertIsNone(retention.action(d(29), None, False, None, self.TODAY))              # inside 30 days
        self.assertEqual(retention.action(d(30), None, False, None, self.TODAY), "retire")
        self.assertEqual(retention.action(None, d(45), False, None, self.TODAY), "retire")     # First Surfaced when no Posting Date
        self.assertEqual(retention.action(d(60), d(2), False, None, self.TODAY), "retire")     # the employer date wins over First Surfaced
        self.assertIsNone(retention.action(d(2), d(60), False, None, self.TODAY))
        self.assertIsNone(retention.action(None, None, False, None, self.TODAY))               # no date: never retire on a guess

    def test_applied_and_decided_jobs_are_never_retired(self):
        old = self.TODAY - timedelta(days=200)
        self.assertIsNone(retention.action(old, None, True, None, self.TODAY))                 # Applied: UNKNOWN until INT-7.1A
        self.assertIsNone(retention.action(old, None, False, "Pass", self.TODAY))              # a Saturn Decision protects it
        self.assertEqual(progression.resolve(True), progression.UNKNOWN)
        self.assertEqual(progression.resolve(True, interview_evidence=False), progression.NOT_PROTECTED)
        self.assertEqual(progression.resolve(True, interview_evidence=True), progression.PROTECTED)
        self.assertEqual(progression.resolve(False), progression.NOT_PROTECTED)

    def test_query_never_selects_acted_on_rows_and_uses_the_employer_clock(self):
        flt = retention.query_filter(NOW, "US Remote")["and"]
        props = [f.get("property") for f in flt]
        for needed in ("Applied", "Saturn Decision", "Visible Lane"):
            self.assertIn(needed, props)
        text = str(flt)
        self.assertIn("Posting Date", text)
        self.assertIn("First Surfaced", text)
        self.assertNotIn("Created At", text)                                                    # never Notion's Created At
        self.assertNotIn("Lifecycle", text)                                                     # the legacy field is not read at all
        self.assertNotIn("Expired", text)                                                       # no legacy Lifecycle writes or triggers


class TombstoneTests(unittest.TestCase):
    def test_active_tombstone_blocks_and_a_newer_posting_reenters(self):
        retired = NOW - timedelta(days=10)
        cursor = FakeCursor(script={"SELECT": (retired,)})
        self.assertTrue(tombstone.blocked(cursor, "k", "f", None, NOW))
        self.assertTrue(tombstone.blocked(FakeCursor(script={"SELECT": (retired,)}), "k", "f", retired.date() - timedelta(days=3), NOW))
        again = FakeCursor(script={"SELECT": (retired,)})
        self.assertFalse(tombstone.blocked(again, "k", "f", NOW.date(), NOW))                    # posted after the retirement: genuinely new
        self.assertIn("DELETE", again.verbs())
        self.assertFalse(tombstone.blocked(FakeCursor(), "k", "f", None, NOW))                   # no tombstone

    def test_write_sets_a_90_day_expiry(self):
        cursor = FakeCursor()
        tombstone.write(cursor, "k", "f", NOW)
        self.assertEqual(cursor.sql[0][1][3], NOW + timedelta(days=90))

    def test_intake_skips_a_tombstoned_vacancy(self):
        from lifeos.jobs import intake
        cursor = FakeCursor(script={"SELECT": (NOW - timedelta(days=5),)})
        job = {"url": "https://x.example/j/1", "status": "NEW", "title": "T", "company": "C", "location": "L", "salary": None,
               "source": "s", "provider": "p", "lane": "US Remote", "age_days": None, "received": NOW, "provider_score": None}
        key, is_new = intake.add_job(cursor, job, NOW)
        self.assertFalse(is_new)
        self.assertNotIn("INSERT", cursor.verbs())


if __name__ == "__main__":
    unittest.main()
