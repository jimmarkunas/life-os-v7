"""LIFE-921: the Anthropic report reader may call only the two report endpoints, prints numbers only, and turns every failure into nulls."""
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest import mock

from lifeos.edge.anthropic_report import reader

NOW = datetime(2026, 11, 23, 19, 0, tzinfo=timezone.utc)
KEY = "sk-ant-admin-TESTKEY-not-real"


class Reply:
    def __init__(self, body):
        self.body = body

    def read(self):
        return json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener(cost=None, keys=("k1",), calls=None):
    def call(request, timeout=None):
        if calls is not None:
            calls.append(request)
        if "cost_report" in request.full_url:
            return Reply(cost if cost is not None else {"data": [{"results": [{"amount": "2.2661445", "currency": "USD"}]}], "has_more": False})
        return Reply({"data": [{"results": [{"api_key_id": k} for k in keys]}], "has_more": False})
    return call


class Paths(unittest.TestCase):
    def test_every_path_but_the_two_report_endpoints_is_refused_before_any_request(self):
        calls = []
        for path in ("/v1/organizations/users", "/v1/organizations/api_keys", "/v1/organizations/cost_report/extra", "/v1/messages", "/v1/organizations/workspaces", "", "//evil/v1/organizations/cost_report"):
            with self.assertRaises(reader.Refused, msg=path):
                reader.get(path, {}, KEY, opener(calls=calls))
        self.assertEqual(calls, [])

    def test_the_allowed_paths_go_to_the_anthropic_host_with_get_and_the_key_only_in_its_header(self):
        calls = []
        reader.get(reader.ALLOWED[0], {"starting_at": "2026-11-01T00:00:00Z"}, KEY, opener(calls=calls))
        (request,) = calls
        self.assertEqual((request.get_method(), request.full_url.split("/")[2], request.get_header("X-api-key")), ("GET", "api.anthropic.com", KEY))
        self.assertNotIn(KEY, request.full_url)
        self.assertEqual(reader.ALLOWED, ("/v1/organizations/cost_report", "/v1/organizations/usage_report/messages"))

    def test_the_program_text_calls_no_other_endpoint(self):
        text = open(reader.__file__).read()
        self.assertEqual(sorted(set(re.findall(r"/v1/[a-z_/]+", text))), ["/v1/organizations/cost_report", "/v1/organizations/usage_report/messages"])


class Reading(unittest.TestCase):
    def test_cents_become_dollars_and_one_key_is_honchos(self):
        self.assertEqual(reader.reading(KEY, opener(), NOW), {"billed_usd": 0.0227, "honcho_usd": 0.0227, "as_of": "2026-11-23T19:00:00Z"})

    def test_a_second_key_makes_the_attribution_unverified_but_keeps_billed(self):
        self.assertEqual(reader.reading(KEY, opener(keys=("k1", "k2")), NOW), {"billed_usd": 0.0227, "honcho_usd": None, "as_of": "2026-11-23T19:00:00Z"})

    def test_the_report_counts_from_the_start_of_the_current_credit_window(self):
        calls = []
        reader.reading(KEY, opener(calls=calls), now=NOW)
        self.assertTrue(calls and all("starting_at=2026-11-06T00%3A00%3A00Z" in c.full_url for c in calls), [c.full_url for c in calls])      # Nov 5 6:00 PM CT = Nov 6 00:00 UTC
        calls.clear()
        reader.reading(KEY, opener(calls=calls), now=datetime(2026, 12, 6, 0, 0, 1, tzinfo=timezone.utc))                                      # the new window: spend before it is not counted
        self.assertTrue(calls and all("starting_at=2026-12-06T00%3A00%3A00Z" in c.full_url for c in calls))

    def test_no_spend_is_zero_dollars_not_unknown(self):
        self.assertEqual(reader.reading(KEY, opener(cost={"data": [], "has_more": False}, keys=()), NOW)["billed_usd"], 0.0)

    def test_pages_are_followed_and_added(self):
        pages = [{"data": [{"results": [{"amount": "100", "currency": "USD"}]}], "has_more": True, "next_page": "p2"}, {"data": [{"results": [{"amount": "50", "currency": "USD"}]}], "has_more": False}]
        seen = []

        def call(request, timeout=None):
            seen.append(request.full_url)
            if "cost_report" in request.full_url:
                return Reply(pages[len(seen) - 1])
            return Reply({"data": [], "has_more": False})
        self.assertEqual(reader.reading(KEY, call, NOW)["billed_usd"], 1.5)
        self.assertIn("page=p2", seen[1])

    def test_every_failure_is_nulls_and_never_text(self):
        def boom(request, timeout=None):
            raise OSError(KEY)
        self.assertEqual(reader.reading(KEY, boom, NOW), {"billed_usd": None, "honcho_usd": None, "as_of": None})
        self.assertEqual(reader.reading(KEY, opener(cost={"data": [{"results": [{"amount": "1", "currency": "EUR"}]}]}), NOW)["billed_usd"], None)

    def test_main_prints_one_json_line_of_numbers_and_never_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "anthropic_admin_key"), "w").write(KEY + "\n")
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": tmp}), mock.patch.object(reader, "reading", lambda key: {"billed_usd": 1.0, "honcho_usd": None, "as_of": None}), redirect_stdout(out):
                reader.main()
        self.assertEqual(json.loads(out.getvalue()), {"billed_usd": 1.0, "honcho_usd": None, "as_of": None})
        self.assertNotIn(KEY, out.getvalue())

    def test_a_missing_credential_is_nulls(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(out):
            reader.main()
        self.assertEqual(json.loads(out.getvalue()), {"billed_usd": None, "honcho_usd": None, "as_of": None})


class Units(unittest.TestCase):
    def test_the_key_is_the_readers_own_credential_and_the_socket_is_local(self):
        base = os.path.dirname(reader.__file__)
        service = open(os.path.join(base, "lifeos-anthropic-report@.service")).read()
        socket_unit = open(os.path.join(base, "lifeos-anthropic-report.socket")).read()
        self.assertIn("LoadCredentialEncrypted=anthropic_admin_key:/etc/lifeos-anthropic-report/anthropic_admin_key.cred", service)
        self.assertIn("DynamicUser=yes", service)
        self.assertIn("ListenStream=/run/lifeos-anthropic-report.sock", socket_unit)
        self.assertNotRegex(socket_unit, r"ListenStream=\d|ListenStream=\[|0\.0\.0\.0|OnCalendar|OnBootSec")             # no TCP port, no clock


if __name__ == "__main__":
    unittest.main()
