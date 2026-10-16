"""LIFE-47 S2 Edge gate: synthetic identities, fake tailscaled and a recording fake application only. No network, no Notion, no real ids."""
import io
import json
import os
import tempfile
import unittest

from lifeos.edge import close_gate as gate
from tests.imessage.test_alerts import RUNNER, OTHER, v2

HOST = "edge.example.ts.net:47443"
LOGIN = "owner@example.com"
PHONE, MACBOOK, ORACLE, MAC_MINI = "nPHONE1CNTRL", "nMACBOOK1CNTRL", "nORACLE1CNTRL", "nMINI1CNTRL"
WHOIS = {RUNNER: {"Node": {"StableID": PHONE}, "UserProfile": {"LoginName": LOGIN}}}


def whois_as(node=PHONE, login=LOGIN, tags=None):
    return lambda ip: {"Node": {"StableID": node, **({"Tags": tags} if tags else {})}, "UserProfile": {"LoginName": login}}


def http(method="GET", path="/close", headers=None, body=b""):
    h = {"Host": HOST, **({"Content-Length": str(len(body))} if body else {}), **(headers or {})}
    return (f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n").encode() + body


POST_OK = {"Origin": "https://" + HOST, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/x-www-form-urlencoded"}


class Gate(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.policy = os.path.join(d, "policy.json")
        with open(self.policy, "w") as f:
            json.dump({"host": HOST, "login": LOGIN, "nodes": [PHONE, MACBOOK]}, f)
        self.creds = os.path.join(d, "creds")
        os.mkdir(self.creds)
        for name, value in (("link", b"synthetic-link-secret"), ("notion", b"synthetic-notion-token")):
            with open(os.path.join(self.creds, name), "wb") as f:
                f.write(value)
        self.calls = []

    def app(self, req, who, secrets):
        self.calls.append((req, who, secrets))
        return 200, "text/html; charset=utf-8", b"<p>ok</p>", ()

    def run_gate(self, raw, lookup=None, uid=0, app=None):
        out = io.BytesIO()
        code = gate.main(app or self.app, io.BytesIO(raw), out, lookup or whois_as(), lambda: uid, self.policy, self.creds, client=lambda t, p: type("C", (), {"source": "ds"})())
        text = out.getvalue().decode("latin-1")
        return code, (int(text.split(" ")[1]) if text else None), text

    def test_an_approved_device_reaches_the_app_and_get_carries_no_notion_credential(self):
        for node in (PHONE, MACBOOK):
            self.calls.clear()
            code, status, text = self.run_gate(v2(RUNNER) + http(path="/close?l=abc"), whois_as(node))
            self.assertEqual((code, status), (0, 200))
            req, who, secrets = self.calls[0]
            self.assertEqual((req["method"], req["path"], req["query"], who), ("GET", "/close", {"l": "abc"}, {"login": LOGIN, "node": node}))
            self.assertEqual(set(secrets), {"link", "reader"})               # GET never receives the Notion token, only the read-only client
            for header in ("Cache-Control: no-store", "X-Frame-Options: DENY", "Content-Security-Policy:", "X-Content-Type-Options: nosniff"):
                self.assertIn(header, text)

    def test_get_reads_the_current_opportunity_through_a_read_only_client_and_writes_nothing(self):
        class Notion:                                                   # a fake Notion client: one opportunity, every call recorded
            source = "ds-ledger"

            def __init__(self, stage):
                self.stage, self.log = stage, []

            def call(self, method, path, body=None):
                self.log.append((method, path))
                return {"id": "page-1", "stage": self.stage}

            def get_page(self, page_id):
                return self.call("GET", f"/pages/{page_id}")

            def query_data_source(self, source_id=None, body=None):
                return self.call("POST", f"/data_sources/{source_id or self.source}/query", body)

        def screen(req, who, secrets):                                  # what close_app's GET must do: read live state, refuse a stale link
            reader = secrets["reader"]
            live = reader.get_page("page-1")["stage"]
            reader.query_data_source(body={"filter": {}})
            for write in (lambda: reader.call("PATCH", "/pages/page-1", {}), lambda: reader.call("POST", "/pages", {})):
                self.assertRaises(PermissionError, write)
            self.assertFalse(hasattr(reader, "update_page_properties") or hasattr(reader, "call_once") or hasattr(reader, "create"))
            return (409, "text/plain", b"stale", ()) if live != req["query"]["stage"] else (200, "text/html", b"current", ())

        for stage, want in (("Interview", 200), ("Offer", 409)):
            notion = Notion(stage)
            out = io.BytesIO()
            gate.main(screen, io.BytesIO(v2(RUNNER) + http(path="/close?stage=Interview")), out, whois_as(), lambda: 0, self.policy, self.creds,
                      client=lambda token, policy: (self.assertEqual(token, b"synthetic-notion-token"), notion)[1])
            self.assertEqual(int(out.getvalue().split(b" ")[1]), want)
            self.assertEqual(notion.log, [("GET", "/pages/page-1"), ("POST", "/data_sources/ds-ledger/query")])      # reads only: zero writes reached Notion

    def test_a_same_origin_form_post_gets_the_notion_credential_and_the_parsed_form(self):
        body = b"reason=Rejected&confirm=yes&csrf=t"
        self.assertEqual(self.run_gate(v2(RUNNER) + http("POST", headers=POST_OK, body=body))[1], 200)
        req, _, secrets = self.calls[0]
        self.assertEqual((secrets["notion"], req["form"]), (b"synthetic-notion-token", {"reason": "Rejected", "confirm": "yes", "csrf": "t"}))

    def test_openclaw_or_any_local_non_root_peer_is_refused_before_reading(self):
        self.assertEqual(self.run_gate(v2(RUNNER) + http(), uid=1001), (1, None, ""))
        self.assertEqual(self.calls, [])

    def test_a_missing_or_forged_proxy_header_is_refused(self):
        for raw in (http(), b"PROXY TCP4 100.64.0.20 127.0.0.1 1 2\r\n" + http(), v2(RUNNER, cmd=0x20) + http()):
            self.assertEqual(self.run_gate(raw), (1, None, ""))
        self.assertEqual(self.calls, [])

    def test_wrong_identities_are_refused_with_403_and_nothing_reaches_the_app(self):
        for lookup in (whois_as(ORACLE), whois_as(MAC_MINI), whois_as(login="someone@example.com"), whois_as(tags=["tag:v7-runner"]),
                       lambda ip: {}, lambda ip: (_ for _ in ()).throw(OSError())):
            self.assertEqual(self.run_gate(v2(OTHER) + http(), lookup)[1], 403)
        self.assertEqual(self.calls, [])

    def test_bad_requests_are_refused_without_reaching_the_app(self):
        cases = [(http(headers={"Host": "evil.example:47443"}), 400), (b"GET /close HTTP/1.0\r\nHost: " + HOST.encode() + b"\r\n\r\n", 400),
                 (http(headers={"host": HOST}), 400),                                                   # a duplicate Host header
                 (http("POST", headers={**POST_OK, "Transfer-Encoding": "chunked"}), 400),
                 (http(body=b"x=1"), 400),                                                              # a GET with a body
                 (http("PUT", headers=POST_OK, body=b"a=1"), 405),
                 (http("POST", headers={**POST_OK, "Origin": "https://evil.example"}, body=b"a=1"), 403),
                 (http("POST", headers={**POST_OK, "Sec-Fetch-Site": "cross-site"}, body=b"a=1"), 403),
                 (http("POST", headers={**POST_OK, "Content-Type": "application/json"}, body=b"{}"), 403),
                 (http("POST", headers={k: v for k, v in POST_OK.items() if k != "Origin"}, body=b"a=1"), 403),
                 (http("POST", headers={**POST_OK, "Content-Length": str(gate.MAX_BODY + 1)}), 400)]
        for raw, want in cases:
            self.assertEqual(self.run_gate(v2(RUNNER) + raw)[1], want, raw[:60])
        self.assertEqual(self.calls, [])

    def test_an_application_failure_is_a_bare_500(self):
        def boom(req, who, secrets):
            raise RuntimeError("SECRET DETAIL")
        _, status, text = self.run_gate(v2(RUNNER) + http(), app=boom)
        self.assertEqual(status, 500)
        self.assertNotIn("SECRET", text)


if __name__ == "__main__":
    unittest.main()
