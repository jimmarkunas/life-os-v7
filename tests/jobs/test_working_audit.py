import unittest
from unittest import mock

from lifeos.jobs import working_audit
from tests.kit.db import FakeConn


class WorkingAudit(unittest.TestCase):
    def test_every_statement_is_a_select_and_the_output_is_counts_only(self):
        conn = FakeConn()
        conn.cur.fetchall = lambda: [("US Remote", "remote", "ADMIT", "PUBLISHED", True, 3)]
        with mock.patch.object(working_audit.store, "connect", lambda: conn):
            out = working_audit.run(0, False)
        self.assertEqual(list(out), ["work_mode_by_disposition", "reasons", "board_host_company_labels"])
        self.assertEqual(len(conn.cur.sql), 3)
        self.assertEqual({words.split()[0] for words, _ in conn.cur.sql}, {"SELECT"})
        for sql in working_audit.QUERIES.values():
            self.assertTrue(sql.startswith("SELECT"))
            self.assertNotRegex(sql.upper(), r"\b(INSERT|UPDATE|DELETE|REPLACE|ALTER|DROP|CREATE|TRUNCATE)\b")
            self.assertNotRegex(sql.lower(), r"\b(title|source_url|final_apply_url|location_text|full_text)\b")      # never selects text that identifies a job
        self.assertEqual(out["work_mode_by_disposition"], [["US Remote", "remote", "ADMIT", "PUBLISHED", 1, 3]])


if __name__ == "__main__":
    unittest.main()
