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
    """A bare HTTP 400 hid which request Notion refused."""

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


class RetryTests(unittest.TestCase):
    """a 409 conflict (not applied) is retried on any method, a 502/503/504 only on an idempotent request (GET, DELETE)
    """

    def run_calls(self, method, *answers, once=False, rate_retries=None):
        import urllib.request
        from types import SimpleNamespace
        from unittest import mock
        from tests.kit.http import Response, http_error
        sleeps, items = [], list(answers)

        def opener(request, timeout=0):
            item = items.pop(0)
            if isinstance(item, tuple):
                raise http_error(SimpleNamespace(full_url=request.full_url), item[0], headers=item[1] if len(item) > 1 else None)
            return Response({"ok": 1})
        ticks = iter(range(0, 10_000_000, 100))
        client = notion_client.Client({"NOTION_API_TOKEN": "t", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "s"}, clock=lambda: next(ticks), sleep=sleeps.append, jitter=lambda: 0.5)
        self.client = client
        send = client.call_once if once else client.call
        with mock.patch.object(urllib.request, "urlopen", opener):
            try:
                result = send(method, "/blocks/b", rate_retries=rate_retries) if once and rate_retries is not None else send(method, "/blocks/b")
            except notion_client.NotionError as error:
                result = str(error)
        return result, [s for s in sleeps if s > 0.45], len(items), client.calls

    def test_a_409_then_success_is_retried_with_backoff_and_jitter_for_any_method(self):
        for method, once in (("GET", False), ("PATCH", False), ("PATCH", True)):
            result, waits, left, calls = self.run_calls(method, (409,), (409,), None, once=once)
            self.assertEqual((result, waits, left, calls), ({"ok": 1}, [0.75, 1.5], 0, 3))

    def test_a_409_forever_raises_the_same_fixed_code_after_the_capped_attempts(self):
        result, waits, left, calls = self.run_calls("PATCH", (409,), (409,), (409,), (409,), (409,))
        self.assertEqual((result, waits, left, calls), ("NOTION_HTTP_409", [0.75, 1.5, 3.0], 1, 4))

    def test_a_5xx_is_retried_on_an_idempotent_request_only(self):
        self.assertEqual(self.run_calls("GET", (502,), None)[:3], ({"ok": 1}, [0.75], 0))
        self.assertEqual(self.run_calls("DELETE", (503,), None)[:3], ({"ok": 1}, [0.75], 0))  # Notion: GET or DELETE
        for code in (503, 504):
            self.assertEqual(self.run_calls("GET", (code,), (code,), (code,), (code,))[0], f"NOTION_HTTP_{code}")
        for method in ("POST", "PATCH"):
            result, waits, left, _ = self.run_calls(method, (502,), None)
            self.assertEqual((result, waits, left), ("NOTION_HTTP_502", [], 1))

    def test_a_429_waits_the_retry_after_with_jitter_on_every_call_including_call_once(self):
        self.assertEqual(self.run_calls("GET", (429, {"Retry-After": "8"}), None)[:3], ({"ok": 1}, [10.0], 0))  # 8 s x (1 + 0.5 / 2)
        self.assertEqual(self.run_calls("GET", (429, {"Retry-After": "8"}), None, once=True)[:3], ({"ok": 1}, [10.0], 0))  # the default for call_once too
        self.assertEqual(self.run_calls("PATCH", (429, {"Retry-After": "60"}), None, once=True)[1], [75.0])
        self.assertEqual(self.run_calls("PATCH", (429,), (429,), (429,), None, once=True)[1], [2.5, 5.0, 10.0])  # no header: exponential from 2 s
        self.assertEqual(self.run_calls("GET", (429,), None, once=True, rate_retries=0)[:3], ("NOTION_HTTP_429", [], 1))  # a caller can still ask for none

    def test_a_529_is_retried_like_a_429(self):
        self.assertEqual(self.run_calls("PATCH", (529, {"Retry-After": "4"}), None)[:3], ({"ok": 1}, [5.0], 0))
        self.assertEqual(self.run_calls("PATCH", *[(529,)] * 9)[0], "NOTION_HTTP_529")

    def test_a_429_forever_gives_up_after_the_capped_retries_with_the_same_fixed_code(self):
        result, waits, left, calls = self.run_calls("PATCH", *[(429,)] * 9, once=True)
        self.assertEqual((result, waits, left, calls), ("NOTION_HTTP_429", [2.5, 5.0, 10.0, 20.0, 40.0, 75.0], 2, 7))
        result, waits, left, calls = self.run_calls("GET", *[(429,)] * 9)
        self.assertEqual((result, left, calls), ("NOTION_HTTP_429", 2, 7))

    def test_a_wait_longer_than_notions_maximum_or_a_spent_wait_budget_gives_up_at_once(self):
        result, waits, left, calls = self.run_calls("GET", (429, {"Retry-After": "900"}), None)  # a per-workspace wait: not slept through
        self.assertEqual((result, waits, left, calls), ("NOTION_HTTP_429", [], 1, 1))
        result, waits, left, calls = self.run_calls("GET", *[(429, {"Retry-After": "60"})] * 5)  # 75 + 75, a third 75 would pass 180
        self.assertEqual((result, waits, calls), ("NOTION_HTTP_429", [75.0, 75.0], 3))

    def test_a_429_slows_the_process_down_for_the_rest_of_the_run(self):
        self.run_calls("GET", (429,), (429,), None)
        self.assertEqual(self.client._gap, notion_client.limits.NOTION_GAP_SECONDS * 4)
        self.run_calls("GET", *[(429,)] * 6, None)
        self.assertEqual(self.client._gap, notion_client.limits.NOTION_GAP_MAX_SECONDS)


if __name__ == "__main__":
    unittest.main()

