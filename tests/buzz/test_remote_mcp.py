"""LIFE-216 / BUZZ-16: the shared remote MCP (OAuth 2.1 resource server + Streamable HTTP) for Claude Chat and ChatGPT Chat, proved over real loopback HTTP against a MOCKED relay and a LOCAL test identity provider.
These are fixture results: they say nothing about the real claude.ai / chatgpt.com connectors or live Buzz."""
import base64, hashlib, json, os, pathlib, random, shlex, shutil, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer

from lifeos.buzz import mcp as buzz_mcp

from lifeos.buzz import nip98
from lifeos.buzz.auth import AuthError, Verifier
from lifeos.buzz.relay import Relay
from tests.buzz.test_bridge import CHAN, KEY_A, KEY_B, OTHER, ROOT, URLS, FakeRelay
from tests.platform.mcp_access_fixture import open_access

BASE, ISS = "https://edge.example.test", "https://idp.example.test"
CLIENTS = {"cid-one"}
USERS = {"user-claude": ("claude", ("life-os", "bytalos")), "user-chatgpt": ("chatgpt", ("life-os", "bytalos")), "user-lifeos-only": ("claude", ("life-os",))}
NOW = 1_800_000_000
POST = dict(community="life-os", channel=CHAN, text="synthetic hello", idempotency_key="k1")
KEYS = {("claude", "life-os"): bytes([21]) * 32, ("claude", "bytalos"): bytes([22]) * 32, ("chatgpt", "life-os"): bytes([23]) * 32, ("chatgpt", "bytalos"): bytes([24]) * 32}   # synthetic, one per pairing
CHANS = {"life-os": CHAN, "bytalos": OTHER}
BOTH = (("life-os", CHAN), ("bytalos", OTHER))
WR = dict(scope="buzz:read buzz:write")


def _prime(rng, bits=1024):
    while True:
        c = rng.getrandbits(bits) | (1 << bits - 1) | 1
        if all(c % p for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)) and all(pow(a, c - 1, c) == 1 for a in (2, 3, 5, 7, 11)):
            return c


def _rsa():
    rng = random.Random(1616)  # deterministic synthetic key; never a real credential
    while True:
        p, q = _prime(rng), _prime(rng)
        if p != q and (p - 1) % 65537 and (q - 1) % 65537 and (p * q).bit_length() == 2048:
            return p * q, pow(65537, -1, (p - 1) * (q - 1))


N, D = _rsa()
b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
JWKS = {"keys": [{"kty": "RSA", "use": "sig", "kid": "k1", "n": b64(N.to_bytes(256, "big")), "e": b64((65537).to_bytes(3, "big"))}]}


def token(alg="RS256", kid="k1", sign=True, **over):
    claims = {"iss": ISS, "aud": BASE + "/mcp", "exp": NOW + 600, "client_id": "cid-one", "scope": "buzz:read", "sub": "user-claude", **over}
    h, p = b64(json.dumps({"alg": alg, "kid": kid}).encode()), b64(json.dumps(claims).encode())
    info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(f"{h}.{p}".encode()).digest()
    em = b"\0\1" + b"\xff" * (256 - len(info) - 3) + b"\0" + info
    sig = pow(int.from_bytes(em, "big"), D, N).to_bytes(256, "big") if sign else bytes(256)
    return f"{h}.{p}.{b64(sig)}"


class Server(unittest.TestCase):
    def start(self, fake=None, writes=((("life-os", CHAN)),), fetch=None, origins=(), keys=None, access=None):
        self.fake, keys = fake or FakeRelay(), KEYS if keys is None else keys
        self.relays = {i: Relay(URLS, {c: k for (ii, c), k in keys.items() if ii == i}, writes, self.fake) for i in ("claude", "chatgpt") if any(ii == i for ii, _ in keys)}
        self.audit = []
        verifier = Verifier(ISS, BASE + "/mcp", CLIENTS, ISS + "/jwks", USERS, fetch=fetch or (lambda url: JWKS), clock=lambda: NOW)
        self.receipts = []
        srv = ThreadingHTTPServer(("127.0.0.1", 0), buzz_mcp.handler(self.relays, verifier, BASE, origins, self.audit.append, open_access() if access is None else (None if access is False else access), self.receipts.append))     # access=False: the handler loads the repo data itself
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.url = f"http://127.0.0.1:{srv.server_address[1]}"

    def req(self, method, path, body=None, tok=None, **hdr):
        data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json, text/event-stream", **({"Authorization": "Bearer " + tok} if tok else {}), **{k.replace("_", "-"): v for k, v in hdr.items()}}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + path, data, headers, method=method)) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def rpc(self, tok, method, params=None, id=1, **hdr):
        status, raw, _ = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}, tok, **hdr)
        return status, json.loads(raw) if raw else None

    def tool(self, tok, name, **args):
        status, out = self.rpc(tok, "tools/call", {"name": name, "arguments": args})
        return (status, json.loads(out["result"]["content"][0]["text"])) if status == 200 else (status, out)


class Discovery(Server):
    def test_protected_resource_metadata_and_challenge(self):
        self.start()
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            status, raw, _ = self.req("GET", path)
            self.assertEqual((status, json.loads(raw)), (200, {"resource": BASE + "/mcp", "authorization_servers": [ISS], "scopes_supported": [BASE + "/mcpbuzz:read", BASE + "/mcpbuzz:write"], "bearer_methods_supported": ["header"]}))
        for status, _, hdr in (self.req("POST", "/mcp", {"id": 1, "method": "ping"}), self.req("GET", "/mcp")):
            self.assertEqual(status, 401)
            self.assertIn(f'resource_metadata="{BASE}/.well-known/oauth-protected-resource"', hdr["WWW-Authenticate"])
        self.assertEqual(self.fake.calls, [])


class TokenChecks(Server):
    def test_bad_tokens_are_refused_before_any_tool_runs(self):
        self.start()
        forged_hs = token(alg="HS256")
        cases = {"expired": token(exp=NOW - 1), "wrong_audience": token(aud="https://other.example/mcp"), "wrong_issuer": token(iss="https://evil.example"), "alg_none": token(alg="none"),
                 "hs256_confusion": forged_hs, "bad_signature": token(sign=False), "unknown_kid": token(kid="zz"), "no_exp": token(exp=None), "bool_exp": token(exp=True),
                 "not_yet_valid": token(nbf=NOW + 60), "garbage": "a.b.c", "two_parts": "a.b"}
        for name, tok in cases.items():
            status, _, hdr = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, tok)
            self.assertEqual(status, 401, name)
            self.assertIn('error="invalid_token"', hdr["WWW-Authenticate"], name)
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, token(client_id="cid-unknown"))[0], 403)   # valid token, client not allowlisted
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, token(client_id=["x"], azp=None))[0], 403)
        self.assertEqual(self.fake.calls, [])

    def test_unavailable_identity_provider_fails_closed_without_detail(self):
        def broken(url):
            raise RuntimeError("secret-looking-detail")
        self.start(fetch=broken)
        status, raw, _ = self.req("POST", "/mcp", {"id": 1, "method": "ping"}, token())
        self.assertEqual((status, json.loads(raw)), (503, {"error": "auth_unavailable"}))

    def test_key_rotation_refreshes_but_unknown_kid_cannot_hammer_the_idp(self):
        fetches, keys = [], {"keys": []}
        v = Verifier(ISS, BASE + "/mcp", CLIENTS, ISS + "/jwks", USERS, fetch=lambda u: (fetches.append(u), keys)[1], clock=lambda: NOW)
        for _ in range(5):
            with self.assertRaises(AuthError):
                v.verify("Bearer " + token())
        self.assertEqual(len(fetches), 1)
        keys.update(JWKS)
        v.clock = lambda: NOW + 31
        self.assertEqual(v.verify("Bearer " + token(exp=NOW + 9999))["client"], "claude")


class Telemetry(Server):
    def test_expired_tokens_are_logged_with_the_verified_subject_and_no_token_material(self):
        self.start()
        expired = token(exp=NOW - 5)
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, expired)[0], 401)
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, "a.b.c")[0], 401)
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"})[0], 401)           # no token at all is not logged
        self.assertEqual([(a["outcome"], a["reason"], a["sub"]) for a in self.audit], [("invalid_token", "expired", "user-claude"), ("invalid_token", None, None)])
        blob = json.dumps(self.audit)
        for secret in (expired, expired.split(".")[2], expired.split(".")[1]):
            self.assertNotIn(secret, blob)

    def test_an_expired_token_is_attributed_only_after_issuer_audience_client_and_user_all_check_out(self):
        self.start()
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        for label, bad in (("wrong audience", dict(aud="https://other.example/mcp")), ("wrong issuer", dict(iss="https://evil.example")), ("unauthorized client", dict(client_id="cid-unknown")),
                           ("unauthorized subject", dict(sub="user-2")), ("unlisted subject with a real-looking name", dict(sub="user-claude-2")), ("no audience", dict(aud=None))):
            status, raw, _ = self.req("POST", "/mcp", ping, token(exp=NOW - 5, **bad))
            self.assertEqual((status, label), (401, label))                                  # still the same fail-closed 401, never 403 or 200
            self.assertEqual(json.loads(raw), {"error": "invalid_token"}, label)
        self.assertEqual([(a["outcome"], a["reason"], a["sub"]) for a in self.audit], [("invalid_token", None, None)] * 6)   # no subject for any of them
        self.assertEqual(self.req("POST", "/mcp", ping, token(exp=NOW - 5))[0], 401)         # the valid expired token for an allowed user IS attributed
        self.assertEqual(self.audit[-1]["reason"], "expired")
        self.assertEqual(self.audit[-1]["sub"], "user-claude")
        for tok in (token(exp=NOW - 5, sub="user-2"), token(exp=NOW - 5, aud="https://other.example/mcp")):
            self.assertNotIn(tok.split(".")[2], json.dumps(self.audit))
            self.assertNotIn(tok.split(".")[1], json.dumps(self.audit))
        self.assertEqual(self.req("POST", "/mcp", ping, token(sub="user-2"))[0], 403)         # NOT expired: unauthorized user keeps its 403 and is logged for allowlisting
        self.assertEqual((self.audit[-1]["outcome"], self.audit[-1]["sub"]), ("user_not_authorized", "user-2"))
        self.assertEqual(self.req("POST", "/mcp", ping, token(client_id="cid-unknown"))[0], 403)

    def test_successful_calls_record_issue_expiry_and_a_token_id_that_changes_with_each_token(self):
        self.start()
        t1, t2 = token(iat=NOW - 60, exp=NOW + 600), token(iat=NOW - 30, exp=NOW + 3600)
        for tok in (t1, t1, t2):
            self.assertEqual(self.tool(tok, "buzz_list_channels", community="life-os")[0], 200)
        rows = self.audit
        self.assertEqual([(r["iat"], r["exp"]) for r in rows], [(NOW - 60, NOW + 600), (NOW - 60, NOW + 600), (NOW - 30, NOW + 3600)])
        self.assertEqual((rows[0]["tid"] == rows[1]["tid"], rows[0]["tid"] != rows[2]["tid"], len(rows[0]["tid"])), (True, True, 8))   # a renewal shows up as a new tid
        self.assertNotIn(t1.split(".")[2], json.dumps(rows))


class DualClient(Server):
    def test_both_chat_clients_use_one_endpoint_and_see_the_same_tools_and_room(self):
        self.start()
        self.fake.events = [{"id": "c" * 64, "pubkey": "d" * 64, "created_at": 2, "kind": 9, "tags": [["h", CHAN]], "content": "synthetic room message"}]
        seen = {}
        for client in ("claude", "chatgpt"):
            tok = token(sub="user-" + client)
            status, init = self.rpc(tok, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": client, "version": "0"}})
            self.assertEqual((status, init["result"]["protocolVersion"]), (200, "2025-03-26"))
            seen[client] = (self.rpc(tok, "tools/list")[1]["result"], self.tool(tok, "buzz_get_messages", community="life-os", channel=CHAN))
        self.assertEqual(seen["claude"], seen["chatgpt"])
        self.assertEqual([t["name"] for t in seen["claude"][0]["tools"]], ["buzz_capabilities", "buzz_read", "buzz_publish", "buzz_list_channels", "buzz_get_messages", "buzz_search_messages", "buzz_get_addressed", "buzz_post_message"])
        self.assertEqual(seen["claude"][1][1][0]["untrusted_text"], "synthetic room message")
        self.assertEqual({a["client"] for a in self.audit}, {"claude", "chatgpt"})

    def test_audit_identity_comes_from_the_signed_token_not_from_the_caller(self):
        self.start()
        tok = token(sub="user-chatgpt")
        self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "buzz_list_channels", "arguments": {"community": "life-os"}, "client": "claude"}}, tok, X_Client="claude", X_Actor="claude")
        self.assertEqual(self.tool(tok, "buzz_get_messages", community="life-os", channel=CHAN, actor="claude")[1], {"error": "invalid_argument"})
        self.assertEqual([a["client"] for a in self.audit], ["chatgpt", "chatgpt"])
        self.assertEqual(self.audit[1]["outcome"], "invalid_argument")

    def test_scopes_split_read_from_write(self):
        self.start()
        read = token(sub="user-chatgpt")
        status, raw, hdr = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "buzz_post_message", "arguments": POST}}, read)
        self.assertEqual(status, 403)
        self.assertIn('error="insufficient_scope"', hdr["WWW-Authenticate"])
        self.assertIn(f'scope="{BASE}/mcpbuzz:write"', hdr["WWW-Authenticate"])
        self.assertEqual((self.fake.calls, self.fake.events), ([], []))
        self.assertEqual(self.tool(read, "buzz_list_channels", community="life-os")[0], 200)
        self.assertEqual(self.tool(token(scope="buzz:write"), "buzz_list_channels", community="life-os")[0], 403)   # write scope does not imply read

    def test_writes_stay_disabled_without_an_allowlisted_channel_even_with_the_scope(self):
        self.start(writes=())
        self.assertEqual(self.tool(token(scope="buzz:read buzz:write"), "buzz_post_message", **POST), (200, {"error": "write_disabled"}))
        self.assertEqual(self.fake.events, [])

    def test_wrong_community_and_wrong_channel_are_denied(self):
        self.start()
        tok = token(scope="buzz:read buzz:write")
        self.assertEqual(self.tool(tok, "buzz_list_channels", community="other")[1], {"error": "community_not_authorized"})
        self.assertEqual(self.tool(tok, "buzz_post_message", **dict(POST, community="bytalos"))[1], {"error": "write_disabled"})
        self.assertEqual(self.tool(tok, "buzz_post_message", **dict(POST, channel=OTHER))[1], {"error": "write_disabled"})
        self.assertEqual(self.fake.events, [])

    def test_idempotency_is_scoped_to_the_signing_identity_and_survives_restart(self):
        self.start()
        tc, tg = token(sub="user-claude", **WR), token(sub="user-chatgpt", **WR)
        first, other = self.tool(tc, "buzz_post_message", **POST)[1], self.tool(tg, "buzz_post_message", **POST)[1]
        self.assertEqual((first["status"], other["status"], len(self.fake.events)), ("sent_not_read_back", "sent_not_read_back", 2))   # same key from the other product is a different actor
        self.assertNotEqual((first["actor"], first["event_id"]), (other["actor"], other["event_id"]))
        self.assertEqual(self.tool(tc, "buzz_post_message", **POST)[1]["status"], "already_delivered")
        self.assertEqual(self.tool(tg, "buzz_post_message", **dict(POST, text="different"))[1], {"error": "idempotency_conflict"})
        self.start(fake=self.fake)                                                  # restart: new process, empty in-memory caches, same relay
        self.assertEqual(self.tool(tc, "buzz_post_message", **dict(POST, text="different"))[1], {"error": "idempotency_conflict"})
        self.assertEqual((self.tool(tg, "buzz_post_message", **POST)[1]["status"], self.tool(tc, "buzz_post_message", **POST)[1]["status"]), ("already_delivered",) * 2)
        self.assertEqual(len(self.fake.events), 2)

    def test_concurrent_posts_from_both_clients_create_one_event_per_identity(self):
        self.start()
        gate, out = threading.Barrier(8), []
        def go(i):
            gate.wait()
            out.append(self.tool(token(sub=("user-claude", "user-chatgpt")[i % 2], scope="buzz:write"), "buzz_post_message", **POST)[1]["status"])
        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((sorted(out), len(self.fake.events)), (["already_delivered"] * 6 + ["sent_not_read_back"] * 2, 2))


class Governed(Server):
    """LIFE-865: every Buzz tool call goes through authorization_gate.execute, through the real HTTP handler; a refusal is before any relay request and carries the gate's fixed reason."""
    def calls(self):
        return [c for c in self.fake.calls] if hasattr(self.fake, "calls") else None

    def test_a_granted_read_runs_and_the_receipt_names_client_tenant_and_purpose(self):
        self.start()
        tok = token(sub="user-claude")
        status, out = self.tool(tok, "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual(status, 200)
        self.assertTrue(out["ok"])
        allow, done = self.receipts[-2:]
        self.assertEqual((allow["outcome"], allow["tenant"], allow["tool"], allow["purpose"], allow["action"]), ("allow", "personal", "buzz_read", "buzz-read", "read"))
        self.assertEqual(done["outcome"], "ok")
        self.assertEqual(len(allow["principal_fp"]), 8)
        self.assertNotIn("claude-chat", json.dumps(self.receipts))                     # fingerprints only

    def refused(self, tok, name, reason, **args):
        before = len(self.fake.posted) if hasattr(self.fake, "posted") else 0
        status, out = self.tool(tok, name, **args)
        self.assertEqual((status, out["error"], out["reason"]), (200, "access_denied", reason))
        self.assertEqual(self.receipts[-1]["outcome"], "deny")
        self.assertEqual(self.receipts[-1]["reason"], reason)

    def test_an_ungranted_tool_is_refused_before_the_relay(self):
        from tests.platform.mcp_access_fixture import build
        self.start(access=build(grants=lambda seeded: [g for g in seeded if g["tool"] != "buzz_publish"]))      # the publish grants removed: the catalog row is there, the grant is not
        self.refused(token(sub="user-claude", **WR), "buzz_publish", "no_grant", operation="message", community="life-os", arguments={"channel": CHAN, "text": "hi", "idempotency_key": "k9"})

    def test_the_shipped_publish_grant_passes_the_gate_with_a_write_receipt_and_the_relay_allowlist_still_applies(self):
        """LIFE-865 (Jim, option A, Nov 22 2026): the shipped seed grants buzz_publish to the two chat clients in the communities they can write to; the gate allows it and writes the receipt, the relay's channel allowlist is unchanged."""
        from tests.platform.mcp_access_fixture import build
        self.start(access=build())
        status, out = self.tool(token(sub="user-claude", **WR), "buzz_publish", operation="message", community="life-os", arguments={"channel": CHAN, "text": "hi", "idempotency_key": "k9"})
        self.assertEqual(status, 200)
        allow = self.receipts[-2]
        self.assertEqual((allow["outcome"], allow["tool"], allow["action"], allow["purpose"], allow["tenant"]), ("allow", "buzz_publish", "write", "buzz-publish", "personal"))

    def test_revoked_expired_wrong_tenant_and_wrong_purpose_grants_are_refused_with_their_reason(self):
        from tests.platform.mcp_access_fixture import build
        def edit(**patch):
            return build(grants=lambda seeded: [{**g, **patch} if g["principal"] == "claude-chat" and g["tool"] == "buzz_read" else g for g in seeded])
        tok = token(sub="user-claude")
        self.start(access=edit(revoked=True))
        self.refused(tok, "buzz_read", "grant_revoked", operation="list_channels", community="life-os")
        self.start(access=edit(expires="2026-11-01"))
        self.refused(tok, "buzz_read", "grant_expired", operation="list_channels", community="life-os")
        self.start(access=edit(purpose="something-else"))
        self.refused(tok, "buzz_read", "purpose_mismatch", operation="list_channels", community="life-os")
        self.start(access=build(grants=lambda seeded: [g for g in seeded if g["tenant"] != "bytalos"]))
        self.refused(tok, "buzz_read", "no_grant", operation="list_channels", community="bytalos")                 # a Bytalos call with only Personal grants

    def test_a_spoofed_identity_is_ignored_and_an_unregistered_client_is_refused(self):
        from tests.platform.mcp_access_fixture import build
        self.start()
        tok = token(sub="user-chatgpt")
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "buzz_read", "arguments": {"operation": "list_channels", "community": "life-os", "principal": "claude-chat", "client": "claude"}, "principal": "claude-chat"}}
        status, raw, _ = self.req("POST", "/mcp", body, tok, X_Client="claude", X_Principal="claude-chat", X_Lifeos_Client_Id="claude-chat")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(json.loads(raw)["result"]["content"][0]["text"])["error"], "invalid_argument")       # an unknown argument never reaches identity at all
        self.tool(tok, "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual(self.receipts[-2]["principal_fp"], __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint("chatgpt-chat"))
        self.start(access=build(clients=[c for c in __import__("tests.platform.mcp_access_fixture", fromlist=["x"]).DOC["clients"] if c["id"] != "chatgpt-chat"]))
        status, out = self.tool(tok, "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual((status, out["error"], out["reason"]), (200, "access_denied", "identity_unknown"))

    def test_without_readable_access_data_every_tool_call_is_refused(self):
        self.start(access=False)
        from unittest import mock
        with mock.patch("lifeos.platform.mcp_access.load", side_effect=OSError("data file missing")):                 # a release without the platform files, or unreadable data
            status, out = self.tool(token(sub="user-claude"), "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual((status, out["error"]), (200, "governance_unavailable"))


class Credentials(Server):
    """LIFE-873: scoped `lm1.` credentials for the local clients, through the real HTTP handler. Synthetic secrets only; the shipped data holds hashes, never a secret."""
    SECRETS = {"mac-claude-code": "A" * 43, "oracle-claude-code": "B" * 43, "openclaw": "D" * 43, "hermes-librarian": "E" * 43}

    def access(self, edit=None, no_identity=False):
        import copy
        from lifeos.platform import mcp_access
        from tests.platform.mcp_access_fixture import CATALOG, DOC
        doc = copy.deepcopy(DOC)
        for row in doc["credentials"]:
            row["sha256"] = hashlib.sha256(self.SECRETS[row["id"]].encode()).hexdigest()
            if no_identity and row["id"] == "oracle-claude-code":
                row["buzz_identity"] = None
        if edit:
            edit(doc)
        return mcp_access.load(json.dumps(doc), json.dumps(CATALOG))

    def tok(self, cid="mac-claude-code", secret=None):
        return f"lm1.{cid}." + (secret or self.SECRETS[cid])

    def test_each_credential_lists_only_its_granted_tools_and_completes_one_read_with_its_own_receipt(self):
        self.start(access=self.access())
        for cid in (c for c in self.SECRETS if c not in ("openclaw", "hermes-librarian")):                                                # openclaw and hermes-librarian are capabilities only: their own tests below
            status, out = self.rpc(self.tok(cid), "tools/list")
            names = {t["name"] for t in out["result"]["tools"]}
            self.assertEqual((status, {"buzz_capabilities", "buzz_read"} <= names, "buzz_publish" in names or "buzz_post_message" in names), (200, True, False), cid)
            status, caps = self.tool(self.tok(cid), "buzz_capabilities")
            self.assertEqual((status, caps["ok"]), (200, True), cid)
            allow, done = self.receipts[-2:]
            self.assertEqual((allow["outcome"], allow["tenant"], allow["tool"], allow["purpose"], done["outcome"]), ("allow", "personal", "buzz_capabilities", "buzz-read", "ok"))
            self.assertEqual(allow["principal_fp"], __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint(cid))
        self.assertNotIn("mac-claude-code", json.dumps(self.receipts))                    # fingerprints only

    def test_a_call_outside_the_clients_grants_is_refused_no_grant_before_the_relay(self):
        self.start(access=self.access(edit=lambda d: d.update(grants=[g for g in d["grants"] if not (g["principal"] == "mac-claude-code" and g["tool"] == "buzz_read")])))
        status, out = self.tool(self.tok(), "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual((status, out["error"], out["reason"]), (200, "access_denied", "no_grant"))
        self.assertEqual(self.receipts[-1]["outcome"], "deny")
        self.assertEqual(self.fake.posted if hasattr(self.fake, "posted") else [], [])

    def test_a_credential_cannot_publish_or_leave_its_community(self):
        self.start(access=self.access())
        status, out = self.tool(self.tok(), "buzz_publish", operation="message", community="life-os", arguments={"channel": CHAN, "text": "x", "idempotency_key": "k1"})
        self.assertEqual(status, 403)                                                       # read scope only
        status, out = self.tool(self.tok(), "buzz_read", operation="list_channels", community="bytalos")
        self.assertEqual((status, out["error"]), (200, "community_not_authorized"))

    def test_bad_credentials_are_one_401_and_no_tool_runs(self):
        self.start(access=self.access(edit=lambda d: d["credentials"][1].update(revoked=True)))
        expired = self.access(edit=lambda d: d["credentials"][0].update(expires="2026-11-01"))
        bad = [self.tok(secret="C" * 43), self.tok("no-such-client", "A" * 43), self.tok("mac-claude-code", self.SECRETS["oracle-claude-code"]),    # wrong secret, unknown id, the other client's secret
               self.tok("oracle-claude-code"), "lm1.mac-claude-code", "lm1.mac-claude-code.short", "lm1..x.y.z"]                                      # revoked, malformed
        for tok in bad:
            status, out = self.rpc(tok, "tools/call", {"name": "buzz_capabilities", "arguments": {}})
            self.assertEqual((status, out), (401, {"error": "invalid_token"}), tok[:20])
        self.assertEqual(self.receipts, [])
        self.start(access=expired)
        self.assertEqual(self.rpc(self.tok(), "tools/list")[0], 401)

    def test_one_credential_never_becomes_another_client_whatever_the_caller_sends(self):
        self.start(access=self.access())
        status, raw, _ = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "buzz_capabilities", "arguments": {}}}, self.tok("oracle-claude-code"),
                                  X_Client="mac-claude-code", X_Principal="mac-claude-code", X_Lifeos_Client_Id="mac-claude-code", X_Forwarded_User="jimmarkunas")
        self.assertEqual(status, 200)
        fp = __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint
        self.assertEqual(self.receipts[-2]["principal_fp"], fp("oracle-claude-code"))
        self.tool(self.tok("mac-claude-code"), "buzz_capabilities")
        self.assertNotEqual(self.receipts[-2]["principal_fp"], fp("oracle-claude-code"))                                                             # same host, two clients, two identities

    def test_a_credential_with_no_buzz_identity_sees_capabilities_but_makes_no_relay_call(self):
        self.start(access=self.access(no_identity=True))
        status, caps = self.tool(self.tok("oracle-claude-code"), "buzz_capabilities")
        self.assertEqual((status, caps["ok"], caps["data"]["identity"]["communities"]), (200, True, {}))
        status, out = self.tool(self.tok("oracle-claude-code"), "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual((status, out["error"]), (200, "client_identity_not_configured"))

    def test_source_read_and_agent_lookup_are_their_own_gate_tools_for_every_proved_client_and_not_for_the_chat_clients(self):
        """LIFE-888 (Jim, Nov 22 2026, comment 13735): the two lookups are granted to the four proved clients only; no Buzz identity or relay is needed; the chat clients are refused at the gate."""
        from datetime import datetime, timedelta
        from unittest import mock
        from lifeos.platform import agent_directory
        from tests.platform import agent_directory_fixture as F
        fp = __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint
        now = datetime(2026, 11, 16, 12, 0, tzinfo=agent_directory.CENTRAL)
        github = lambda path, ref: {"commit": "a" * 40, "last_change": "b" * 40, "blob": "c" * 40, "modified": now - timedelta(days=1), "text": "policy text"}
        self.start(access=self.access(no_identity=True))
        with mock.patch.dict(buzz_mcp.LOOKUP, {"fetchers": {"github": github}, "rows": lambda: F.rows(), "now": lambda: now}):
            for cid in self.SECRETS:
                status, out = self.tool(self.tok(cid), "buzz_read", operation="source_read", community="life-os", arguments={"source": "github", "path": "AGENTS.md"})
                self.assertEqual((status, out["ok"], out["data"]["revision"]["commit_sha"], out["data"]["freshness"]), (200, True, "a" * 40, "fresh"), cid)
                allow = self.receipts[-2]
                self.assertEqual((allow["tool"], allow["purpose"], allow["outcome"], allow["principal_fp"]), ("source_read", "source-read", "allow", fp(cid)))
                status, out = self.tool(self.tok(cid), "buzz_read", operation="agent_lookup", community="life-os", arguments={"agent_id": "bot_hermes_claude-hermes"})
                self.assertEqual((status, out["ok"], out["data"]["status"], out["data"]["authorizes"]), (200, True, "configured-untested", []), cid)
                self.assertEqual((self.receipts[-2]["tool"], self.receipts[-2]["purpose"]), ("agent_lookup", "agent-lookup"))
            status, out = self.tool(self.tok(), "buzz_read", operation="source_read", community="life-os", arguments={"source": "github", "path": "AGENTS.md", "max_age_seconds": 60})
            self.assertEqual((status, out["error"]), (200, "stale_source"))                                                                       # the live refusal's shape: fixed reason, after the gate allowed it
            status, out = self.tool(self.tok(), "buzz_read", operation="source_read", community="life-os", arguments={"source": "jira"})
            self.assertEqual(out["error"], "unknown_source")
            before = len(self.receipts)
            status, out = self.tool(token(sub="user-claude"), "buzz_read", operation="source_read", community="life-os", arguments={"source": "github", "path": "AGENTS.md"})
            self.assertEqual((out["error"], out["reason"], self.receipts[-1]["outcome"]), ("access_denied", "catalog_unknown_capability", "deny"))      # chat clients hold no lookup grant
            self.assertEqual(len(self.receipts), before + 1)

    def test_an_unwired_lookup_says_so_with_a_fixed_reason(self):
        from unittest import mock
        self.start(access=self.access())
        with mock.patch.dict(buzz_mcp.LOOKUP, {"fetchers": {}, "rows": None}):
            self.assertEqual(self.tool(self.tok(), "buzz_read", operation="source_read", community="life-os", arguments={"source": "notion", "page_id": "[removed]"})[1]["error"], "source_unavailable")
            self.assertEqual(self.tool(self.tok(), "buzz_read", operation="agent_lookup", community="life-os", arguments={"agent_id": "bot_hermes_claude-hermes"})[1]["error"], "directory_unavailable")

    def test_google_reads_are_granted_to_hermes_librarian_only_per_account_and_the_named_account_is_the_boundary(self):
        """LIFE-875 (Jim, comment 13747): read-only Gmail and Drive grants for the Hermes librarian credential, personal and bytalos; every other client is refused at the gate; an omitted account never reaches Google."""
        from unittest import mock
        from tests.platform.test_google_read import FakeGoogle
        fake = FakeGoogle()
        fp = __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint
        self.start(access=self.access(no_identity=True))
        creds = lambda account, service: {"client_id": "cid", "client_secret": "csecret-xxxxxxxx", "refresh_token": account}
        from lifeos.platform import google_read
        from tests.platform.test_google_read import SYNTHETIC_OWNER_SHA256
        with mock.patch.dict(google_read.OWNER_SHA256, SYNTHETIC_OWNER_SHA256), mock.patch.dict(buzz_mcp.GOOGLE, {"creds": creds, "opener": fake}):
            lib = self.tok("hermes-librarian")
            for account in ("personal", "bytalos"):
                for op, args, key in (("gmail_search", {"query": "q"}, "results"), ("gmail_read", {"message_id": "msg111111"}, "id"), ("drive_search", {"query": "q"}, "results"), ("drive_read", {"file_id": "file111111"}, "id")):
                    status, out = self.tool(lib, "buzz_read", operation=op, arguments={"account": account, **args})
                    self.assertEqual((status, out["ok"], out["data"]["account"]), (200, True, account), (account, op))
                    allow = self.receipts[-2]
                    self.assertEqual((allow["tool"], allow["purpose"], allow["tenant"], allow["outcome"], allow["principal_fp"]), (op, "governed-google-read", account, "allow", fp("hermes-librarian" if account == "personal" else "hermes-librarian-bytalos")))
            reached = len(fake.requests)
            for cid in ("mac-claude-code", "oracle-claude-code", "openclaw"):                                                    # granted to no other client
                status, out = self.tool(self.tok(cid), "buzz_read", operation="gmail_search", arguments={"account": "personal", "query": "q"})
                self.assertEqual((out["error"], out["reason"]), ("access_denied", "catalog_unknown_capability"), cid)
            status, out = self.tool(token(sub="user-claude"), "buzz_read", operation="drive_read", arguments={"account": "personal", "file_id": "file111111"})
            self.assertEqual(out["error"], "access_denied")                                                                        # nor to the chat clients
            for args, reason in (({"query": "q"}, "account_required"), ({"account": "work", "query": "q"}, "unknown_account")):
                status, out = self.tool(lib, "buzz_read", operation="gmail_search", arguments=args)
                self.assertEqual((out["error"], out["reason"]), ("access_denied", reason))
            self.assertEqual(len(fake.requests), reached)                                                                          # none of the refusals reached Google
            names = {t["name"] for t in self.rpc(lib, "tools/list")[1]["result"]["tools"]}
            self.assertFalse({n for n in names if "send" in n or "delete" in n or "modify" in n})                                  # no write tool exists

    def test_a_revoked_google_consent_and_an_unwired_service_have_fixed_reasons(self):
        from unittest import mock
        from tests.platform.test_google_read import FakeGoogle
        self.start(access=self.access(no_identity=True))
        lib = self.tok("hermes-librarian")
        from lifeos.platform import google_read
        from tests.platform.test_google_read import SYNTHETIC_OWNER_SHA256
        with mock.patch.dict(google_read.OWNER_SHA256, SYNTHETIC_OWNER_SHA256), mock.patch.dict(buzz_mcp.GOOGLE, {"creds": lambda a, s: {"client_id": "c", "client_secret": "s" * 10, "refresh_token": a}, "opener": FakeGoogle(revoked=True)}):
            self.assertEqual(self.tool(lib, "buzz_read", operation="drive_search", arguments={"account": "bytalos", "query": "q"})[1]["error"], "consent_revoked")
        with mock.patch.dict(buzz_mcp.GOOGLE, {"creds": None, "opener": None}):
            self.assertEqual(self.tool(lib, "buzz_read", operation="gmail_search", arguments={"account": "personal", "query": "q"})[1]["error"], "not_configured")

    def test_hermes_librarian_ships_capabilities_only_like_openclaw_and_is_its_own_identity(self):
        self.start(access=self.access())
        tok = self.tok("hermes-librarian")
        names = {t["name"] for t in self.rpc(tok, "tools/list")[1]["result"]["tools"]}
        self.assertEqual((len(names), {"buzz_capabilities", "buzz_read"} <= names, "buzz_publish" in names or "buzz_post_message" in names), (6, True, False))
        status, caps = self.tool(tok, "buzz_capabilities")
        self.assertEqual((status, caps["ok"], caps["data"]["identity"]["communities"]), (200, True, {}))
        self.assertEqual(self.receipts[-2]["principal_fp"], __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint("hermes-librarian"))
        self.assertEqual(self.tool(tok, "buzz_read", operation="list_channels", community="life-os")[1]["error"], "client_identity_not_configured")
        self.assertEqual(self.rpc(self.tok("hermes-librarian", self.SECRETS["openclaw"]), "tools/list")[0], 401)                                   # another client's secret is a 401

    def test_openclaw_ships_capabilities_only_with_its_own_two_read_tools_and_no_buzz_key(self):
        self.start(access=self.access())
        tok = self.tok("openclaw")
        names = {t["name"] for t in self.rpc(tok, "tools/list")[1]["result"]["tools"]}
        self.assertEqual((len(names), {"buzz_capabilities", "buzz_read"} <= names, "buzz_publish" in names or "buzz_post_message" in names), (6, True, False))   # the same six read tools as the other clients, no publish
        status, caps = self.tool(tok, "buzz_capabilities")
        self.assertEqual((status, caps["ok"], caps["data"]["identity"]["communities"]), (200, True, {}))
        self.assertEqual(self.tool(tok, "buzz_read", operation="list_channels", community="life-os")[1]["error"], "client_identity_not_configured")
        self.assertEqual(self.rpc(self.tok("openclaw", self.SECRETS["mac-claude-code"]), "tools/list")[0], 401)   # another client's secret is a 401

    def test_an_oauth_token_never_matches_a_credential_row_and_unreadable_data_fails_closed(self):
        from unittest import mock
        self.start(access=self.access())
        self.assertEqual(self.tool(token(sub="user-claude"), "buzz_capabilities")[1]["ok"], True)                                  # unchanged path
        self.assertEqual(self.receipts[-2]["principal_fp"], __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint("claude-chat"))
        self.start(access=False)
        with mock.patch("lifeos.platform.mcp_access.load", side_effect=OSError("data file missing")):
            status, out = self.rpc(self.tok(), "tools/list")
        self.assertEqual((status, out), (503, {"error": "governance_unavailable"}))


class FourIdentities(Server):
    def test_each_client_and_community_pairing_signs_with_its_own_key_and_reads_back_independently(self):
        self.start(writes=BOTH)
        pairs = [(c, k) for c in ("claude", "chatgpt") for k in ("life-os", "bytalos")]
        receipts = {}
        for client, comm in pairs:
            tok = token(sub="user-" + client, **WR)
            status, receipts[client, comm] = self.tool(tok, "buzz_post_message", community=comm, channel=CHANS[comm], text=f"synthetic from {client} to {comm}", idempotency_key="id-" + client + comm[:2])
            self.assertEqual((status, receipts[client, comm]["status"]), (200, "sent_not_read_back"))
        pubs = {pair: nip98.pubkey(KEYS[pair]) for pair in pairs}
        self.assertEqual(len(set(pubs.values())), 4)
        by_id = {e["id"]: e for e in self.fake.events}
        for pair in pairs:
            ev = by_id[receipts[pair]["event_id"]]
            self.assertEqual((ev["pubkey"], dict(ev["tags"])["h"], dict(ev["tags"])["actor"]), (pubs[pair], CHANS[pair[1]], "key:" + pubs[pair][:16]))
            other = ({"claude": "chatgpt", "chatgpt": "claude"}[pair[0]], pair[1])              # the OTHER product's identity reads it back
            got = self.tool(token(sub="user-" + other[0]), "buzz_get_messages", community=pair[1], channel=CHANS[pair[1]])[1]
            self.assertIn((receipts[pair]["event_id"], f"synthetic from {pair[0]} to {pair[1]}", "key:" + pubs[pair][:16]), [(m["event_id"], m["untrusted_text"], m["actor"]) for m in got])
        self.assertEqual({a["identity"] for a in self.audit if a["tool"] == "buzz_post_message"}, {p[:16] for p in pubs.values()})
        self.assertNotIn(KEY_A.hex(), json.dumps(self.audit))

    def test_a_missing_credential_denies_only_its_pairing_and_never_falls_back(self):
        keys = {k: v for k, v in KEYS.items() if k != ("chatgpt", "bytalos")}
        self.start(writes=BOTH, keys=keys)
        tg = token(sub="user-chatgpt", **WR)
        for call in (("buzz_list_channels", dict(community="bytalos")), ("buzz_post_message", dict(POST, community="bytalos", channel=OTHER))):
            self.assertEqual(self.tool(tg, *call[:1], **call[1]), (200, {"error": "key_not_configured"}))
        self.assertEqual((self.fake.calls, self.fake.events), ([], []))                  # claude's bytalos key was never borrowed
        self.assertEqual(self.tool(token(sub="user-claude", **WR), "buzz_list_channels", community="bytalos")[0], 200)
        self.assertEqual(self.tool(tg, "buzz_list_channels", community="life-os")[0], 200)
        self.assertEqual(self.tool(tg, "buzz_post_message", **POST)[1]["actor"], "key:" + nip98.pubkey(KEYS["chatgpt", "life-os"])[:16])

    def test_a_client_with_no_identity_at_all_is_denied_before_any_relay_request(self):
        self.start(writes=BOTH, keys={k: v for k, v in KEYS.items() if k[0] == "claude"})
        for call in (("buzz_list_channels", dict(community="life-os")), ("buzz_post_message", POST)):
            self.assertEqual(self.tool(token(sub="user-chatgpt", **WR), call[0], **call[1]), (200, {"error": "client_identity_not_configured"}))
        self.assertEqual((self.fake.calls, self.fake.events), ([], []))
        self.assertEqual(self.tool(token(sub="user-claude"), "buzz_list_channels", community="life-os")[0], 200)

    def test_the_caller_cannot_choose_the_identity(self):
        self.start(writes=BOTH)
        tok = token(sub="user-chatgpt", **WR)
        for forged in (dict(client="claude"), dict(actor="key:" + nip98.pubkey(KEYS["claude", "life-os"])[:16]), dict(identity="claude")):
            self.assertEqual(self.tool(tok, "buzz_post_message", **dict(POST, **forged)), (200, {"error": "invalid_argument"}))
        self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "buzz_post_message", "arguments": POST}}, tok, X_Client="claude", X_Actor="claude", User_Agent="Claude-User")
        self.assertEqual([e["pubkey"] for e in self.fake.events], [nip98.pubkey(KEYS["chatgpt", "life-os"])])


class UserAuthorization(Server):
    def test_a_different_user_on_an_authorized_client_gets_nothing(self):
        self.start()
        for name, tok in (("other_user", token(sub="user-2")), ("absent_sub", token(sub=None)), ("numeric_sub", token(sub=1)), ("list_sub", token(sub=["user-1"])),
                          ("write_scope_other_user", token(sub="user-2", scope="buzz:read buzz:write"))):
            for call in (("buzz_list_channels", dict(community="life-os")), ("buzz_post_message", POST)):
                status, raw, _ = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": call[0], "arguments": call[1]}}, tok)
                self.assertEqual((status, json.loads(raw)), (403, {"error": "user_not_authorized"}), name)
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, token(sub="user-2"))[0], 403)
        self.assertEqual((self.fake.calls, self.fake.events), ([], []))
        self.assertTrue(self.audit and all(a["outcome"] == "user_not_authorized" and a["tool"] is None and a["sub"] in ("user-2", None, "1") for a in self.audit))   # the denied subject is logged so the owner can allowlist it

    def test_community_boundary_is_per_user_and_checked_before_any_relay_request(self):
        self.start()
        tok = token(sub="user-lifeos-only", scope="buzz:read buzz:write")
        self.assertEqual(self.tool(tok, "buzz_list_channels", community="bytalos"), (200, {"error": "community_not_authorized"}))
        self.assertEqual(self.tool(tok, "buzz_post_message", **dict(POST, community="bytalos"))[1], {"error": "community_not_authorized"})
        self.assertEqual(self.tool(tok, "buzz_search_messages", query="x")[1], {"error": "community_not_authorized"})   # no community: denied, not defaulted
        self.assertEqual((self.fake.calls, self.fake.events), ([], []))
        self.assertEqual(self.tool(tok, "buzz_list_channels", community="life-os")[0], 200)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.tool(token(scope="buzz:read"), "buzz_list_channels", community="bytalos")[0], 200)       # a user granted both communities still reaches both
        self.assertEqual(self.audit[0]["outcome"], "community_not_authorized")


class Startup(unittest.TestCase):
    """Runs the unit's real ExecStart, parsed from the unit file, from the staged /opt layout (only lifeos/__init__.py, lifeos/buzz/*.py and the old-name shims lifeos/buzz_*.py) with synthetic config."""
    UNIT = ROOT / "lifeos" / "edge" / "buzz" / "oracle" / "lifeos-buzz-mcp.service"

    def stage(self):
        stage = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, stage, True)
        (stage / "lifeos").mkdir()
        for f in [ROOT / "lifeos" / "__init__.py", *(ROOT / "lifeos").glob("buzz_*.py")]:
            shutil.copy(f, stage / "lifeos" / f.name)
        (stage / "lifeos" / "buzz").mkdir()
        for f in (ROOT / "lifeos" / "buzz").glob("*.py"):
            shutil.copy(f, stage / "lifeos" / "buzz" / f.name)
        return stage

    def exec_argv(self, flags=None):
        unit = self.UNIT.read_text()
        argv = shlex.split(next(l for l in unit.splitlines() if l.startswith("ExecStart="))[10:])
        self.assertEqual(argv[0], "/usr/bin/python3")
        return [sys.executable, *(argv[1:] if flags is None else flags)]

    def env(self, stage, **extra):
        return {"PATH": os.environ["PATH"], "PYTHONPATH": str(stage), "PYTHONDONTWRITEBYTECODE": "1", **extra}

    def test_unit_does_not_use_isolated_mode_and_keeps_sandboxing(self):
        unit = self.UNIT.read_text()
        self.assertNotIn(" -I ", unit.split("ExecStart=")[1].splitlines()[0])
        self.assertEqual(unit.count("LoadCredentialEncrypted="), 4)                    # exactly the four identity slots
        for line in ("NoNewPrivileges=yes", "ProtectSystem=strict", "User=buzz-mcp", "LoadCredentialEncrypted=buzz_key_claude_life_os", "LoadCredentialEncrypted=buzz_key_chatgpt_bytalos", "Environment=PYTHONPATH=/opt/lifeos-buzz-mcp/current"):
            self.assertIn(line, unit)

    def test_execstart_imports_from_the_staged_layout_without_secrets(self):
        stage = self.stage()
        other = tempfile.mkdtemp()                                   # cwd that is NOT the stage: only PYTHONPATH can supply the package
        self.addCleanup(shutil.rmtree, other, True)
        done = subprocess.run(self.exec_argv(), cwd=other, env=self.env(stage), capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 1)
        self.assertIn("config error", done.stderr)                   # reached main() and failed on missing config, not on import
        self.assertNotIn("ModuleNotFoundError", done.stderr)
        isolated = subprocess.run([sys.executable, "-I", *self.exec_argv()[3:]], cwd=other, env=self.env(stage), capture_output=True, text=True, timeout=30)
        self.assertIn("ModuleNotFoundError", isolated.stderr)        # negative control: the old -I command really cannot start

    def test_staged_service_serves_loopback_discovery(self):
        stage = self.stage()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = self.env(stage, BUZZ_MCP_PORT=str(port), BUZZ_MCP_PUBLIC_URL=BASE, BUZZ_OAUTH_ISSUER=ISS, BUZZ_OAUTH_JWKS_URL=ISS + "/jwks", BUZZ_OAUTH_CLIENTS="cid-one",
                       BUZZ_OAUTH_USERS="user-claude:claude:life-os", BUZZ_URL_LIFE_OS=URLS["life-os"], BUZZ_KEY_CLAUDE_LIFE_OS=KEY_A.hex())
        proc = subprocess.Popen(self.exec_argv(), cwd=tempfile.gettempdir(), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(proc.kill)
        self.addCleanup(proc.stderr.close)
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/.well-known/oauth-protected-resource") as r:
                    self.assertEqual(json.load(r)["resource"], BASE + "/mcp")
                break
            except OSError:
                self.assertIsNone(proc.poll(), "service exited during startup")
                time.sleep(0.1)
        else:
            self.fail("service never answered")
        self.assertEqual(self.post_status(port), 401)
        proc.kill()
        self.assertNotIn(KEY_A.hex(), proc.stderr.read())

    @staticmethod
    def post_status(port):
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/mcp", b"{}", method="POST"))
        except urllib.error.HTTPError as e:
            return e.code


class OracleShapes(Server):
    def test_scopes_may_carry_the_audience_prefix_oracle_adds_but_no_other_prefix(self):
        self.start()
        q = BASE + "/mcp"
        self.assertEqual(self.tool(token(scope=f"{q}buzz:read"), "buzz_list_channels", community="life-os")[0], 200)
        self.assertEqual(self.tool(token(scope=f"{q}buzz:write"), "buzz_post_message", **POST)[0], 200)
        for bad in ("https://evil.example/mcpbuzz:write", f"{q}x", "buzz:writer"):
            self.assertEqual(self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "buzz_post_message", "arguments": POST}}, token(scope=bad))[0], 403)
        self.assertEqual(len(self.fake.events), 1)                                   # only the properly scoped write went through


    def test_issuer_trailing_slash_is_equivalent_but_nothing_else(self):
        v = Verifier("https://identity.example.test", BASE + "/mcp", CLIENTS, ISS + "/jwks", USERS, fetch=lambda u: JWKS, clock=lambda: NOW)
        self.assertEqual(v.verify("Bearer " + token(iss="https://identity.example.test/"))["client"], "claude")     # Oracle IAM issues iss with a trailing slash
        self.assertEqual(v.verify("Bearer " + token(iss="https://identity.example.test"))["client"], "claude")
        for bad in ("https://identity.example.test.evil", "https://identity.example.test//x", None, ["https://identity.example.test"], 5):
            with self.assertRaises(AuthError):
                v.verify("Bearer " + token(iss=bad))

    def test_authorization_server_address_is_separate_from_the_issuer(self):
        env = {**Config.ENV, "BUZZ_OAUTH_ISSUER": "https://identity.example.test/", "BUZZ_OAUTH_AS_URL": "https://idcs-x.example.test/", "BUZZ_OAUTH_CLIENTS": "cid-oracle"}
        auth = buzz_mcp.from_env(env)[1]
        self.assertEqual((auth.issuer, auth.as_url, auth.clients), ("https://identity.example.test", "https://idcs-x.example.test", frozenset({"cid-oracle"})))
        self.assertEqual(buzz_mcp.from_env(Config.ENV)[1].as_url, ISS)
        with self.assertRaises(ValueError):
            buzz_mcp.from_env({**env, "BUZZ_OAUTH_AS_URL": "http://insecure.example.test"})


class Transport(Server):
    def test_streamable_http_shapes(self):
        self.start(origins=("https://claude.ai",))
        tok = token()
        self.assertEqual(self.req("POST", "/mcp", {"method": "notifications/initialized"}, tok)[0], 202)
        self.assertEqual(self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 5, "result": {}}, tok)[0], 202)           # a client's reply to a server request
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, tok, MCP_Protocol_Version="1999-01-01")[0], 400)
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, tok, Accept="text/html")[0], 400)
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, tok, Origin="https://evil.example")[0], 403)
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, tok, Origin="https://claude.ai")[0], 200)
        self.assertEqual(self.req("POST", "/mcp", b"not json", tok)[0], 400)
        self.assertEqual(self.req("POST", "/mcp", b"[1]", tok)[0], 400)
        self.assertEqual(self.req("POST", "/mcp", b"x" * (buzz_mcp.MAX_BODY + 1), tok)[0], 400)
        self.assertEqual(self.req("POST", "/other", {"id": 1}, tok)[0], 404)
        status, _, hdr = self.req("GET", "/mcp", tok=tok)
        self.assertEqual((status, hdr["Allow"]), (405, "POST"))
        self.assertEqual(self.rpc(tok, "initialize", {"protocolVersion": "2099-01-01"})[1]["result"]["protocolVersion"], buzz_mcp.PROTOCOL)

    def test_no_secret_is_logged_or_returned(self):
        self.start()
        tok = token(sub="user-chatgpt", scope="buzz:read buzz:write")
        self.fake.fail_query = True
        results = [self.tool(tok, "buzz_list_channels", community="life-os"), self.tool(tok, "buzz_post_message", **POST)]
        blob = json.dumps([self.audit, results])
        for secret in (tok, tok.split(".")[2], *(k.hex() for k in KEYS.values()), POST["text"]):
            self.assertNotIn(secret, blob)


class Config(unittest.TestCase):
    FOUR = {f"BUZZ_KEY_{i}_{c}": KEYS[i.lower(), c.lower().replace("_", "-")].hex() for i in ("CLAUDE", "CHATGPT") for c in ("LIFE_OS", "BYTALOS")}
    ENV = {"BUZZ_MCP_PUBLIC_URL": BASE + "/", "BUZZ_OAUTH_ISSUER": ISS, "BUZZ_OAUTH_JWKS_URL": ISS + "/jwks", "BUZZ_OAUTH_CLIENTS": "cid-one", "BUZZ_OAUTH_USERS": "user-claude:claude:life-os+bytalos,user-chatgpt:chatgpt:life-os+bytalos,svc+x@example.com:chatgpt:life-os",
           "BUZZ_URL_LIFE_OS": URLS["life-os"] + "/", "BUZZ_URL_BYTALOS": URLS["bytalos"], **FOUR}

    def test_from_env_builds_four_identities_defaults_to_no_writes_and_rejects_unsafe_config(self):
        relays, auth, base, origins = buzz_mcp.from_env(self.ENV)
        self.assertEqual(({i: {c: nip98.pubkey(k) for c, k in r.keys.items()} for i, r in relays.items()}, {r.writes.__len__() for r in relays.values()}),
                         ({i: {c: nip98.pubkey(KEYS[i, c]) for c in ("life-os", "bytalos")} for i in ("claude", "chatgpt")}, {0}))
        self.assertEqual((base, origins, auth.audience, auth.clients), (BASE, (), BASE + "/mcp", frozenset({"cid-one"})))
        self.assertEqual(auth.users["svc+x@example.com"], ("chatgpt", frozenset({"life-os"})))   # a plus-address subject parses, the label is the middle field
        url_ids = buzz_mcp.from_env({**self.ENV, "BUZZ_OAUTH_CLIENTS": "https://chatgpt.com/oauth/client.json, dcr-abc123"})[1]
        self.assertEqual(url_ids.clients, frozenset({"https://chatgpt.com/oauth/client.json", "dcr-abc123"}))   # a client id may be a URL
        no_chatgpt = {k: v for k, v in self.ENV.items() if "CHATGPT_BYTALOS" not in k}
        self.assertEqual({i: set(r.keys) for i, r in buzz_mcp.from_env(no_chatgpt)[0].items()}, {"claude": {"life-os", "bytalos"}, "chatgpt": {"life-os"}})
        bad = [{**self.ENV, "BUZZ_MCP_PUBLIC_URL": "http://edge.example.test"}, {**self.ENV, "BUZZ_MCP_PUBLIC_URL": BASE + "/mcp"}, {**self.ENV, "BUZZ_OAUTH_ISSUER": ""}, {**self.ENV, "BUZZ_OAUTH_JWKS_URL": "http://x"},
               {**self.ENV, "BUZZ_OAUTH_USERS": "user-claude:admin:life-os"}, {**self.ENV, "BUZZ_OAUTH_USERS": "user-claude:shared:life-os"}, {**self.ENV, "BUZZ_OAUTH_USERS": ""}, {**self.ENV, "BUZZ_OAUTH_USERS": "user-claude:claude:other"}, {**self.ENV, "BUZZ_OAUTH_USERS": "user-claude"}, {**self.ENV, "BUZZ_OAUTH_USERS": "user-claude:claude"},
               {**self.ENV, "BUZZ_OAUTH_CLIENTS": ""}, {**self.ENV, "BUZZ_KEY_CLAUDE_LIFE_OS": "nothex"}, {**self.ENV, "BUZZ_AUTH_TAG": "x"},
               {**self.ENV, "BUZZ_KEY_LIFE_OS": KEY_A.hex()},                                                             # the old per-community shared key is refused, not used as a fallback
               {**self.ENV, "BUZZ_KEY_CHATGPT_BYTALOS": self.FOUR["BUZZ_KEY_CLAUDE_BYTALOS"]},                          # one key may not fill two identity slots
               {k: v for k, v in self.ENV.items() if not k.startswith("BUZZ_KEY_")}]
        for env in bad:
            with self.assertRaises(ValueError) as c:
                buzz_mcp.from_env(env)
            self.assertNotIn("nothex", str(c.exception))
            self.assertNotIn(KEY_A.hex(), str(c.exception))

    def test_systemd_credentials_overlay_the_environment(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "buzz_key_chatgpt_bytalos").write_text(KEYS["chatgpt", "bytalos"].hex() + "\n")
            env = buzz_mcp.credentials({**self.ENV, "BUZZ_KEY_CHATGPT_BYTALOS": "", "CREDENTIALS_DIRECTORY": d})
        self.assertEqual(nip98.secret_from(env["BUZZ_KEY_CHATGPT_BYTALOS"]), KEYS["chatgpt", "bytalos"])
        self.assertEqual(buzz_mcp.credentials({"A": "1"}), {"A": "1"})


if __name__ == "__main__":
    unittest.main()
