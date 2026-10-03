import json
import re
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from lifeos.outlook import stage, store
from lifeos.platform import outlook
from lifeos.platform.outlook import Outlook, OutlookError

ROOT = Path(__file__).resolve().parents[2]


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(request, code, body=b"secret body text", headers=None):
    return urllib.error.HTTPError(request.full_url, code, "msg", headers or {}, __import__("io").BytesIO(body))


class Router:
    """Fake network: token endpoint + Graph pages. Records every request."""

    def __init__(self, pages=(), rotate=False, fail=None):
        self.pages, self.rotate, self.fail, self.seen = list(pages), rotate, fail or {}, []

    def __call__(self, request, timeout=0):
        self.seen.append((request.get_method(), request.full_url, dict(request.headers)))
        if "/oauth2/v2.0/token" in request.full_url:
            if "token" in self.fail:
                raise http_error(request, 400, b'{"error": "invalid_grant", "error_description": "AADSTS secret"}')
            return Response({"access_token": "ACCESS", "refresh_token": "ROTATED" if self.rotate else "OLD"})
        if request.full_url.startswith(outlook.GRAPH):
            return Response(self.pages.pop(0))
        raise AssertionError(request.full_url)


def page(count, start=0, link=None, unread=0):
    body = {"value": [{"id": f"m{start + i}", "isRead": i >= unread, "subject": "Private subject"} for i in range(count)]}
    if link:
        body["@odata.nextLink"] = link
    return body


class ClientTests(unittest.TestCase):
    def test_enumeration_follows_every_page_asks_for_immutable_ids_and_saves_a_rotated_token(self):
        saved = []
        net = Router([page(2, 0, outlook.GRAPH + "/me/next"), page(2, 2)], rotate=True)
        client = Outlook("cid", "OLD", save_refresh=saved.append, sleep=lambda s: None)
        with patch("urllib.request.urlopen", side_effect=net):
            messages = client.messages("inbox", "2026-10-01T00:00:00Z")
        self.assertEqual([m["id"] for m in messages], ["m0", "m1", "m2", "m3"])
        self.assertEqual(saved, ["ROTATED"])
        graph = [h for _, url, h in net.seen if url.startswith(outlook.GRAPH)]
        self.assertTrue(all(h["Prefer"] == 'IdType="ImmutableId"' for h in graph))
        self.assertIn("receivedDateTime", net.seen[1][1])

    def test_an_incomplete_listing_raises_instead_of_returning_a_partial_census(self):
        net = Router([page(2, 0, outlook.GRAPH + "/me/next"), page(2, 2, outlook.GRAPH + "/me/more")])
        client = Outlook("cid", "OLD", sleep=lambda s: None)
        with patch("urllib.request.urlopen", side_effect=net):
            with self.assertRaises(OutlookError) as error:
                client.messages("inbox", limit=3)
        self.assertEqual(str(error.exception), "OUTLOOK_LISTING_INCOMPLETE")

    def test_never_follows_a_link_off_graph_with_the_token(self):
        net = Router([page(1, 0, "https://evil.invalid/steal")])
        client = Outlook("cid", "OLD", sleep=lambda s: None)
        with patch("urllib.request.urlopen", side_effect=net):
            with self.assertRaises(OutlookError) as error:
                client.messages()
        self.assertEqual(str(error.exception), "OUTLOOK_BAD_LINK")
        self.assertFalse(any("evil" in url for _, url, _ in net.seen))

    def test_throttling_is_retried_and_errors_never_leak_bodies_or_urls(self):
        calls = []

        def flaky(request, timeout=0):
            if "/oauth2/" in request.full_url:
                return Response({"access_token": "A"})
            calls.append(1)
            if len(calls) < 3:
                raise http_error(request, 429, headers={"Retry-After": "1"})
            return Response({"value": []})

        client = Outlook("cid", "R", sleep=lambda s: None)
        with patch("urllib.request.urlopen", side_effect=flaky):
            self.assertEqual(client.messages(), [])
        self.assertEqual(len(calls), 3)

        def denied(request, timeout=0):
            if "/oauth2/" in request.full_url:
                return Response({"access_token": "A"})
            raise http_error(request, 403)

        with patch("urllib.request.urlopen", side_effect=denied):
            with self.assertRaises(OutlookError) as error:
                client.get("/me/secret/path")
        self.assertEqual(str(error.exception), "OUTLOOK_HTTP_403")

    def test_refresh_failure_is_a_fixed_code(self):
        client = Outlook("cid", "OLD", sleep=lambda s: None)
        with patch("urllib.request.urlopen", side_effect=Router(fail={"token": 1})):
            with self.assertRaises(OutlookError) as error:
                client.get("/me/messages")
        self.assertEqual(str(error.exception), "OUTLOOK_REFRESH_INVALID_GRANT")
        for bad in (("", "r"), ("c", "")):
            with self.assertRaises(OutlookError):
                Outlook(*bad)

    def test_device_sign_in_waits_then_returns_tokens_and_fails_closed_on_decline(self):
        replies = iter([{"error": "authorization_pending"}, {"error": "slow_down"}, {"refresh_token": "R", "access_token": "A"}])
        with patch.object(outlook, "_post_form", side_effect=lambda url, form: next(replies)):
            clock = iter(range(0, 1000, 1))
            got = outlook.device_wait("cid", {"device_code": "D", "expires_in": 900, "interval": 1}, sleep=lambda s: None,
                                      clock=lambda: next(clock))
        self.assertEqual(got["refresh_token"], "R")
        with patch.object(outlook, "_post_form", side_effect=lambda url, form: {"error": "authorization_declined"}):
            with self.assertRaises(OutlookError) as error:
                outlook.device_wait("cid", {"device_code": "D", "expires_in": 900}, sleep=lambda s: None, clock=lambda: 0)
        self.assertEqual(str(error.exception), "OUTLOOK_SIGNIN_AUTHORIZATION_DECLINED")


class FakeDb:
    def __init__(self, rows=None):
        self.rows, self.sql = dict(rows or {}), []

    def connect(self):
        db = self

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *e): return False

            def execute(self, sql, args=None):
                db.sql.append(sql.split()[0])
                self.args, self.q = args, sql

            def fetchone(self):
                return (db.rows[self.args[0]],) if self.args[0] in db.rows else None

            def fetchall(self):
                return [(k,) for k in sorted(db.rows)]

        class Conn:
            def cursor(self): return Cursor()

            def __enter__(self): return self
            def __exit__(self, *e): return False

        # emulate INSERT ... ON DUPLICATE by hooking execute
        conn = Conn()
        orig = Cursor.execute

        def execute(self, sql, args=None):
            orig(self, sql, args)
            if sql.startswith("INSERT"):
                db.rows[args[0]] = args[1]

        Cursor.execute = execute
        return conn


class StageTests(unittest.TestCase):
    ENV = {"OUTLOOK_CLIENT_ID": "cid", "OUTLOOK_ACCOUNT": "personal"}

    def test_auth_dry_run_saves_nothing_and_live_saves_the_token_without_printing_it(self):
        db = FakeDb()
        out = stage.auth(1, False, environ=self.ENV, connect=db.connect)
        self.assertEqual((out["signed_in"], db.rows), (0, {}))
        said = []
        with patch.object(stage, "device_start", return_value={"device_code": "D", "user_code": "ABCD-1234",
                                                               "verification_uri": "https://example.invalid/device"}), \
                patch.object(stage, "device_wait", return_value={"refresh_token": "SECRET-REFRESH"}):
            out = stage.auth(1, True, environ=self.ENV, connect=db.connect, say=said.append)
        self.assertEqual((out["signed_in"], db.rows), (1, {"personal": "SECRET-REFRESH"}))
        self.assertTrue(any("ABCD-1234" in line for line in said))
        self.assertFalse(any("SECRET-REFRESH" in line for line in said))

    def test_config_must_name_a_known_label_and_a_client_id(self):
        for env in ({"OUTLOOK_CLIENT_ID": "c", "OUTLOOK_ACCOUNT": "not-a-known-label"}, {"OUTLOOK_ACCOUNT": "personal"}):
            with self.assertRaises(OutlookError):
                stage.auth(1, True, environ=env, connect=FakeDb().connect)

    def test_probe_reports_counts_by_position_and_fails_if_any_account_fails_or_none_exist(self):
        db = FakeDb({"personal": "R1", "work": "R2"})

        class Ok:
            def __init__(self, cid, token, save): self.token = token
            def messages(self, folder, since):
                return [{"isRead": False}, {"isRead": True}] if self.token == "R1" else [{"isRead": False}]

        out = stage.probe(1, False, environ=self.ENV, connect=db.connect, client_factory=Ok,
                          now=datetime(2026, 10, 7, tzinfo=timezone.utc))
        self.assertEqual((out["accounts"], out["ok"], out["recent"], out["unread"]), (2, 2, 3, 2))
        self.assertNotIn("personal", json.dumps(out))

        class Bad(Ok):
            def messages(self, folder, since): raise OutlookError("OUTLOOK_HTTP_403")

        with self.assertRaises(OutlookError) as error:
            stage.probe(1, False, environ=self.ENV, connect=db.connect, client_factory=Bad)
        self.assertEqual(str(error.exception), "OUTLOOK_PROBE_FAILED:2of2:OUTLOOK_HTTP_403@1,OUTLOOK_HTTP_403@2")
        with self.assertRaises(OutlookError):
            stage.probe(1, False, environ=self.ENV, connect=FakeDb().connect, client_factory=Ok)


class WorkflowTests(unittest.TestCase):
    text = (ROOT / ".github/workflows/outlook.yml").read_text()

    def test_the_sign_in_code_is_visible_while_the_run_waits(self):
        self.assertIn("python -u -m lifeos.run outlook-auth", self.text)         # unbuffered: the code must not wait for process exit
        self.assertIn("print(line, flush=True)", (ROOT / "lifeos/outlook/stage.py").read_text())

    def test_manual_only_and_only_the_outlook_and_database_secrets(self):
        self.assertNotRegex(self.text, r"\n  (schedule|push|pull_request):")
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", self.text))),
                         sorted(["OUTLOOK_CLIENT_ID"] + [f"LIFEOS_ACQ_{n}" for n in (
                             "SSH_PRIVATE_KEY", "DB_PASSWORD", "SSH_HOST", "SSH_PORT", "SSH_USER", "SSH_KNOWN_HOSTS", "DB_NAME", "DB_USER")]))

    def test_no_mailbox_address_anywhere_in_tracked_outlook_code(self):
        for path in list((ROOT / "lifeos" / "outlook").glob("*.py")) + [ROOT / "lifeos/platform/outlook.py"]:
            self.assertNotRegex(path.read_text(), r"[\w.+-]+@[\w-]+\.[\w.]+")


if __name__ == "__main__":
    unittest.main()
