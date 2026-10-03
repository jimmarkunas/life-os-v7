import unittest
from unittest import mock

from lifeos.jobs.resolve.aggregators import linkedin

ROW = (1, "https://www.linkedin.com/comm/jobs/view/product-manager-at-acme-3912345678?refId=abc&trk=eml", "Acme Limited", "Product Manager", "London")


class PageFallbackTests(unittest.TestCase):
    """D3 rank 4: a definite 'no employer link found' lands on the LinkedIn posting, flagged aggregator; nothing else does."""

    def test_a_definite_no_match_lands_on_the_clean_linkedin_job_url(self):
        got = linkedin.page_fallback(ROW, {"outcome": "no_match_no_board+no_result"})
        self.assertEqual(got, {"outcome": "landed", "via": "linkedin_page", "kind": "aggregator", "url": "https://www.linkedin.com/jobs/view/3912345678"})

    def test_a_clean_url_has_no_tracking_parameters(self):
        got = linkedin.page_fallback(ROW, {"outcome": "no_match_no_title+no_result"})
        self.assertNotIn("?", got["url"])

    def test_deferred_closed_landed_and_rate_limited_results_are_left_alone(self):
        for result in ({"outcome": "deferred"}, {"outcome": "closed"}, {"outcome": "rate_limited"},
                       {"outcome": "landed", "via": "guest", "kind": "ats", "url": "https://job-boards.greenhouse.io/x/jobs/1"}):
            self.assertIs(linkedin.page_fallback(ROW, result), result)

    def test_a_row_without_a_linkedin_job_id_is_left_alone(self):
        row = (2, "https://www.linkedin.com/company/acme", "Acme", "PM", "")
        result = {"outcome": "no_match_no_board+no_result"}
        self.assertIs(linkedin.page_fallback(row, result), result)

    def test_resolve_rows_applies_it_after_the_matchers(self):
        with mock.patch.object(linkedin, "read_guest", return_value={"outcome": "external_hidden"}), \
                mock.patch.object(linkedin.ats_match, "match_many", return_value=["no_board"]), \
                mock.patch.object(linkedin.search_match, "find", return_value=("miss", "no_result")):
            out = linkedin.resolve_rows([ROW], budget={"left": 5})
        self.assertEqual((out[0]["outcome"], out[0]["kind"]), ("landed", "aggregator"))


class RequeueTests(unittest.TestCase):
    """The one-time catch-up: only definite no-match HOLD rows with a real LinkedIn job id, and never twice."""

    def _run(self, rows):
        from tests.kit.db import FakeCursor
        cursor = FakeCursor()
        cursor.fetchall = lambda: rows
        n = linkedin.requeue_held(cursor)
        return n, cursor

    def test_rows_with_a_linkedin_job_id_go_back_to_new_with_a_reason_that_is_not_no_match(self):
        n, cursor = self._run([(1, "https://www.linkedin.com/comm/jobs/view/pm-at-acme-3912345678?x=1"), (2, "https://www.linkedin.com/jobs/view/3999999999")])
        self.assertEqual((n, [w for w, _ in cursor.sql].count("UPDATE v7_jobs")), (2, 2))

    def test_rows_without_one_are_left_on_hold(self):
        n, _ = self._run([(3, "https://www.linkedin.com/company/acme"), (4, "https://example.com/jobs/view/3912345678")])
        self.assertEqual(n, 0)

    def test_the_selection_only_asks_for_no_match_reasons_and_the_update_sets_a_different_one(self):
        from tests.kit.db import FakeCursor
        seen = []
        cursor = FakeCursor(handler=lambda sql, args, cur: seen.append(sql))
        cursor.fetchall = lambda: [(1, "https://www.linkedin.com/jobs/view/3912345678")]
        linkedin.requeue_held(cursor)
        self.assertIn("LIKE 'no_match%", seen[0])
        self.assertIn("'requeued_linkedin'", seen[1])
        self.assertNotIn("no_match", seen[1])


if __name__ == "__main__":
    unittest.main()
