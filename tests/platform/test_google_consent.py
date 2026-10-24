"""The one-time Drive sign-in: drive.file only, PKCE, state checked, the credential only ever written to a pipe. Synthetic values only."""
import base64
import hashlib
import http.server
import io
import json
import threading
import unittest
import urllib.parse
import urllib.request

from lifeos.platform import gdrive, google_consent as G


class Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.body).encode()


class Consent(unittest.TestCase):
    def test_it_is_standalone_and_asks_for_the_same_scope_the_client_uses(self):
        self.assertEqual((G.SCOPE, G.TOKEN_URL), (gdrive.SCOPE, gdrive.TOKEN_URL))
        import pathlib
        imports = [line for line in pathlib.Path(G.__file__).read_text().splitlines() if line.startswith(("import ", "from "))]
        self.assertFalse([line for line in imports if "lifeos" in line or "webbrowser" in line])          # runs by path under python3 -I, opens no browser

    def test_the_sign_in_asks_for_drive_file_only_offline_with_pkce(self):
        url = G.auth_url("cid", "http://127.0.0.1:8765", "st", "verifier")
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}
        self.assertEqual(q["scope"], "https://www.googleapis.com/auth/drive.file")
        self.assertEqual((q["access_type"], q["prompt"], q["state"], q["code_challenge_method"]), ("offline", "consent", "st", "S256"))
        self.assertEqual(q["code_challenge"], base64.urlsafe_b64encode(hashlib.sha256(b"verifier").digest()).rstrip(b"=").decode())

    def test_the_exchange_returns_the_drive_credential_and_refuses_a_wider_or_missing_grant(self):
        sent = []
        opener = lambda req, timeout: sent.append(req.data) or Reply({"scope": gdrive.SCOPE, "refresh_token": "rt"})   # noqa: E731
        raw = G.exchange("cid", "cs", "code", "http://127.0.0.1:1", "ver", opener)
        self.assertEqual(gdrive.credential(raw), ("cid", "cs", "rt"))
        self.assertIn(b"code_verifier=ver", sent[0])
        for bad in ({"scope": gdrive.SCOPE}, {"scope": "https://www.googleapis.com/auth/drive", "refresh_token": "rt"}):
            with self.assertRaises(SystemExit):
                G.exchange("cid", "cs", "code", "r", "v", lambda req, timeout, b=bad: Reply(b))

    def test_only_a_redirect_with_the_right_state_yields_the_code(self):
        for state, want in (("good", "abc"), ("forged", None)):
            server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
            port = server.server_port
            threading.Thread(target=lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/?state={state}&code=abc").read(), daemon=True).start()
            self.assertEqual(G.catch(server, "good"), want)
            server.server_close()

    def test_the_credential_is_never_printed_on_a_terminal(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        out, tty = Terminal(), io.StringIO()
        self.assertEqual(G.main((), stdout=out, tty=tty), 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("never printed on screen", tty.getvalue())


if __name__ == "__main__":
    unittest.main()
