"""a quiet board is re-read on the quiet schedule (24 h base, backing off to 72 h) even when its answer is a 304 or an
identical listing
"""
from datetime import datetime, timedelta
import unittest
from unittest import mock

from lifeos.platform import limits
from lifeos.sources.web import lister, run
from tests.sources.web.test_conditional import BOARD, GH, NOW, Provider

ENGINEER = {"jobs": [{"id": 9, "title": "Staff Software Engineer", "location": {"name": "Remote - US"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/9",
                      "first_published": "2026-10-18", "content": "x"}]}
SOURCE = {**BOARD, "tier": "employer", "status": "ready", "enabled": True, "refresh_hours": limits.BOARDS_REFRESH_HOURS, "hot_hours": limits.BOARDS_HOT_HOURS, "quiet": True}


class Quiet(unittest.TestCase):
    def setUp(self):
        from tests.jobs.fakes_jobsdb import JobsDB
        self.db = JobsDB()
        self.addCleanup(self.db.raw.close)

    def sim(self, provider, reads, repo=None):
        """Read the board each time it falls due (the clock carries over between calls)."""
        from lifeos.sources.web import boards
        now, out = getattr(self, "now", NOW), []
        first_read = now == NOW
        for i in range(reads):
            with mock.patch.object(run.store, "connect", return_value=self.db), mock.patch.object(run.store, "ensure_schema"):
                first = (lambda *a: boards.backfill(*a, environ={"BOARDS_BACKFILL": "on"})) if first_read and i == 0 else boards.run
                counts = first(40, True, now, lambda s: lister.list_source(s, provider), repo or run.SqlRepo(), [SOURCE])
            due, failures = self.db.raw.execute("SELECT due_at, failures FROM v7_sources WHERE source_id='gh-acme'").fetchone()
            due = due if isinstance(due, datetime) else datetime.fromisoformat(str(due))
            path = "304" if counts["not_modified"] else "same_frontier" if counts["same_frontier"] else "failed" if counts["failed"] else "full"
            out.append((path, round((due - now).total_seconds() / 3600, 1), failures))
            now = due
        self.now = now
        return out

    def test_a_quiet_board_answering_304_reaches_72_hours_not_18(self):
        gaps = [h for path, h, _ in self.sim(Provider(body=ENGINEER), 14)]
        self.assertEqual(gaps[-6:], [72.0] * 6)
        self.assertTrue(all(h >= 24 for h in gaps[1:]), gaps)  # never back to the 6 h hot base

    def test_the_same_holds_for_a_host_without_validators_the_identical_listing_path(self):
        out = self.sim(Provider(body=ENGINEER, honours=False), 14)
        self.assertIn("same_frontier", [p for p, _, _ in out])
        self.assertEqual([h for _, h, _ in out][-6:], [72.0] * 6)

    def test_a_hot_board_keeps_its_6_hour_base_and_18_hour_cap(self):
        out = self.sim(Provider(body=GH), 14)
        gaps = [h for _, h, _ in out]
        self.assertIn("304", [p for p, _, _ in out])
        self.assertTrue(all(h <= 18.0 for h in gaps), gaps)
        self.assertEqual(gaps[2], 12.0)  # the first skip: 6 h x 2

    def test_the_hot_or_quiet_question_is_one_batched_query_per_run_and_reads_no_item_for_a_skip(self):
        repo = run.SqlRepo()
        batches, reads = [], []
        original_hot, original_items = repo.hot_ids, repo.items
        repo.hot_ids = lambda ids: batches.append(list(ids)) or original_hot(ids)
        repo.items = lambda sid: reads.append(sid) or original_items(sid)
        self.sim(Provider(body=ENGINEER), 4, repo)
        self.assertTrue(all(len(b) <= 1 for b in batches))
        self.assertEqual(len(batches), 4)  # one batch per run, however many boards
        self.assertEqual(len(reads), 1)  # only the first full read loaded items

    def test_a_repo_without_the_batch_method_still_decides_by_the_same_rule(self):
        class Plain:
            def __init__(self, inner):
                self.inner = inner
            states = property(lambda self: self.inner.states)
            items = property(lambda self: self.inner.items)
            commit = property(lambda self: self.inner.commit)
        quiet = [h for _, h, _ in self.sim(Provider(body=ENGINEER), 10, Plain(run.SqlRepo()))]
        self.assertEqual(quiet[-4:], [72.0] * 4)


class Dead(unittest.TestCase):
    def setUp(self):
        from tests.jobs.fakes_jobsdb import JobsDB
        self.db = JobsDB()
        self.addCleanup(self.db.raw.close)
        self.sim = Quiet.sim.__get__(self)

    def test_the_fifth_not_found_in_a_row_goes_weekly_and_earlier_ones_keep_the_ladder(self):
        out = self.sim(Provider(body=ENGINEER, status=404), 9)
        self.assertEqual([h for _, h, _ in out[:4]], [1.0, 2.0, 4.0, 8.0])  # the ladder is untouched until the fifth
        self.assertEqual([h for _, h, _ in out[4:]], [limits.BOARDS_DEAD_RETRY_DAYS * 24.0] * 5)  # the fifth and every later not_found: weekly
        self.assertEqual(out[4][2], 5)

    def test_a_failure_that_is_not_not_found_never_goes_weekly(self):
        out = self.sim(Provider(body=ENGINEER, status=500), 9)
        self.assertTrue(all(h <= 24.0 for _, h, _ in out), out)

    def test_a_revived_board_resets_to_the_normal_schedule_and_loses_the_failure_count(self):
        provider = Provider(body=ENGINEER, status=404)
        self.sim(provider, 6)
        provider.status = 200
        out = self.sim(provider, 1)  # a successful read after the weekly wait
        self.assertEqual(out[0][2], 0)
        self.assertLessEqual(out[0][1], 36.0)  # back on the 24 h quiet base

    def test_a_failed_read_never_removes_what_the_board_held(self):
        provider = Provider(body=GH)
        self.sim(provider, 1)
        before = self.db.raw.execute("SELECT COUNT(*) FROM v7_source_items WHERE source_id='gh-acme' AND state='CURRENT'").fetchone()[0]
        self.assertGreater(before, 0)
        provider.status = 404
        self.sim(provider, 6)
        after = self.db.raw.execute("SELECT COUNT(*) FROM v7_source_items WHERE source_id='gh-acme' AND state='CURRENT'").fetchone()[0]
        self.assertEqual((before, self.db.raw.execute("SELECT COUNT(*) FROM v7_source_items WHERE source_id='gh-acme' AND state='REMOVED'").fetchone()[0]), (after, 0))


if __name__ == "__main__":
    unittest.main()
