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


if __name__ == "__main__":
    unittest.main()
