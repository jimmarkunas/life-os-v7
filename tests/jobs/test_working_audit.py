import unittest
from unittest import mock

from lifeos.jobs import working_audit
from tests.kit.db import FakeConn


class WorkingAudit(unittest.TestCase):
    def test_every_statement_is_a_select_and_the_output_is_counts_only(self):
        conn = FakeConn()
        conn.cur.fetchall = lambda: [("openjobs", "host_word", True, 3, 2)]
        with mock.patch.object(working_audit.store, "connect", lambda: conn):
            out = working_audit.run(0, False)
        self.assertEqual(list(out), ["board_host_labels_by_family_and_pattern", "excluded_fit_with_admit_or_review"])
        self.assertEqual(len(conn.cur.sql), 2)
        self.assertEqual({words.split()[0] for words, _ in conn.cur.sql}, {"SELECT"})
        for sql in working_audit.QUERIES.values():
            self.assertTrue(sql.startswith("SELECT"))
            self.assertNotRegex(sql.upper(), r"\b(INSERT|UPDATE|DELETE|REPLACE|ALTER|DROP|CREATE|TRUNCATE)\b")
            self.assertNotRegex(sql.lower(), r"\b(title|source_url|final_apply_url|location_text|full_text|dedupe_key|notion_page_id\s*,)\b")      # never selects text that identifies a job
            self.assertNotRegex(sql.split(" FROM ")[0].lower(), r"j\.company\s*(,|\s+from)")                    # the company text is only ever matched or counted, never returned
        self.assertIn("COUNT(DISTINCT j.company)", working_audit.QUERIES["board_host_labels_by_family_and_pattern"])
        self.assertEqual(out["board_host_labels_by_family_and_pattern"], [["openjobs", "host_word", 1, 3, 2]])


if __name__ == "__main__":
    unittest.main()
