import unittest
from datetime import datetime
from unittest import mock

from lifeos.jobs import hold
from tests.kit.db import FakeConn

NOW = datetime(2026, 10, 10, 12, 0)


def conn(due=3, no_reason=1, holding=9, updated=2):
    seen = []

    def handler(sql, args, cursor):
        seen.append((sql, args))
        if sql.startswith("SELECT COUNT(*), COALESCE"):
            cursor.row = (due, no_reason)
        elif sql.startswith("SELECT COUNT(*) FROM v7_jobs WHERE status='HOLD'"):
            cursor.row = (holding,)
        elif sql.startswith("UPDATE"):
            cursor.rowcount = updated

    return FakeConn(handler=handler), seen


class HoldExpiryTests(unittest.TestCase):
    def go(self, live, **kw):
        c, seen = conn(**kw)
        with mock.patch.object(hold.store, "connect", return_value=c), mock.patch.object(hold.store, "ensure_schema"):
            return hold.run(0, live, now=NOW), seen

    def test_dry_run_counts_what_is_due_and_writes_nothing(self):
        out, seen = self.go(False)
        self.assertEqual(out, {"on_hold": 9, "due": 3, "excluded": 0, "saved": False})
        self.assertFalse([s for s, _ in seen if s.startswith("UPDATE")])

    def test_live_excludes_only_hold_jobs_older_than_seven_days_as_a_terminal_status(self):
        out, seen = self.go(True)
        self.assertEqual((out["due"], out["excluded"]), (3, 2))
        sql, args = [(s, a) for s, a in seen if s.startswith("UPDATE")][0]
        self.assertIn("WHERE status='HOLD' AND updated_at < %s", sql)
        self.assertEqual(args[0], "EXCLUDED_UNRESOLVED")
        self.assertEqual(args[3], datetime(2026, 10, 3, 12, 0))                   # exactly seven days before now
        self.assertTrue(args[0].startswith("EXCLUDED_"))                         # the finalize stage treats EXCLUDED_* as an outcome

    def test_nothing_due_means_no_write_even_when_live(self):
        out, seen = self.go(True, due=0, no_reason=0)
        self.assertEqual(out["excluded"], 0)
        self.assertFalse([s for s, _ in seen if s.startswith("UPDATE")])

    def test_the_stage_is_registered_and_the_status_fits_the_column(self):
        from lifeos import run
        self.assertIn("expire-holds", run.STAGES)
        self.assertLessEqual(len(hold.EXCLUDED), 24)                              # v7_jobs.status is VARCHAR(24)


if __name__ == "__main__":
    unittest.main()
