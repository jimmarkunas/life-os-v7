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


class RefusedRequest(unittest.TestCase):
    """A bare HTTP 400 hid which request Notion refused (D165). The detail is Notion's code and the body paths it names, never its message text."""

    def refuse(self, body):
        import urllib.request
        from unittest import mock
        from tests.kit.http import http_error
        client = notion_client.Client({"NOTION_API_TOKEN": "t", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "s"}, sleep=lambda s: None)
        with mock.patch.object(urllib.request, "urlopen", lambda request, timeout=0: (_ for _ in ()).throw(http_error(request, 400, body))):
            with self.assertRaises(notion_client.NotionError) as caught:
                client.call("POST", "/data_sources/s/query", {})
        return caught.exception

    def test_the_code_and_body_paths_are_kept_and_nothing_else(self):
        error = self.refuse(b'{"object":"error","status":400,"code":"validation_error","message":"body failed validation: body.filter.and[4].or[1].and should be not present, instead was Acme Corp secret"}')
        self.assertEqual((str(error), error.detail), ("NOTION_HTTP_400", "validation_error body.filter.and[4].or[1].and"))
        self.assertNotIn("Acme", error.detail)

    def test_an_unreadable_or_odd_body_gives_no_detail_and_the_same_fixed_code(self):
        for body in (b"secret body text", b"[]", b'{"code":"Not A Code!","message":7}'):
            error = self.refuse(body)
            self.assertEqual((str(error), error.detail), ("NOTION_HTTP_400", ""))


if __name__ == "__main__":
    unittest.main()

