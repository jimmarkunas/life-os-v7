"""the keyless Google calendar sign-in (GitHub OIDC -> Google STS -> impersonated access token)."""

import io
import json
import unittest
import urllib.error
from unittest import mock

from lifeos.platform import gcal

OIDC_URL = "https://actions.example/idtoken?api-version=2.0"
ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL,
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "REQ-BEARER-SECRET",
    "GCAL_CALENDAR_ID": "cal-id",
}
OIDC_JWT, FEDERATED, ACCESS = "OIDC.JWT.VALUE", "ya29.federated-token", "ya29.calendar-access-token"


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def reply(doc):
    return Reply(json.dumps(doc).encode())


class Net:
    """Routes the three calls by URL"""

    def __init__(self, fail=None, bad=None):
        self.fail, self.bad, self.seen = fail, bad or {}, []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.seen.append((url, request.get_method(), dict(request.header_items()), request.data))
        stage = (
            "oidc"
            if url.startswith("https://actions.example")
            else "sts"
            if url == gcal.STS_URL
            else "iam"
            if url == gcal.IAM_URL
            else "api"
        )
        if self.fail == stage:
            raise urllib.error.HTTPError(url, 403, "refused", {}, io.BytesIO(b"{}"))
        if stage in self.bad:
            return reply(self.bad[stage])
        if stage == "oidc":
            return reply({"value": OIDC_JWT})
        if stage == "sts":
            return reply({"access_token": FEDERATED, "token_type": "Bearer"})
        if stage == "iam":
            return reply({"accessToken": ACCESS, "expireTime": "2026-11-27T03:00:00Z"})
        return reply({"items": []})


def client(out, **kw):
    return gcal.GoogleCalendar.from_env(ENV, out=out, **kw)


class Keyless(unittest.TestCase):
    def test_success_makes_exactly_the_three_calls_and_uses_the_impersonated_token(self):
        net, lines = Net(), []
        with mock.patch("urllib.request.urlopen", net):
            events = client(lines.append).list_events("2026-11-27T00:00:00Z", "2026-11-28T00:00:00Z")
        self.assertEqual(events, [])
        oidc, sts, iam, api = net.seen
        self.assertIn("audience=" + "%2F%2Fiam.googleapis.com", oidc[0])
        self.assertEqual(oidc[2]["Authorization"], "Bearer REQ-BEARER-SECRET")
        sent = json.loads(sts[3])
        self.assertEqual(
            {k: sent[k] for k in sent if k != "subjectToken"},
            {
                "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
                "audience": gcal.WIF_AUDIENCE,
                "scope": "https://www.googleapis.com/auth/cloud-platform",
                "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
                "subjectTokenType": "urn:ietf:params:oauth:token-type:jwt",
            },
        )
        self.assertEqual(sent["subjectToken"], OIDC_JWT)
        self.assertEqual(iam[2]["Authorization"], "Bearer " + FEDERATED)
        self.assertEqual(
            json.loads(iam[3]), {"scope": [gcal.SCOPE], "lifetime": "3600s"}
        )  # the calendar scope the client already uses
        self.assertEqual(api[2]["Authorization"], "Bearer " + ACCESS)
        self.assertTrue(api[0].startswith("https://www.googleapis.com/calendar/v3/calendars/cal-id/events"))

    def test_the_read_only_scope_is_the_one_asked_for(self):
        net, lines = Net(), []
        with mock.patch("urllib.request.urlopen", net):
            gcal.GoogleCalendar.from_env(ENV, out=lines.append, scope=gcal.SCOPE_READONLY).list_events("a", "b")
        self.assertEqual(json.loads(net.seen[2][3])["scope"], [gcal.SCOPE_READONLY])

    def test_every_token_is_masked_once_each_and_the_only_other_line_is_the_fixed_ok(self):
        lines = []
        with mock.patch("urllib.request.urlopen", Net()):
            client(lines.append).list_events("a", "b")
        self.assertEqual(
            lines,
            [f"::add-mask::{OIDC_JWT}", f"::add-mask::{FEDERATED}", f"::add-mask::{ACCESS}", "gcal auth: keyless ok"],
        )
        self.assertTrue(all("\n" not in line for line in lines))
        self.assertNotIn("REQ-BEARER-SECRET", "".join(lines))

    def test_the_token_is_fetched_once_per_client(self):
        net, lines = Net(), []
        with mock.patch("urllib.request.urlopen", net):
            c = client(lines.append)
            c.list_events("a", "b")
            c.list_events("a", "b")
        self.assertEqual(sum(1 for u, *_ in net.seen if u == gcal.STS_URL), 1)
        self.assertEqual(lines.count("gcal auth: keyless ok"), 1)

    def test_each_stage_failing_is_its_own_fixed_code_and_nothing_secret_leaks_into_it(self):
        for stage, code in (
            ("oidc", "GCAL_OIDC_UNAVAILABLE"),
            ("sts", "GCAL_STS_REFUSED"),
            ("iam", "GCAL_IAM_REFUSED"),
        ):
            with self.subTest(stage):
                lines = []
                with mock.patch("urllib.request.urlopen", Net(fail=stage)):
                    with self.assertRaises(gcal.GcalError) as error:
                        client(lines.append).list_events("a", "b")
                self.assertEqual(str(error.exception), code)
                self.assertNotIn("gcal auth: keyless ok", lines)

    def test_a_reply_without_the_token_is_refused_with_the_stage_code(self):
        for stage, bad, code in (
            ("sts", {"error": "x"}, "GCAL_STS_REFUSED"),
            ("iam", {}, "GCAL_IAM_REFUSED"),
            ("oidc", {"nope": 1}, "GCAL_OIDC_UNAVAILABLE"),
        ):
            with self.subTest(stage):
                with mock.patch("urllib.request.urlopen", Net(bad={stage: bad})):
                    with self.assertRaises(gcal.GcalError) as error:
                        client([].append).list_events("a", "b")
                self.assertEqual(str(error.exception), code)

    def test_no_oidc_bearer_is_oidc_unavailable_without_any_call(self):
        net = Net()
        env = {"ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL, "GCAL_CALENDAR_ID": "cal-id"}
        with mock.patch("urllib.request.urlopen", net):
            with self.assertRaises(gcal.GcalError) as error:
                gcal.GoogleCalendar.from_env(env, out=[].append).list_events("a", "b")
        self.assertEqual((str(error.exception), net.seen), ("GCAL_OIDC_UNAVAILABLE", []))

    def test_the_path_is_picked_by_the_oidc_url_and_the_key_path_needs_no_oidc(self):
        self.assertTrue(gcal.keyless_available(ENV))
        self.assertFalse(gcal.keyless_available({"GCAL_CALENDAR_ID": "c"}))
        with self.assertRaises(gcal.GcalError) as error:
            gcal.GoogleCalendar.from_env({"GCAL_CALENDAR_ID": "c"})
        self.assertEqual(
            str(error.exception), "GCAL_CONFIG_MISSING:GCAL_SERVICE_ACCOUNT_JSON"
        )  # no OIDC, no key: degrades like a missing key
        with self.assertRaises(gcal.GcalError) as error:
            gcal.GoogleCalendar.from_env({"ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL})
        self.assertEqual(str(error.exception), "GCAL_CONFIG_MISSING:GCAL_CALENDAR_ID")
        key = json.dumps({"client_email": "x", "private_key": "k"})
        self.assertFalse(
            gcal.GoogleCalendar.from_env({"GCAL_SERVICE_ACCOUNT_JSON": key, "GCAL_CALENDAR_ID": "c"})._keyless
        )

    def test_a_keyless_environment_ignores_a_leftover_key_value(self):
        key = json.dumps({"client_email": "x", "private_key": "k"})
        net = Net()
        with mock.patch("urllib.request.urlopen", net):
            gcal.GoogleCalendar.from_env({**ENV, "GCAL_SERVICE_ACCOUNT_JSON": key}, out=[].append).list_events("a", "b")
        self.assertNotIn("oauth2.googleapis.com", " ".join(u for u, *_ in net.seen))

    def test_the_followup_calendar_reader_signs_in_keyless_when_there_is_no_key(self):
        from lifeos.platform import followup_fetchers as F

        class Cal:
            def __init__(self, secret, calendar_id):
                self.secret = secret

            def list_events(self, a, b):
                return [{"id": "e1", "updated": "2026-11-27T01:00:00Z", "summary": "s", "htmlLink": "l"}]

        seen = []
        ref = next(r for r, k in F._BY_REF.items() if k[0] == "calendar")
        fetch = F.build_fetchers(
            {**ENV, "FOLLOWUP_GCAL_PERSONAL_CALENDAR_ID": "cal"},
            factories={"calendar": lambda s, c: seen.append(s) or Cal(s, c)},
        )["calendar"]
        fetch(ref, None)
        self.assertEqual(seen, [gcal.KEYLESS])


if __name__ == "__main__":
    unittest.main()
