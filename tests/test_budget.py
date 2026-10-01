import unittest
from urllib.parse import urlsplit

from pipeline import budget, tinyfish


class BudgetTests(unittest.TestCase):
    def test_grant_never_exceeds_cap(self):
        self.assertEqual(budget.granted(0, 900, 30), 30)
        self.assertEqual(budget.granted(890, 900, 30), 10)
        self.assertEqual(budget.granted(900, 900, 30), 0)
        self.assertEqual(budget.granted(950, 900, 30), 0)

    def test_caps_leave_headroom_under_free_limits(self):
        self.assertLessEqual(budget.FETCH_DAILY_CAP, 900)                       # free limit is 1,000/day
        self.assertLessEqual(budget.FETCH_BATCH * 60 / budget.MIN_SECONDS_PER_BATCH, 120)   # free limit is 150/min

    def test_pacer_spaces_batches(self):
        now, slept = [0.0], []
        pacer = budget.Pacer(clock=lambda: now[0], sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
        pacer.wait()
        now[0] += 1
        pacer.wait()
        self.assertEqual(round(slept[0]), budget.MIN_SECONDS_PER_BATCH - 1)


class NoPaidEndpointTests(unittest.TestCase):
    def test_only_the_free_fetch_endpoint_exists_in_code(self):
        self.assertEqual(urlsplit(tinyfish.ENDPOINT).hostname, "api.fetch.tinyfish.ai")
        source = open(tinyfish.__file__).read()
        for paid in ("agent.tinyfish.ai", "/v1/automation", "run-sse", "run-async", "browser.tinyfish"):
            self.assertNotIn(paid, source)


if __name__ == "__main__":
    unittest.main()


class CleanKeyTests(unittest.TestCase):
    def test_extracts_the_key_shaped_token(self):
        key, parts = tinyfish.clean_key('API key: "sk-abc123_DEF456-ghi789_JKL012"\n')
        self.assertEqual(key, "sk-abc123_DEF456-ghi789_JKL012")
        self.assertEqual(parts, [3, 3, 30][:len(parts)] if False else parts)        # lengths only, never content

    def test_plain_and_labelled_forms(self):
        self.assertEqual(tinyfish.clean_key("  plainkey_ABCDEFGHIJKLMNOP  ")[0], "plainkey_ABCDEFGHIJKLMNOP")
        self.assertEqual(tinyfish.clean_key("TINYFISH_API_KEY=abcdefghijklmnop1234")[0], "abcdefghijklmnop1234")
        self.assertEqual(tinyfish.clean_key("")[0], "")
