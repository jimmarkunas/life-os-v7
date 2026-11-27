"""Each stage's row says how many calls each outside service got: names and numbers only."""

import io
import threading
import unittest
import urllib.error
from unittest import mock

from lifeos.platform import api_counts, gmail, notion_client, rest, stage_timing


class Counter(unittest.TestCase):
    def setUp(self):
        api_counts.begin()

    def test_a_stage_gets_only_its_own_threads_calls_and_take_clears_them(self):
        api_counts.count("notion")
        api_counts.count("notion")
        api_counts.count("JIRA")
        seen = {}
        helper = threading.Thread(
            target=lambda: (api_counts.begin(), api_counts.count("gmail"), seen.update(api_counts.take()))
        )
        helper.start()
        helper.join()
        self.assertEqual(seen, {"gmail": 1})
        self.assertEqual(api_counts.take(), {"jira": 1, "notion": 2})
        self.assertEqual(api_counts.take(), {})
        self.assertGreaterEqual(api_counts.totals()["gmail"], 1)  # the helper's call is in the totals

    def test_the_rest_layers_error_prefixes_become_service_names(self):
        for prefix, name in (("JIRA", "jira"), ("OUTLOOK", "graph"), ("TODOIST", "todoist"), ("GCAL", "gcal")):
            api_counts.begin()
            api_counts.count(prefix)
            self.assertEqual(api_counts.take(), {name: 1})


class Wired(unittest.TestCase):
    def setUp(self):
        api_counts.begin()

    def test_every_attempt_of_the_shared_rest_layer_counts_including_retries(self):
        answers = [urllib.error.HTTPError("https://x", 503, "x", None, None), mock.MagicMock()]
        answers[1].__enter__.return_value.read.return_value = b"{}"
        with mock.patch.object(rest.urllib.request, "urlopen", side_effect=answers):
            rest.call(lambda: object(), RuntimeError, "JIRA", lambda s: None)
        self.assertEqual(api_counts.take(), {"jira": 2})

    def test_notion_and_gmail_attempts_count_by_service(self):
        client = notion_client.Client({"NOTION_API_TOKEN": "t", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})
        reply = mock.MagicMock()
        reply.__enter__.return_value.read.return_value = b"{}"
        with mock.patch.object(notion_client.urllib.request, "urlopen", return_value=reply):
            client.call("GET", "/users/me")
        self.assertEqual(api_counts.take(), {"notion": 1})
        mail = gmail.Gmail("i", "s", "r")
        mail._token = "t"
        with mock.patch.object(gmail.urllib.request, "urlopen", return_value=reply):
            mail._request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/labels")
        self.assertEqual(api_counts.take(), {"gmail": 1})


class Row(unittest.TestCase):
    def test_a_row_carries_api_calls_only_when_there_are_some_and_a_stage_begin_clears_the_last_stage(self):
        quiet = stage_timing.record("x", "ok", 1.0)
        self.assertNotIn("api_calls", quiet)
        busy = stage_timing.record("x", "ok", 1.0, api_calls={"notion": 3, "gmail": 1})
        self.assertEqual(busy["api_calls"], {"gmail": 1, "notion": 3})
        api_counts.count("notion")
        stage_timing.begin("next")
        self.assertEqual(api_counts.take(), {})

    def test_the_row_never_holds_anything_but_names_and_numbers(self):
        row = stage_timing.record("x", "ok", 1.0, api_calls={"notion": 2})
        self.assertEqual(set(row["api_calls"]), {"notion"})
        out = io.StringIO()
        self.assertTrue(stage_timing.emit(row, environ={}, err=out))
        self.assertIn('"api_calls": {"notion": 2}', out.getvalue())


if __name__ == "__main__":
    unittest.main()
