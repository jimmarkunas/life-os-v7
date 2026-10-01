import unittest

from lifeos.platform import limits, notion_client


class ClientTests(unittest.TestCase):
    def test_missing_config_is_a_fixed_code(self):
        with self.assertRaises(notion_client.NotionError):
            notion_client.Client({})


class RateTests(unittest.TestCase):
    def test_calls_are_paced_under_three_per_second(self):
        sleeps, now = [], [0.0]
        client = notion_client.Client({"NOTION_API_TOKEN": "t", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "s"},
                               clock=lambda: now[0], sleep=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s)))
        client._last = now[0]
        import urllib.request
        from unittest import mock
        with mock.patch.object(urllib.request, "urlopen", side_effect=OSError):
            for _ in range(2):
                with self.assertRaises(notion_client.NotionError):
                    client.call("GET", "/x")
        self.assertTrue(all(s >= 0 for s in sleeps) and sleeps and sleeps[0] >= limits.NOTION_GAP_SECONDS - 0.001)


if __name__ == "__main__":
    unittest.main()
