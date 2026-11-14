import pathlib
import re
import unittest

from lifeos.platform import usage, limits, tinyfish_search


class LimitsTests(unittest.TestCase):
    def test_we_stay_under_every_provider_limit(self):
        self.assertLess(limits.TINYFISH_FETCH_DAILY_CAP, limits.TINYFISH_FETCH_PER_DAY)
        self.assertLessEqual(limits.TINYFISH_SEARCH_LANES * 60 / limits.TINYFISH_SEARCH_GAP_SECONDS,
                             limits.TINYFISH_SEARCH_PER_MINUTE)       # all lanes together, one shared key
        from lifeos.sources.web import discover_jobs, titlewatch      # noqa: PLC0415
        self.assertLess(limits.TINYFISH_SEARCH_PER_RUN_LINKEDIN + discover_jobs.RESOLVE_SEARCHES + titlewatch.MAX_QUERIES_PER_RUN,
                        limits.TINYFISH_SEARCH_PER_HOUR)                # every per-run search budget on the shared key, in one tick (Lensa retired, D201)
        self.assertLessEqual(1 / limits.NOTION_GAP_SECONDS, limits.NOTION_REQUESTS_PER_SECOND)
        self.assertFalse(limits.TINYFISH_AGENT_ALLOWED)

    def test_code_uses_the_canonical_numbers(self):
        self.assertEqual(usage.FETCH_DAILY_CAP, limits.TINYFISH_FETCH_DAILY_CAP)
        self.assertEqual(usage.FETCH_BATCH, limits.TINYFISH_FETCH_BATCH)
        self.assertEqual(tinyfish_search.MIN_GAP_SECONDS, limits.TINYFISH_SEARCH_GAP_SECONDS)

    def test_the_notion_numbers_match_the_documentation_file_and_each_other(self):
        """LIFE-682: the numbers in limits.py are the ones recorded from Notion's documentation (docs/platform/notion-rate-limits.md)."""
        root = pathlib.Path(__file__).resolve().parents[2]
        block = (root / "docs/platform/notion-rate-limits.md").read_text().split("```")[1]
        documented = {k: int(v) for k, v in re.findall(r"^(NOTION_[A-Z_]+) = (\d+)$", block, re.M)}
        self.assertGreaterEqual(len(documented), 7)
        for name, value in documented.items():
            self.assertEqual(getattr(limits, name), value, name)
        self.assertEqual(limits.NOTION_BUDGET_PER_MINUTE, limits.NOTION_REQUESTS_PER_SECOND * 60)
        self.assertLessEqual(60 / limits.NOTION_GAP_MAX_SECONDS, limits.NOTION_BUDGET_PER_MINUTE)
        self.assertLessEqual(limits.NOTION_RETRY_AFTER_CAP_SECONDS * (1 + 0.5), limits.NOTION_RATE_WAIT_BUDGET_SECONDS)     # one advised wait always fits the budget

    def test_no_module_calls_the_notion_api_except_the_shared_client(self):
        """LIFE-682: one path to Notion, so the one rate rule applies to every call."""
        root = pathlib.Path(__file__).resolve().parents[2] / "lifeos"
        callers = sorted(str(p.relative_to(root.parent)) for p in root.rglob("*.py") if "api.notion.com" in p.read_text())
        self.assertEqual(callers, ["lifeos/platform/notion_client.py"])


if __name__ == "__main__":
    unittest.main()
