"""LIFE-423 / GWT-T13-01 to GWT-T13-03: the read-only Google sign-in helper. Fake allowlist, fake token endpoint, loopback only; no Google, no browser, no real credential."""
import io
import json
import pathlib
import threading
import unittest
import urllib.parse
import urllib.request
import http.server

from lifeos.platform import google_consent as base, google_consent_readonly as R

SECRET = "fake-client-secret-not-real"
ALLOW = [{"account": "acct-a-gmail", "tenant": "TENANT-A-TEST", "service": "gmail", "label": "INBOX", "size_cap": 1000},
         {"account": "acct-a-drive", "tenant": "TENANT-A-TEST", "service": "drive", "label": "Fake folder", "size_cap": 500},
         {"account": "acct-a-cal", "tenant": "TENANT-A-TEST", "service": "calendar", "label": "primary", "size_cap": 200}]
GOOD = {"gmail": "https://www.googleapis.com/auth/gmail.readonly", "drive": "https://www.googleapis.com/auth/drive.metadata.readonly",
        "calendar": "https://www.googleapis.com/auth/calendar.readonly"}
WRITE = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/gmail.modify",
         "https://www.googleapis.com/auth/gmail.send", "https://mail.google.com/", "https://www.googleapis.com/auth/calendar",
         "https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/gmail.compose"]


class Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def token_endpoint(scope, refresh="fake-refresh-token", seen=None):
    def opener(request, timeout=30):
        if seen is not None:
            seen.append(request.full_url)
        return Reply({"scope": scope, "refresh_token": refresh} if refresh else {"scope": scope})
    return opener


def server():
    return http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)


def reply_with(state_ok=True):
    """A catcher that plays Google's redirect back to the loopback port (a fake consent endpoint), then uses the real loopback catcher."""
    def catcher(srv, state):
        query = urllib.parse.urlencode({"state": state if state_ok else "wrong", "code": "fake-code"})
        threading.Thread(target=lambda: urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/?{query}", timeout=10).read(), daemon=True).start()
        return base.catch(srv, state)
    return catcher


def go(account="acct-a-gmail", service="gmail", scope=None, **kw):
    out, tty = io.StringIO(), io.StringIO()
    srv = server()
    try:
        receipt = R.run(ALLOW, account, service, "fake-client-id", SECRET, out, tty, opener=kw.pop("opener", token_endpoint(GOOD.get(service, "") if scope is None else scope)),
                        server=srv, catcher=kw.pop("catcher", reply_with()), **kw)
    finally:
        srv.server_close()
    return receipt, out.getvalue(), tty.getvalue()


def link_of(tty_text):
    return next(line for line in tty_text.splitlines() if line.startswith("https://accounts.google.com/"))


class LinkScopes(unittest.TestCase):
    def test_gwt_t13_01_an_allowlisted_account_gets_one_link_with_exactly_the_read_only_scope(self):
        for entry in ALLOW:
            with self.subTest(service=entry["service"]):
                receipt, out, tty = go(entry["account"], entry["service"])
                links = [line for line in tty.splitlines() if line.startswith("https://")]
                self.assertEqual(len(links), 1)
                q = urllib.parse.parse_qs(urllib.parse.urlparse(links[0]).query)
                self.assertEqual(q["scope"], [GOOD[entry["service"]]])
                self.assertEqual((q["code_challenge_method"], q["access_type"]), (["S256"], ["offline"]))     # the reused PKCE link
                self.assertEqual((receipt["outcome"], receipt["scopes_granted"]), ("stored", [GOOD[entry["service"]]]))

    def test_the_scope_set_is_exactly_gmail_drive_metadata_and_calendar_readonly(self):
        self.assertEqual({k: v for k, v in R.READONLY_SCOPES.items()}, {k: (v,) for k, v in GOOD.items()})
        for scopes in R.READONLY_SCOPES.values():
            self.assertTrue(all(s.endswith(".readonly") for s in scopes))


class Refusals(unittest.TestCase):
    def refused(self, receipt, out, tty, code):
        self.assertEqual((receipt["outcome"], receipt["code"], receipt["stored"], receipt["scopes_granted"]), ("refused", code, "nothing", []))
        self.assertEqual(out, "")                                                  # nothing was stored
        self.assertNotIn("https://", tty)                                          # and no link was produced
        self.assertNotIn(SECRET, tty)

    def test_gwt_t13_02_an_account_not_on_the_allowlist_gets_no_link(self):
        self.refused(*go("acct-b-gmail", "gmail"), "DENY_ACCOUNT_AMBIGUOUS")
        self.refused(*go("acct-a-gmail", "drive"), "DENY_ACCOUNT_AMBIGUOUS")        # listed for another service
        self.refused(*go("", "gmail"), "DENY_INPUT")
        self.refused(*go("acct-a-gmail", "composio"), "DENY_INPUT")

    def test_a_broken_or_duplicate_allowlist_entry_never_authorizes(self):
        bad = [{**ALLOW[0], "size_cap": 0}, {**ALLOW[0], "size_cap": True}, {**ALLOW[0], "label": " "}, {k: v for k, v in ALLOW[0].items() if k != "tenant"}]
        for entry in bad:
            self.assertEqual(R.plan([entry], "acct-a-gmail", "gmail")["code"], "DENY_ACCOUNT_AMBIGUOUS", entry)
        self.assertEqual(R.plan([ALLOW[0], dict(ALLOW[0])], "acct-a-gmail", "gmail")["code"], "DENY_ACCOUNT_AMBIGUOUS")
        self.assertEqual(R.plan("not a list", "acct-a-gmail", "gmail")["code"], "DENY_INPUT")

    def test_gwt_t13_02_any_write_or_delete_scope_is_refused_before_a_link(self):
        for scope in WRITE:
            with self.subTest(scope=scope):
                self.refused(*go(scopes=[scope]), "DENY_UNSUPPORTED_ACTION")
        for scopes in ([GOOD["gmail"], WRITE[2]], [GOOD["gmail"], GOOD["drive"]], [], "x"):
            with self.subTest(scopes=scopes):
                self.refused(*go(scopes=scopes), "DENY_UNSUPPORTED_ACTION")

    def test_a_reply_that_grants_more_than_read_only_stores_nothing(self):
        for granted in (GOOD["gmail"] + " " + WRITE[2], WRITE[2], GOOD["drive"], ""):
            with self.subTest(granted=granted):
                receipt, out, tty = go(scope=granted)
                self.assertEqual((receipt["outcome"], receipt["code"], receipt["stored"], out), ("refused", "DENY_UNSUPPORTED_ACTION", "nothing", ""))

    def test_a_reply_without_a_refresh_token_or_with_the_wrong_state_stores_nothing(self):
        receipt, out, tty = go(opener=token_endpoint(GOOD["gmail"], refresh=None))
        self.assertEqual((receipt["code"], out), ("DENY_INPUT", ""))
        receipt, out, tty = go(catcher=lambda srv, state: None)                     # the catcher found no code for this state
        self.assertEqual((receipt["code"], receipt["stored"], out), ("DENY_PRINCIPAL", "nothing", ""))

    def test_it_refuses_to_print_the_credential_on_a_terminal(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True
        tty = io.StringIO()
        def never(srv, state):
            raise AssertionError("no sign-in may start when the output is a terminal")
        receipt = R.run(ALLOW, "acct-a-gmail", "gmail", "cid", SECRET, Tty(), tty, server=server(), catcher=never)
        self.assertEqual((receipt["outcome"], receipt["code"]), ("refused", "DENY_SECRET_EXPOSURE"))
        self.assertNotIn("https://", tty.getvalue())


class CredentialAndReceipt(unittest.TestCase):
    def test_gwt_t13_03_the_credential_goes_only_to_the_pipe_and_no_secret_is_shown(self):
        seen = []
        receipt, out, tty = go(opener=token_endpoint(GOOD["gmail"], seen=seen))
        self.assertEqual(json.loads(out), {"client_id": "fake-client-id", "client_secret": SECRET, "refresh_token": "fake-refresh-token"})     # the pipe only
        self.assertEqual(seen, [base.TOKEN_URL])                                   # the (fake) token call, and no other request from the helper
        for shown in (tty, json.dumps(receipt)):
            for secret in (SECRET, "fake-refresh-token", "fake-code"):
                self.assertNotIn(secret, shown)
        self.assertEqual((receipt["account"], receipt["tenant"], receipt["service"], receipt["label"], receipt["size_cap"]),
                         ("acct-a-gmail", "TENANT-A-TEST", "gmail", "INBOX", 1000))

    def test_the_helper_opens_no_browser_and_imports_nothing_that_could(self):
        text = pathlib.Path(R.__file__).read_text()
        self.assertNotIn("webbrowser", text)
        self.assertNotIn("subprocess", text)

    def test_the_full_scope_script_is_exactly_as_before(self):
        before = (base.SCOPE, base.AUTH_URL, base.TOKEN_URL)
        go()
        self.assertEqual((base.SCOPE, base.AUTH_URL, base.TOKEN_URL), before)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(base.auth_url("c", "http://127.0.0.1:1", "s", "v")).query)
        self.assertEqual(q["scope"], ["https://www.googleapis.com/auth/drive"])


class Cli(unittest.TestCase):
    def test_a_missing_allowlist_or_unlisted_account_is_refused_before_any_prompt(self):
        tty, out = io.StringIO(), io.StringIO()
        self.assertEqual(R.main(["--account", "acct-a-gmail", "--service", "gmail", "--allowlist", "/nonexistent.json"], out, tty), 2)
        path = pathlib.Path(self.id() + ".json")
        path.write_text(json.dumps(ALLOW))
        try:
            self.assertEqual(R.main(["--account", "acct-x", "--service", "gmail", "--allowlist", str(path)], out, tty), 1)
        finally:
            path.unlink()
        self.assertEqual(out.getvalue(), "")
        self.assertIn("DENY_ACCOUNT_AMBIGUOUS", tty.getvalue())


if __name__ == "__main__":
    unittest.main()
