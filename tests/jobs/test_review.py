import unittest
from unittest import mock

from lifeos.jobs import review
from tests.kit.db import RecorderConn

ROWS = [("Technical Program Manager", "London", "PUBLISHED", "web:su-revolut-ltd", 82, "ADMIT", "", None, None, None),
        ("API Enterprise Sales Executive", "Madrid", "EXCLUDED_FIT", "web:su-revolut-ltd", 79, "EXCLUDE", "excluded: sales_role", None, None, None),
        ("Senior Product Manager", "London", "EXCLUDED_FIT", "web:su-revolut-ltd", 55, "EXCLUDE", "Fit 55 below 60", None, None, None),
        ("Director of Delivery", None, "NEW", "web:su-revolut-ltd", None, None, None, "http_403", "https://www.revolut.com/", None)]


class ReviewTests(unittest.TestCase):
    def go(self, live, rows=ROWS, env=None):
        conn = RecorderConn(rows)
        client = mock.MagicMock()
        with mock.patch.object(review.store, "connect", return_value=conn), mock.patch.object(review.store, "ensure_schema"), \
                mock.patch.object(review, "Client", return_value=client), mock.patch.object(review.ledger, "verify", return_value="ok"):
            return review.run(0, live, environ=env or {"REVIEW_COMPANY": "Revolut"}), conn, client

    def test_groups_every_job_by_what_became_of_it_with_the_reason(self):
        counts, _, _ = self.go(False)
        self.assertEqual(counts["matched"], 4)
        self.assertEqual(counts["groups"], {"On the board": 1, "Excluded by Fit or the lane policy: excluded: sales_role": 1,
                                            "Excluded by Fit or the lane policy: Fit N below N": 1, "Not finished: NEW (http_N)": 1})

    def test_a_dry_run_writes_nothing_and_a_live_run_creates_one_page_with_titles_and_reasons(self):
        counts, _, client = self.go(False)
        client.create.assert_not_called()
        counts, _, client = self.go(True)
        self.assertTrue(counts["written"])
        props, blocks = client.create.call_args[0]
        text = " ".join(b[b["type"]]["rich_text"][0]["text"]["content"] for b in blocks)
        self.assertIn("Senior Product Manager | London | Fit 55 | Fit 55 below 60", text)
        self.assertIn("Fit Review - Revolut", props["Job"]["title"][0]["text"]["content"])

    def test_the_log_carries_counts_only_and_a_short_or_missing_input_is_refused(self):
        counts, _, _ = self.go(True)
        self.assertNotIn("Senior Product Manager", repr(counts))
        self.assertEqual(self.go(True, env={"REVIEW_COMPANY": "ab"})[0], {"error": "REVIEW_COMPANY_missing_or_short"})

    def test_the_company_filter_is_a_bound_parameter_never_part_of_the_sql(self):
        _, conn, _ = self.go(False, env={"REVIEW_COMPANY": "x'; DROP"})
        sql, args = conn.cur.statements[-1]
        self.assertNotIn("DROP", sql)
        self.assertIn("%x'; DROP%", args)


if __name__ == "__main__":
    unittest.main()


class SeveralCompaniesTests(unittest.TestCase):
    def test_the_optional_company_cap_defaults_to_twelve_and_can_be_lowered(self):
        for env, want in (({"REVIEW_COMPANY": "aaa,bbb,ccc"}, 3), ({"REVIEW_COMPANY": "aaa,bbb,ccc", "REVIEW_MAX_COMPANIES": "2"}, 2)):
            conn = RecorderConn(ROWS)
            with mock.patch.object(review.store, "connect", return_value=conn), mock.patch.object(review.store, "ensure_schema"):
                review.run(0, False, environ=env)
            sql = next(s for s, p in conn.cur.statements if "FROM v7_jobs" in s)
            self.assertEqual(sql.count("j.company LIKE"), want)

    def test_comma_separated_companies_become_one_or_filter_with_bound_parameters(self):
        conn = RecorderConn(ROWS)
        with mock.patch.object(review.store, "connect", return_value=conn), mock.patch.object(review.store, "ensure_schema"):
            review.run(0, False, environ={"REVIEW_COMPANY": "wheely, hyperexponential ,ab,plentific"})
        sql, params = next((s, p) for s, p in conn.cur.statements if "FROM v7_jobs" in s) if conn.cur.statements and isinstance(conn.cur.statements[0], tuple) else (None, None)
        self.assertEqual(params[:-1], ("%wheely%", "%wheely%", "%hyperexponential%", "%hyperexponential%", "%plentific%", "%plentific%"))
        self.assertEqual(sql.count("j.company LIKE"), 3)
        sql % tuple("x" for _ in params)                               # survives driver formatting
