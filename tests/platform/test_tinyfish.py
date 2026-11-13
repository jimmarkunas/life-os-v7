import unittest
from urllib.parse import urlsplit

from lifeos.platform import tinyfish


class NoPaidEndpointTests(unittest.TestCase):
    def test_only_the_free_fetch_endpoint_exists_in_code(self):
        self.assertEqual(urlsplit(tinyfish.ENDPOINT).hostname, "api.fetch.tinyfish.ai")
        source = open(tinyfish.__file__).read()
        for paid in ("agent.tinyfish.ai", "/v1/automation", "run-sse", "run-async", "browser.tinyfish"):
            self.assertNotIn(paid, source)


class CleanKeyTests(unittest.TestCase):
    def test_extracts_the_key_shaped_token(self):
        key, parts = tinyfish.clean_key('API key: "sk-abc123_DEF456-ghi789_JKL012"\n')
        self.assertEqual(key, "sk-abc123_DEF456-ghi789_JKL012")
        self.assertTrue(all(isinstance(n, int) for n in parts))        # lengths only, never content

    def test_plain_and_labelled_forms(self):
        self.assertEqual(tinyfish.clean_key("  plainkey_ABCDEFGHIJKLMNOP  ")[0], "plainkey_ABCDEFGHIJKLMNOP")
        self.assertEqual(tinyfish.clean_key("TINYFISH_API_KEY=abcdefghijklmnop1234")[0], "abcdefghijklmnop1234")
        self.assertEqual(tinyfish.clean_key("")[0], "")

class KeyConfigTests(unittest.TestCase):
    def test_missing_key_is_a_fixed_code(self):
        import os
        from lifeos.platform import tinyfish
        os.environ.pop("TINYFISH_API_KEY", None)
        with self.assertRaises(tinyfish.TinyFishError) as ctx:
            tinyfish.fetch_many(["https://example.com"])
        self.assertEqual(str(ctx.exception), "TINYFISH_KEY_MISSING")


if __name__ == "__main__":
    unittest.main()
