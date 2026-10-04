import unittest
from unittest import mock

from lifeos.jobs import review

ROWS = [("Technical Program Manager", "London", "PUBLISHED", "web:su-revolut-ltd", 82, "ADMIT", "", None, None, None),
        ("API Enterprise Sales Executive", "Madrid", "EXCLUDED_FIT", "web:su-revolut-ltd", 79, "EXCLUDE", "excluded: sales_role", None, None, None),
        ("Senior Product Manager", "London", "EXCLUDED_FIT", "web:su-revolut-ltd", 55, "EXCLUDE", "Fit 55 below 60", None, None, None),
        ("Director of Delivery", None, "NEW", "web:su-revolut-ltd", None, None, None, "http_403", "https://www.revolut.com/", None)]


class Cur:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        self.sql.append((sql, args))

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, rows):
        self.cur = Cur(rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cur


class ReviewTests(unittest.TestCase):
    def go(self, live, rows=ROWS, env=None):
        conn = Conn(rows)
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
        sql, args = conn.cur.sql[-1]
        self.assertNotIn("DROP", sql)
        self.assertIn("%x'; DROP%", args)


if __name__ == "__main__":
    unittest.main()


class SeveralCompaniesTests(unittest.TestCase):
    def test_comma_separated_companies_become_one_or_filter_with_bound_parameters(self):
        conn = Conn(ROWS)
        with mock.patch.object(review.store, "connect", return_value=conn), mock.patch.object(review.store, "ensure_schema"):
            review.run(0, False, environ={"REVIEW_COMPANY": "wheely, hyperexponential ,ab,plentific"})
        sql, params = next((s, p) for s, p in conn.cur.sql if "FROM v7_jobs" in s) if conn.cur.sql and isinstance(conn.cur.sql[0], tuple) else (None, None)
        self.assertEqual(params[:-1], ("%wheely%", "%wheely%", "%hyperexponential%", "%hyperexponential%", "%plentific%", "%plentific%"))
        self.assertEqual(sql.count("j.company LIKE"), 3)
        sql % tuple("x" for _ in params)                               # survives driver formatting
