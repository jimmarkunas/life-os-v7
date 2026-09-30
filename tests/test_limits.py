import unittest

from pipeline import budget, limits, tinyfish_search


class LimitsTests(unittest.TestCase):
    def test_we_stay_under_every_provider_limit(self):
        self.assertLess(limits.TINYFISH_FETCH_DAILY_CAP, limits.TINYFISH_FETCH_PER_DAY)
        self.assertLessEqual(60 / limits.TINYFISH_SEARCH_GAP_SECONDS, limits.TINYFISH_SEARCH_PER_MINUTE)
        self.assertLess(limits.TINYFISH_SEARCH_PER_RUN, limits.TINYFISH_SEARCH_PER_HOUR)
        self.assertLessEqual(1 / limits.NOTION_GAP_SECONDS, limits.NOTION_REQUESTS_PER_SECOND)
        self.assertFalse(limits.TINYFISH_AGENT_ALLOWED)

    def test_code_uses_the_canonical_numbers(self):
        self.assertEqual(budget.FETCH_DAILY_CAP, limits.TINYFISH_FETCH_DAILY_CAP)
        self.assertEqual(budget.FETCH_BATCH, limits.TINYFISH_FETCH_BATCH)
        self.assertEqual(budget.BROWSER_USD_PER_MINUTE, limits.TINYFISH_BROWSER_USD_PER_MINUTE)
        self.assertEqual(tinyfish_search.MIN_GAP_SECONDS, limits.TINYFISH_SEARCH_GAP_SECONDS)


if __name__ == "__main__":
    unittest.main()
