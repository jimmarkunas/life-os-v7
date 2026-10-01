import unittest

from lifeos.jobs.resolve import stage
from tests.kit.db import FakeConn

DUP = {"SELECT id FROM v7_jobs WHERE final_apply_url": (99,)}


class ApplyResultTests(unittest.TestCase):
    def test_landed_is_resolved(self):
        conn = FakeConn()
        ok = {"outcome": "landed", "url": "https://job-boards.greenhouse.io/x/jobs/1", "kind": "ats"}
        self.assertEqual(stage.apply_result(conn, 1, ok), "resolved")
        self.assertTrue(any("status='RESOLVED'" in a[0] or a[0] == "UPDATE v7_jobs" for a in conn.cur.sql))

    def test_same_final_link_is_duplicate(self):
        ok = {"outcome": "landed", "url": "https://careers.acme.example/j/1", "kind": "employer"}
        self.assertEqual(stage.apply_result(FakeConn(DUP), 1, ok), "duplicate")

    def test_failure_stays_pending_with_reason(self):
        conn = FakeConn()
        self.assertEqual(stage.apply_result(conn, 1, {"outcome": "apply_unavailable"}), "pending")
        self.assertEqual(conn.cur.sql[-1][1][0], "apply_unavailable")

    def test_landed_without_url_is_not_resolved(self):
        self.assertEqual(stage.apply_result(FakeConn(), 1, {"outcome": "landed", "url": None, "kind": "internal"}),
                         "pending")


if __name__ == "__main__":
    unittest.main()
