"""the read-only Todoist sign-in helper."""

import io
import json
import unittest
import urllib.parse

from lifeos.platform import todoist_consent_readonly as T
from tests.kit import shared

FAKE_TOKEN = "fake-todoist-token-not-real"
Reply = shared.Reply


def endpoint(reply, seen=None):
    def opener(request, timeout=30):
        if seen is not None:
            seen.append((request.full_url, request.data))
        return Reply(reply)

    return opener


class Pipe(io.StringIO):
    def isatty(self):
        return False


class Helper(unittest.TestCase):
    def go(self, reply, code="the-code"):
        out, tty, seen = Pipe(), io.StringIO(), []
        result = T.run(
            "cid",
            "fake-secret",
            out,
            tty,
            opener=endpoint(reply, seen),
            server=object(),
            catcher=lambda server, state: code,
        )
        return result, out.getvalue(), tty.getvalue(), seen

    def test_the_link_asks_for_read_access_only_and_a_long_lived_token_goes_to_the_pipe(self):
        result, out, tty, seen = self.go({"access_token": FAKE_TOKEN, "token_type": "Bearer"})
        self.assertEqual(
            (result["outcome"], result["scopes_granted"], result["stored"]), ("stored", ["data:read"], "pipe")
        )
        self.assertEqual(out, FAKE_TOKEN + "\n")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(tty.splitlines()[1]).query)
        self.assertEqual(
            (query["scope"], query["redirect_uri"], query["response_type"]), (["data:read"], [T.REDIRECT], ["code"])
        )
        self.assertEqual(seen[0][0], T.TOKEN_URL)
        self.assertNotIn(FAKE_TOKEN, tty)  # the screen never shows the token

    def test_a_rotating_token_is_refused_and_nothing_is_written(self):
        result, out, tty, _ = self.go({"access_token": FAKE_TOKEN, "refresh_token": "r", "expires_in": 3600})
        self.assertEqual((result["outcome"], result["code"], out), ("refused", "DENY_ROTATING_TOKEN", ""))
        self.assertNotIn(FAKE_TOKEN, tty)

    def test_more_than_read_access_is_refused(self):
        for scope in ("data:read_write", "data:read,data:delete", "task:add"):
            result, out, _, _ = self.go({"access_token": FAKE_TOKEN, "scope": scope})
            self.assertEqual((result["code"], out), ("DENY_UNSUPPORTED_ACTION", ""), scope)

    def test_a_reply_with_the_wrong_state_stores_nothing_and_a_terminal_is_refused(self):
        result, out, _, seen = self.go({"access_token": FAKE_TOKEN}, code=None)
        self.assertEqual((result["code"], out, seen), ("DENY_PRINCIPAL", "", []))
        tty = io.StringIO()
        screen = io.StringIO()
        screen.isatty = lambda: True
        self.assertEqual(
            T.run("cid", "s", screen, tty, server=object(), catcher=lambda *a: "c")["code"], "DENY_SECRET_EXPOSURE"
        )
        self.assertEqual(screen.getvalue(), "")

    def test_the_receipt_never_holds_a_secret(self):
        result, _, _, _ = self.go({"access_token": FAKE_TOKEN})
        self.assertNotIn(FAKE_TOKEN, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
