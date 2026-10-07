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
from tests.kit.http import Response, http_error  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]




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


class WriteTests(unittest.TestCase):
    def test_the_write_sign_in_asks_for_the_write_scope_and_refresh_never_narrows_it(self):
        sent = []
        with patch.object(outlook, "_post_form", side_effect=lambda url, form: (sent.append(form), {"device_code": "D", "access_token": "A"})[1]):
            outlook.device_start("cid")
            outlook.device_start("cid", write=True)
            outlook.refresh("cid", "R")
        self.assertIn("Mail.Read ", sent[0]["scope"] + " ")
        self.assertNotIn("Mail.ReadWrite", sent[0]["scope"])
        self.assertIn("Mail.ReadWrite", sent[1]["scope"])
        self.assertNotIn("scope", sent[2])                                       # a refresh keeps whatever the sign-in was granted

    def test_folder_lookup_create_and_a_move_that_is_read_back(self):
        calls = []

        def fake(request, timeout=0):
            url, method = request.full_url, request.get_method()
            calls.append((method, url.split("graph.microsoft.com/v1.0")[-1].split("?")[0], request.data))
            if "oauth2" in url:
                return Response({"access_token": "A"})
            if method == "GET" and url.endswith("/me/mailFolders") or "mailFolders?" in url:
                return Response({"value": []})
            if method == "POST" and url.endswith("/me/mailFolders"):
                return Response({"id": "F1"})
            if method == "POST" and url.endswith("/move"):
                return Response({"id": "m"})
            if method == "GET" and "/me/messages/" in url:
                return Response({"id": "m", "parentFolderId": "F1"})
            raise AssertionError(url)

        client = Outlook("cid", "R", sleep=lambda s: None)
        with patch("urllib.request.urlopen", side_effect=fake):
            self.assertIsNone(client.folder_id("J Newsletters"))
            self.assertEqual(client.folder_id("J Newsletters", create=True), "F1")
            self.assertTrue(client.move("AAMk/+=id", "F1"))
        self.assertEqual([c[0] for c in calls if "oauth2" not in c[1]], ["GET", "GET", "POST", "POST", "GET"])
        self.assertIn("AAMk%2F%2B%3Did", calls[-2][1])                          # ids are URL-quoted
        self.assertEqual(json.loads(calls[-2][2]), {"destinationId": "F1"})

    def test_a_denied_write_is_a_fixed_code_and_never_leaks(self):
        def denied(request, timeout=0):
            if "oauth2" in request.full_url:
                return Response({"access_token": "A"})
            raise urllib.error.HTTPError(request.full_url, 403, "secret body", {}, None)

        with patch("urllib.request.urlopen", side_effect=denied):
            with self.assertRaises(OutlookError) as error:
                Outlook("cid", "R", sleep=lambda s: None).move("m", "F1")
        self.assertEqual(str(error.exception), "OUTLOOK_HTTP_403")

    def test_nothing_in_the_adapter_deletes_or_sends(self):
        source = (ROOT / "lifeos/platform/outlook.py").read_text()
        for forbidden in ('"DELETE"', "'DELETE'", "/send", "sendMail", "/forward", "permanentDelete"):
            self.assertNotIn(forbidden, source)


class FakeDb:
    """Just enough of the token table: rows are {account: [refresh_token, fingerprint]}."""

    def __init__(self, rows=None):
        self.rows = {k: list(v) if isinstance(v, (list, tuple)) else [v, None] for k, v in (rows or {}).items()}

    def connect(self):
        db = self

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *e): return False

            def execute(self, sql, args=None):
                self.out = []
                if "information_schema" in sql:
                    self.out = [(1,)]
                elif sql.startswith("INSERT"):
                    account, token, _, fp = args
                    old = db.rows.get(account, [None, None])
                    db.rows[account] = [token, fp if fp is not None else old[1]]
                elif sql.startswith("SELECT refresh_token"):
                    self.out = [(db.rows[args[0]][0],)] if args[0] in db.rows else []
                elif sql.startswith("SELECT fingerprint"):
                    self.out = [(db.rows[args[0]][1],)] if args[0] in db.rows else []
                elif "WHERE fingerprint" in sql:
                    self.out = [(k,) for k, v in db.rows.items() if v[1] == args[0]]
                elif sql.startswith("SELECT account"):
                    self.out = [(k,) for k in sorted(db.rows)]

            def fetchone(self):
                return self.out[0] if self.out else None

            def fetchall(self):
                return self.out

        class Conn:
            def cursor(self): return Cursor()
            def __enter__(self): return self
            def __exit__(self, *e): return False

        return Conn()


class Mailbox:
    """Stands in for the Graph client during sign-in: each refresh token belongs to one mailbox."""

    def __init__(self, ids):
        self.ids = ids

    def __call__(self, client_id, token):
        mailbox_id = self.ids[token]

        class One:
            def get(self, path, params=None):
                return {"id": mailbox_id}

        return One()


class StageTests(unittest.TestCase):
    ENV = {"OUTLOOK_CLIENT_ID": "cid", "OUTLOOK_ACCOUNT": "personal"}

    def sign_in(self, db, token, env=None, replace=False, ids=None):
        said = []
        with patch.object(stage, "device_start", return_value={"device_code": "D", "user_code": "ABCD-1234",
                                                               "verification_uri": "https://example.invalid/device"}), \
                patch.object(stage, "device_wait", return_value={"refresh_token": token}):
            out = stage.auth(1, True, environ=env or self.ENV, connect=db.connect, say=said.append, replace=replace,
                             client_factory=Mailbox(ids or {"SECRET-REFRESH": "inbox-1", "OTHER": "inbox-2"}))
        return out, said

    def test_auth_dry_run_saves_nothing_and_live_saves_the_token_without_printing_it(self):
        db = FakeDb()
        out = stage.auth(1, False, environ=self.ENV, connect=db.connect)
        self.assertEqual((out["signed_in"], db.rows), (0, {}))
        out, said = self.sign_in(db, "SECRET-REFRESH")
        self.assertEqual(out["signed_in"], 1)
        self.assertEqual(db.rows["personal"][0], "SECRET-REFRESH")
        self.assertTrue(any("ABCD-1234" in line for line in said))
        self.assertFalse(any("SECRET-REFRESH" in line for line in said))

    def test_a_second_mailbox_cannot_silently_replace_the_first_and_one_mailbox_cannot_hold_two_labels(self):
        db = FakeDb()
        self.sign_in(db, "SECRET-REFRESH")
        with self.assertRaises(OutlookError) as error:                       # same label, different mailbox
            self.sign_in(db, "OTHER")
        self.assertEqual(str(error.exception), "OUTLOOK_LABEL_HOLDS_A_DIFFERENT_MAILBOX")
        self.assertEqual(db.rows["personal"][0], "SECRET-REFRESH")
        with self.assertRaises(OutlookError) as error:                       # same mailbox, different label
            self.sign_in(db, "SECRET-REFRESH", env={"OUTLOOK_CLIENT_ID": "cid", "OUTLOOK_ACCOUNT": "work"})
        self.assertEqual(str(error.exception), "OUTLOOK_MAILBOX_ALREADY_SIGNED_IN_AS_OTHER_LABEL")
        self.sign_in(db, "OTHER", env={"OUTLOOK_CLIENT_ID": "cid", "OUTLOOK_ACCOUNT": "work"})   # a different mailbox on a free label
        self.assertEqual(sorted(db.rows), ["personal", "work"])
        self.sign_in(db, "OTHER", replace=True, env={"OUTLOOK_CLIENT_ID": "cid", "OUTLOOK_ACCOUNT": "work"})   # same mailbox again is fine

    def test_an_unfingerprinted_legacy_row_may_be_replaced_and_a_rotated_token_keeps_the_fingerprint(self):
        db = FakeDb({"personal": ["OLD", None]})
        self.sign_in(db, "SECRET-REFRESH")
        self.assertEqual(db.rows["personal"][0], "SECRET-REFRESH")
        fp = db.rows["personal"][1]
        self.assertTrue(fp)
        from lifeos.outlook import store as s
        s.save(db.connect(), "personal", "ROTATED")
        self.assertEqual(db.rows["personal"], ["ROTATED", fp])

    def test_config_must_name_a_known_label_and_a_client_id(self):
        for env in ({"OUTLOOK_CLIENT_ID": "c", "OUTLOOK_ACCOUNT": "not-a-known-label"}, {"OUTLOOK_ACCOUNT": "personal"}):
            with self.assertRaises(OutlookError):
                stage.auth(1, True, environ=env, connect=FakeDb().connect)

    def test_probe_reports_counts_by_position_and_fails_if_any_account_fails_or_none_exist(self):
        db = FakeDb({"personal": ["R1", "f1"], "work": ["R2", "f2"]})

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
        direct = set(re.findall(r"secrets\.(\w+)", self.text))
        loaded = set(re.findall(r"^\s+[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12} > (\w+)$", self.text, re.M))                  # EDGE-1.3: migrated values arrive through the Bitwarden loader, not as GitHub secrets
        self.assertIn("LIFEOS_BWS_RUNTIME_TOKEN", direct)
        self.assertEqual(sorted((direct - {"LIFEOS_BWS_RUNTIME_TOKEN"}) | loaded),
                         sorted(["OUTLOOK_CLIENT_ID"] + [f"LIFEOS_ACQ_{n}" for n in (
                             "SSH_PRIVATE_KEY", "DB_PASSWORD", "SSH_HOST", "SSH_PORT", "SSH_USER", "SSH_KNOWN_HOSTS", "DB_NAME", "DB_USER")]))

    def test_no_mailbox_address_anywhere_in_tracked_outlook_code(self):
        for path in list((ROOT / "lifeos" / "outlook").glob("*.py")) + [ROOT / "lifeos/platform/outlook.py"]:
            self.assertNotRegex(path.read_text(), r"[\w.+-]+@[\w-]+\.[\w.]+")


if __name__ == "__main__":
    unittest.main()
