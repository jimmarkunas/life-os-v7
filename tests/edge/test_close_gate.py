"""LIFE-47 S2 Edge gate: synthetic identities, fake tailscaled and a recording fake application only. No network, no Notion, no real ids."""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

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
            self.assertEqual(set(secrets), {"link", "reader", "config"})     # GET never receives the Notion token, only the read-only client
            for header in ("Cache-Control: no-store", "X-Frame-Options: DENY", "Content-Security-Policy:", "X-Content-Type-Options: nosniff", "Referrer-Policy: same-origin"):
                self.assertIn(header, text)                              # no-referrer would make the browser post `Origin: null`, which the origin check refuses

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


class RealClientGet(unittest.TestCase):
    """The installer's own policy builder and the real Notion Client, with only urllib's transport replaced: GET reads, never writes."""

    LEDGER, PIPELINE = "1" * 32, "2" * 32

    def setUp(self):
        from lifeos.edge.oracle import close_release as rel
        status = {"Self": {"ID": ORACLE, "DNSName": "edge.example.ts.net."}, "User": {"7": {"LoginName": LOGIN}},
                  "Peer": {"k1": {"ID": PHONE, "UserID": 7}, "k2": {"ID": MAC_MINI, "UserID": 7}}}
        d = tempfile.mkdtemp()
        self.policy, self.creds = os.path.join(d, "policy.json"), os.path.join(d, "creds")
        with open(self.policy, "w") as f:
            json.dump(rel.policy(status, "47443", LOGIN, self.LEDGER, "2222" * 8, [PHONE]), f)
        os.mkdir(self.creds)
        for name in ("link", "notion"):
            with open(os.path.join(self.creds, name), "wb") as f:
                f.write(b"synthetic-" + name.encode())
        self.sent = []

    def urlopen(self, request, timeout=None):
        self.sent.append((request.get_method(), request.full_url.split("/v1", 1)[1], request.get_header("Authorization")))
        body = {"id": "page", "properties": {}} if request.get_method() == "GET" else {"results": [], "has_more": False}
        return type("R", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: None, "read": lambda s: json.dumps(body).encode()})()

    def run_get(self, app):
        from unittest import mock
        out = io.BytesIO()
        with mock.patch("lifeos.platform.notion_client.urllib.request.urlopen", self.urlopen), mock.patch("lifeos.platform.notion_client.time.sleep"):
            gate.main(app, io.BytesIO(v2(RUNNER) + http(headers={"Host": "edge.example.ts.net:47443"})), out, whois_as(), lambda: 0, self.policy, self.creds)
        return int(out.getvalue().split(b" ")[1])

    def test_get_gets_the_canonical_ids_and_a_real_read_only_client_and_sends_only_reads(self):
        seen = {}

        def screen(req, who, secrets):
            reader = secrets["reader"]
            seen.update(config=secrets["config"], token="notion" in secrets, source=reader.source)
            reader.get_page("3" * 32)
            reader.query_data_source(body={"page_size": 1})
            self.assertRaises(PermissionError, reader.call, "PATCH", "/pages/" + "3" * 32, {})
            return 200, "text/html", b"ok", ()

        self.assertEqual(self.run_get(screen), 200)
        self.assertEqual(seen, {"config": {"ledger": self.LEDGER, "pipeline": self.PIPELINE}, "token": False, "source": self.LEDGER})
        self.assertEqual([(m, p) for m, p, _ in self.sent], [("GET", "/pages/" + "3" * 32), ("POST", f"/data_sources/{self.LEDGER}/query")])
        self.assertTrue(all(a == "Bearer synthetic-notion" for _, _, a in self.sent))      # the scoped token is used by the client, never handed to GET code

    def test_a_policy_without_the_ledger_id_fails_closed_and_sends_nothing(self):
        p = json.load(open(self.policy))
        del p["ledger"]
        json.dump(p, open(self.policy, "w"))
        self.assertEqual(self.run_get(lambda req, who, secrets: (200, "text/html", b"", ())), 500)
        self.assertEqual(self.sent, [])

    def test_the_policy_builder_refuses_bad_ids_and_wrong_nodes(self):
        from lifeos.edge.oracle import close_release as rel
        status = {"Self": {"ID": ORACLE, "DNSName": "edge.example.ts.net."}, "User": {"7": {"LoginName": LOGIN}, "8": {"LoginName": "x@example.com"}},
                  "Peer": {"a": {"ID": PHONE, "UserID": 7}, "b": {"ID": "nTAGGED", "UserID": 7, "Tags": ["tag:x"]}, "c": {"ID": "nOTHER", "UserID": 8}}}
        for ledger, nodes, code in (("xyz", [PHONE], "NOTION_ID_INVALID"), (self.LEDGER, [ORACLE], "NODE_REFUSED"), (self.LEDGER, ["nTAGGED"], "NODE_REFUSED"),
                                    (self.LEDGER, ["nOTHER"], "NODE_REFUSED"), (self.LEDGER, ["nUNKNOWN"], "NODE_REFUSED"), (self.LEDGER, [], "NO_NODES")):
            with self.assertRaises(rel.Refused) as caught:
                rel.policy(status, "47443", LOGIN, ledger, self.PIPELINE, nodes)
            self.assertTrue(str(caught.exception).startswith(code))


class Refresh(Gate):
    """Jim, Oct 24: closed rows leave the Daily Report without waiting for the hour. After a POST's 200, the app's refresh request becomes one workflow_dispatch
    made with the optional `github` credential, which the app never sees."""

    def with_token(self):
        with open(os.path.join(self.creds, "github"), "wb") as f:
            f.write(b"synthetic-github-token")

    def asking(self, status=200):
        def app(req, who, secrets):
            self.calls.append((req, who, secrets))
            secrets.get("after", []).append("hiring")
            return status, "text/html; charset=utf-8", b"<p>ok</p>", ()
        return app

    def test_a_verified_post_asks_once_after_the_response_and_the_app_never_sees_the_token(self):
        self.with_token()
        with mock.patch.object(gate, "refresh", return_value=["dispatched"]) as asked:
            code, status, _ = self.run_gate(v2(RUNNER) + http("POST", headers=POST_OK, body=b"a=b"), app=self.asking())
        self.assertEqual((code, status), (0, 200))
        asked.assert_called_once_with(b"synthetic-github-token", ["hiring"])
        self.assertNotIn(b"synthetic-github-token", repr(self.calls[0][2]).encode())

    def test_no_credential_a_get_or_a_refused_post_asks_nothing(self):
        with mock.patch.object(gate, "refresh") as asked:
            self.run_gate(v2(RUNNER) + http("POST", headers=POST_OK, body=b"a=b"), app=self.asking())       # no github credential
            self.with_token()
            self.run_gate(v2(RUNNER) + http(path="/close?l=abc"), app=self.asking())                          # GET: no `after` list at all
            self.run_gate(v2(RUNNER) + http("POST", headers=POST_OK, body=b"a=b"), app=self.asking(409))      # not a success
        asked.assert_not_called()
        self.assertNotIn("after", self.calls[1][2])

    def test_the_drive_credential_reaches_a_post_only_and_never_a_get(self):
        with open(os.path.join(self.creds, "gdrive"), "wb") as f:
            f.write(b'{"client_id": "synthetic"}\n')
        self.run_gate(v2(RUNNER) + http("POST", headers=POST_OK, body=b"a=b"), app=self.asking(409))
        self.run_gate(v2(RUNNER) + http(path="/close?l=abc"), app=self.asking())
        self.assertEqual(self.calls[0][2]["gdrive"], b'{"client_id": "synthetic"}')
        self.assertNotIn("gdrive", self.calls[1][2])
        os.remove(os.path.join(self.creds, "gdrive"))
        self.run_gate(v2(RUNNER) + http("POST", headers=POST_OK, body=b"a=b"), app=self.asking(409))
        self.assertNotIn("gdrive", self.calls[2][2])                                           # absent: Build resume keeps the Notion upload

    def test_the_settings_credential_reaches_only_the_alerts_screen_on_get_and_post(self):
        with open(os.path.join(self.creds, "ghvars"), "wb") as f:
            f.write(b"github_pat_synthetic\n")
        self.run_gate(v2(RUNNER) + http(path="/alerts"), app=self.asking())
        self.run_gate(v2(RUNNER) + http("POST", path="/alerts", headers=POST_OK, body=b"a=b"), app=self.asking(409))
        self.run_gate(v2(RUNNER) + http(path="/close?l=abc"), app=self.asking())
        self.run_gate(v2(RUNNER) + http("POST", headers=POST_OK, body=b"a=b"), app=self.asking(409))
        self.assertEqual(self.calls[0][2]["ghvars"], b"github_pat_synthetic")
        self.assertEqual(self.calls[1][2]["ghvars"], b"github_pat_synthetic")
        self.assertNotIn("ghvars", self.calls[2][2])                                          # no other screen ever sees it
        self.assertNotIn("ghvars", self.calls[3][2])
        os.remove(os.path.join(self.creds, "ghvars"))
        self.run_gate(v2(RUNNER) + http(path="/alerts"), app=self.asking())
        self.assertNotIn("ghvars", self.calls[4][2])                                          # absent: the screen is read-only

    def opener(self, runs, fail=False):
        sent = []

        class Response:
            def __init__(self, data):
                self.data = data

            def read(self):
                return self.data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def open_(req, timeout):
            sent.append((req.get_method(), req.full_url, req.get_header("Authorization"), req.data and json.loads(req.data)))
            if fail:
                raise OSError("down")
            return Response(json.dumps({"workflow_runs": runs}).encode() if req.get_method() == "GET" else b"")
        return sent, open_

    def test_one_dispatch_for_the_hiring_region_and_unknown_names_are_ignored(self):
        sent, open_ = self.opener([{"status": "completed"}, {"status": "in_progress"}])
        self.assertEqual(gate.refresh(b"tok", ["hiring", "hiring", "anything"], open_), ["dispatched"])
        self.assertEqual(sent[1], ("POST", gate.API + "/workflows/domains.yml/dispatches", "Bearer tok", {"ref": "main", "inputs": {"refresh": "hiring", "live": "true"}}))
        self.assertEqual(len(sent), 2)
        sent, open_ = self.opener([])
        self.assertEqual(gate.refresh(b"tok", ["recruiters"], open_), ["dispatched"])
        self.assertEqual(sent[1][3], {"ref": "main", "inputs": {"refresh": "recruiters", "live": "true"}})
        self.assertEqual(sent[0][1], gate.API + "/runs?per_page=20")                       # every workflow's runs: Recruiters and Amazon share the domains group

    def test_every_refresh_name_is_a_domains_refresh_option_and_always_writes(self):
        import yaml
        from pathlib import Path
        inputs = yaml.safe_load(Path(".github/workflows/domains.yml").read_text())[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(["none", *gate.REFRESH], inputs["refresh"]["options"])         # an app's name the workflow lacks would dispatch a 422
        for name, body in gate.REFRESH.items():
            self.assertEqual(body, {"refresh": name, "live": "true"})                    # a dispatch without live is a dry run that redraws nothing

    def test_a_waiting_domains_run_is_never_replaced_and_a_failure_is_silent(self):
        for status in gate.WAITING:
            sent, open_ = self.opener([{"status": status}])
            self.assertEqual(gate.refresh(b"tok", ["hiring"], open_), ["waiting"])
            self.assertEqual([m for m, *_ in sent], ["GET"])
        sent, open_ = self.opener([], fail=True)
        self.assertEqual(gate.refresh(b"tok", ["hiring"], open_), ["failed"])
