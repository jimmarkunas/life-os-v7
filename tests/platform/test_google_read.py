"""LIFE-875 (synthetic): read-only Gmail and Drive for two named accounts, with a fake Google. No network, no real token."""
import base64
import io
import json
import re
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

from lifeos.platform import google_read as G

ROOT = Path(__file__).resolve().parents[2]
CREDS = {"client_id": "cid", "client_secret": "csecret-xxxxxxxx", "refresh_token": "rtoken-xxxxxxxx"}
OWNERS = {"at-personal": "jimmarkunas@gmail.com", "at-bytalos": "jim@bytalos.com"}                         # the synthetic token of each account reports that account's address


class FakeGoogle:
    """An opener that answers like Google for the four reads and records every request."""
    def __init__(self, revoked=False, scope="https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/drive.readonly"):
        self.requests, self.revoked, self.scope = [], revoked, scope

    def __call__(self, request, timeout=None):
        self.requests.append((request.get_method(), request.full_url, dict(request.header_items())))
        url = request.full_url
        if url == G.TOKEN_URL:
            if self.revoked:
                raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(b'{"error":"invalid_grant"}'))
            refresh = urllib.parse.parse_qs(request.data.decode())["refresh_token"][0]
            return self._reply({"access_token": "at-" + refresh, "scope": self.scope})
        if url in G.WHO.values():                                                                  # who owns this token: the address of the account it was made for
            email = OWNERS.get(request.get_header("Authorization", "").split(" ")[-1], "stranger@example.com")
            return self._reply({"emailAddress": email} if "gmail" in url else {"user": {"emailAddress": email}})
        if "/messages?" in url:
            return self._reply({"messages": [{"id": "msg111111", "threadId": "thr222222"}]})
        if "/messages/" in url:
            body = base64.urlsafe_b64encode(b"hello body").decode()
            return self._reply({"id": "msg111111", "threadId": "thr222222", "snippet": "hello", "payload": {"headers": [{"name": "Subject", "value": "S"}, {"name": "X-Other", "value": "no"}], "mimeType": "multipart/alternative",
                                                                                                           "parts": [{"mimeType": "text/html", "body": {"data": body}}, {"mimeType": "text/plain", "body": {"data": body}}]}})
        if "/export?" in url:
            return self._raw(b"doc text")
        if "alt=media" in url:
            return self._raw(b"plain file")
        if "/files?" in url:
            return self._reply({"files": [{"id": "file111111", "name": "n", "mimeType": "text/plain", "modifiedTime": "2026-11-01T00:00:00Z"}]})
        if "/files/" in url:
            return self._reply({"id": "file111111", "name": "n", "mimeType": "application/vnd.google-apps.document" if "doc" in url else "text/plain", "modifiedTime": "2026-11-01T00:00:00Z"})
        raise AssertionError(url)

    @staticmethod
    def _raw(body):
        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R(body)

    def _reply(self, body):
        return self._raw(json.dumps(body).encode())


def creds_for(account, service):
    return {**CREDS, "refresh_token": account}                                                  # the synthetic refresh token is the account name, so its access token reports that account


def google(**kw):
    fake = FakeGoogle(**kw)
    return G.Google(creds_for, fake), fake


class Reads(unittest.TestCase):
    def test_each_tool_returns_the_account_and_ids_and_trims_text(self):
        client, fake = google()
        out = client.gmail_search("personal", "from:x")
        self.assertEqual((out["account"], out["results"]), ("personal", [{"account": "personal", "id": "msg111111", "thread_id": "thr222222"}]))
        mail = client.gmail_read("bytalos", "msg111111")
        self.assertEqual((mail["account"], mail["id"], mail["headers"], mail["text"]), ("bytalos", "msg111111", {"subject": "S"}, "hello body"))      # plain text, not the html part; no header beyond the four
        found = client.drive_search("personal", "it's a test")
        self.assertEqual((found["results"][0]["id"], found["results"][0]["account"]), ("file111111", "personal"))
        self.assertIn("it%5C%27s", fake.requests[-1][1])                                                                                            # a quote in the query is escaped, not injected
        self.assertEqual(client.drive_read("bytalos", "file111111")["text"], "plain file")
        self.assertEqual(client.drive_read("bytalos", "docfile111")["text"], "doc text")                                                           # a Google Doc is exported as plain text
        big = G.Google(creds_for, FakeGoogle()).drive_read("personal", "file111111")
        self.assertFalse(big["truncated"])

    def test_every_google_request_is_a_get_except_the_one_token_exchange(self):
        client, fake = google()
        client.gmail_search("personal", "q")
        client.gmail_read("personal", "msg111111")
        client.drive_search("personal", "q")
        client.drive_read("personal", "file111111")
        methods = {(m, url == G.TOKEN_URL) for m, url, _ in fake.requests}
        self.assertEqual(methods, {("GET", False), ("POST", True)})
        self.assertTrue(all(h.get("Authorization", "").startswith("Bearer ") for m, u, h in fake.requests if m == "GET"))

    def test_there_is_no_send_modify_delete_label_upload_or_share_route_in_the_source(self):
        text = (ROOT / "lifeos/platform/google_read.py").read_text()
        code = "\n".join(line.split("#")[0] for line in text.splitlines())
        for word in ("/send", "/modify", "/trash", "/delete", "batchModify", "labels", "/upload", "permissions", "method=\"PUT\"", "method=\"DELETE\"", "method=\"PATCH\""):
            self.assertNotIn(word, code, word)
        self.assertEqual(sorted(set(re.findall(r"method=\"([A-Z]+)\"", code))), ["GET", "POST"])
        self.assertNotIn("gmail.modify", code)

    def test_an_omitted_or_unknown_account_is_refused_before_google_is_called(self):
        client, fake = google()
        for account, reason in ((None, "account_required"), ("", "account_required"), ("work", "unknown_account"), ("PERSONAL", "unknown_account"), (["personal"], "account_required")):   # a list is not an account name
            for call in (lambda a=account: client.gmail_search(a, "q"), lambda a=account: client.drive_read(a, "file111111")):
                with self.assertRaises(G.GoogleError) as raised:
                    call()
                self.assertEqual(raised.exception.reason, reason, account)
        self.assertEqual(fake.requests, [])

    def test_a_revoked_consent_a_narrow_scope_and_no_credential_have_fixed_reasons(self):
        for client, reason in ((google(revoked=True)[0], "consent_revoked"), (google(scope="https://www.googleapis.com/auth/gmail.compose")[0], "scope_missing"), (G.Google(lambda a, s: None, FakeGoogle()), "not_configured")):
            with self.assertRaises(G.GoogleError) as raised:
                client.gmail_search("personal", "q")
            self.assertEqual(raised.exception.reason, reason)

    def test_a_token_that_belongs_to_another_mailbox_is_wrong_account_and_no_data_is_read(self):
        fake = FakeGoogle()
        swapped = G.Google(lambda account, service: {**CREDS, "refresh_token": "bytalos" if account == "personal" else "personal"}, fake)
        for call in (lambda: swapped.gmail_search("personal", "q"), lambda: swapped.drive_read("bytalos", "file111111")):
            with self.assertRaises(G.GoogleError) as raised:
                call()
            self.assertEqual(raised.exception.reason, "wrong_account")
        self.assertFalse([u for m, u, h in fake.requests if "/messages" in u or "/files" in u])                    # refused after the who-am-i check, before any data call
        stranger = G.Google(lambda a, s: {**CREDS, "refresh_token": "nobody"}, FakeGoogle())
        with self.assertRaises(G.GoogleError) as raised:
            stranger.drive_search("personal", "q")
        self.assertEqual(raised.exception.reason, "wrong_account")
        self.assertEqual({G.OWNER_SHA256["personal"], G.OWNER_SHA256["bytalos"]} & {__import__("hashlib").sha256(e.encode()).hexdigest() for e in OWNERS.values()}, set(G.OWNER_SHA256.values()))

    def test_a_secret_in_a_body_is_refused_and_ids_are_validated(self):
        client, fake = google()
        with self.assertRaises(G.GoogleError) as raised:
            client.gmail_read("personal", "../x")
        self.assertEqual(raised.exception.reason, "bad_id")
        self.assertEqual(G._clean({"text": "short"})["truncated"], False)
        with self.assertRaises(G.GoogleError):
            G._clean({"text": "password = hunter2hunter2"})

    def test_the_token_never_appears_in_an_error_or_a_result(self):
        client, _ = google(revoked=True)
        try:
            client.drive_search("personal", "q")
        except G.GoogleError as error:
            self.assertNotIn("rtoken", repr(error) + str(error))
        ok, _ = google()
        self.assertNotIn("at-personal", json.dumps(ok.gmail_search("personal", "q")))


if __name__ == "__main__":
    unittest.main()
