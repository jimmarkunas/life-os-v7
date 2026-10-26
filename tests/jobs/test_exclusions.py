import unittest

from lifeos.jobs import exclusions as ex


class Cursor:
    def __init__(self, rows):
        self.rows, self.sql, self.last = rows, [], None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, args=()):
        self.sql.append(sql.split()[0])
        self.last = sql

    def fetchall(self):
        return [("EXCLUDED_FIT", 3)] if "GROUP BY" in self.last else self.rows


class Exclusions(unittest.TestCase):
    def test_a_stored_score_falls_in_exactly_one_band_and_a_missing_one_is_unscored(self):
        got = {s: ex.band(s) for s in (None, 90, 72, 71, 68, 67, 60, 59, 0)}
        self.assertEqual(got, {None: "unscored", 90: "72_plus", 72: "72_plus", 71: "68_to_71", 68: "68_to_71", 67: "60_to_67", 60: "60_to_67", 59: "under_60", 0: "under_60"})

    def test_a_stored_reason_is_only_ever_a_fixed_word_never_text_from_a_posting(self):
        self.assertEqual([ex.word(t) for t in ("Fit 66 below 72", "lane_exclude", None, "", "123", "Salary £30,000 at ExampleCo!", "x" * 80)],
                         ["fit below", "lane_exclude", "none", "none", "none", "other", "other"])

    def test_the_report_splits_by_reason_band_and_source_with_counts_only(self):
        rows = [("web:su-a", "lane_exclude", 80, "EXCLUDE", "explicit pay below £40,000", None), ("web:su-a", "fit_exclude", 70, "EXCLUDE", "Fit 70 below 72", "title"),
                ("web:su-b", "fit_exclude", 40, None, None, None), ("web:su-b", "fit_exclude", None, None, None, None)]
        report = ex.tally(rows)
        self.assertEqual((report["total"], report["near_floor"]), (4, 1))
        self.assertEqual(report["by_reason"], {"lane_exclude": 1, "fit_exclude": 3})
        self.assertEqual(report["by_band"], {"72_plus": 1, "68_to_71": 1, "under_60": 1, "unscored": 1})
        self.assertEqual(report["by_band_and_reason"]["72_plus/lane_exclude"], 1)
        self.assertEqual(report["by_source"], {"web:su-a": 2, "web:su-b": 2})
        self.assertEqual(report["by_admission"]["exclude/pay_below_floor"], 1)                    # lane reasons are printed as lanes.public_reason's fixed codes, never as text
        self.assertEqual(report["high_score"], {"lane_exclude/pay_below_floor/none": 1})            # only the job scored at or over 72 is listed here

    def test_a_lane_reason_never_reaches_the_report_as_text_and_the_high_scorers_are_listed_by_rule(self):
        rows = [("web:su-a", "lane_exclude", 90, "EXCLUDE", "excluded: finance_risk_domain", "finance_risk_domain"), ("web:su-a", "lane_exclude", 85, "EXCLUDE", "market us is not uk", None),
                ("web:su-a", "excluded_fit", 80, "EXCLUDE", "Fit 55 below 72 at ExampleCo", None)]
        report = ex.tally(rows)
        self.assertEqual(report["high_score"], {"lane_exclude/excluded_private_rule/finance_risk_domain": 1, "lane_exclude/market_mismatch/none": 1, "excluded_fit/other/none": 1})
        self.assertNotIn("ExampleCo", str(report))

    def test_run_only_selects_and_adds_the_floors_and_status_totals(self):
        cursor = Cursor([("web:su-a", "fit_exclude", 70, "EXCLUDE", "x", None)])
        report = ex.run(0, False, connect=lambda: cursor)
        self.assertEqual(set(cursor.sql), {"SELECT"})
        self.assertEqual((report["floor"], report["near_miss_floor"], report["review_floor"], report["status_totals"]), (72, 68, 60, {"EXCLUDED_FIT": 3}))


if __name__ == "__main__":
    unittest.main()


class Review(unittest.TestCase):
    ROWS = [("ExampleCo", "Head of Sales", "London", "https://example.com/j/1", 80, "excluded: sales_role", "sales_role", "lane_exclude"),
            ("ExampleCo", "Programme Manager", "London", "https://example.com/j/2", 90, "market us is not uk", None, "lane_exclude"),
            ("OtherCo", "Risk Lead", "Leeds", "https://example.com/j/3", 75, "excluded: finance_risk_domain", "finance_risk_domain", "excluded_fit"),
            ("OtherCo", "Analyst", "Leeds", "https://example.com/j/4", 60, "excluded: finance_risk_domain", "finance_risk_domain", "lane_exclude")]

    def test_only_high_scoring_jobs_removed_by_the_private_rules_are_listed_with_their_rule(self):
        lines = ex.review_rows(self.ROWS)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].split("\t")[:2], ["OtherCo", "Risk Lead"])
        self.assertIn("\tsales_role\t", lines[1])

    def test_the_text_goes_to_the_private_page_and_the_log_gets_counts_only(self):
        from tests.jobs.test_private_report import FakeClient
        cursor, client = Cursor(self.ROWS), FakeClient()
        self.assertEqual(ex.review(0, False, connect=lambda: cursor, client=client), {"rows": 2, "page": "dry", "failed": 0, "why": {}})
        self.assertEqual((set(cursor.sql), client.writes), ({"SELECT"}, []))                       # a dry run reads only
        report = ex.review(0, True, connect=lambda: Cursor(self.ROWS), client=client)
        self.assertEqual((report["rows"], report["page"]), (2, "new"))
        self.assertIn("ExampleCo", str(client.writes[0][2]))                                       # the plaintext only ever went to Notion
