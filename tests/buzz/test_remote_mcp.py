"""/ BUZZ-16: the shared remote MCP (OAuth 2.1 resource server + Streamable HTTP) for Claude Chat and ChatGPT Chat,
proved over real loopback HTTP.
"""

import base64, hashlib, json, os, pathlib, random, shlex, shutil, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer

from lifeos.buzz import mcp as buzz_mcp
from lifeos.platform import call_limit

from lifeos.buzz import nip98
from lifeos.buzz.auth import AuthError, Verifier
from lifeos.buzz.relay import Relay
from tests.buzz.test_bridge import CHAN, KEY_A, KEY_B, OTHER, ROOT, URLS, FakeRelay
from tests.platform.mcp_access_fixture import open_access

BASE, ISS = "https://edge.example.test", "https://idp.example.test"
CLIENTS = {"cid-one"}
USERS = {
    "user-claude": ("claude", ("life-os", "bytalos")),
    "user-chatgpt": ("chatgpt", ("life-os", "bytalos")),
    "user-lifeos-only": ("claude", ("life-os",)),
}
NOW = 1_800_000_000
POST = dict(community="life-os", channel=CHAN, text="synthetic hello", idempotency_key="k1")
KEYS = {
    ("claude", "life-os"): bytes([21]) * 32,
    ("claude", "bytalos"): bytes([22]) * 32,
    ("chatgpt", "life-os"): bytes([23]) * 32,
    ("chatgpt", "bytalos"): bytes([24]) * 32,
}  # synthetic, one per pairing
CHANS = {"life-os": CHAN, "bytalos": OTHER}
BOTH = (("life-os", CHAN), ("bytalos", OTHER))
WR = dict(scope="buzz:read buzz:write")


def _prime(rng, bits=1024):
    while True:
        c = rng.getrandbits(bits) | (1 << bits - 1) | 1
        if all(c % p for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)) and all(
            pow(a, c - 1, c) == 1 for a in (2, 3, 5, 7, 11)
        ):
            return c


def _rsa():
    rng = random.Random(1616)  # deterministic synthetic key
    while True:
        p, q = _prime(rng), _prime(rng)
        if p != q and (p - 1) % 65537 and (q - 1) % 65537 and (p * q).bit_length() == 2048:
            return p * q, pow(65537, -1, (p - 1) * (q - 1))


N, D = _rsa()
b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
JWKS = {
    "keys": [
        {
            "kty": "RSA",
            "use": "sig",
            "kid": "k1",
            "n": b64(N.to_bytes(256, "big")),
            "e": b64((65537).to_bytes(3, "big")),
        }
    ]
}


def token(alg="RS256", kid="k1", sign=True, **over):
    claims = {
        "iss": ISS,
        "aud": BASE + "/mcp",
        "exp": NOW + 600,
        "client_id": "cid-one",
        "scope": "buzz:read",
        "sub": "user-claude",
        **over,
    }
    h, p = b64(json.dumps({"alg": alg, "kid": kid}).encode()), b64(json.dumps(claims).encode())
    info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(f"{h}.{p}".encode()).digest()
    em = b"\0\1" + b"\xff" * (256 - len(info) - 3) + b"\0" + info
    sig = pow(int.from_bytes(em, "big"), D, N).to_bytes(256, "big") if sign else bytes(256)
    return f"{h}.{p}.{b64(sig)}"


class Server(unittest.TestCase):
    def start(self, fake=None, writes=((("life-os", CHAN)),), fetch=None, origins=(), keys=None, access=None):
        call_limit.reset()  # each test starts with a fresh call count
        self.fake, keys = fake or FakeRelay(), KEYS if keys is None else keys
        self.relays = {
            i: Relay(URLS, {c: k for (ii, c), k in keys.items() if ii == i}, writes, self.fake)
            for i in ("claude", "chatgpt")
            if any(ii == i for ii, _ in keys)
        }
        self.audit = []
        verifier = Verifier(
            ISS, BASE + "/mcp", CLIENTS, ISS + "/jwks", USERS, fetch=fetch or (lambda url: JWKS), clock=lambda: NOW
        )
        self.receipts = []
        srv = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            buzz_mcp.handler(
                self.relays,
                verifier,
                BASE,
                origins,
                self.audit.append,
                open_access() if access is None else (None if access is False else access),
                self.receipts.append,
            ),
        )
        threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.url = f"http://127.0.0.1:{srv.server_address[1]}"

    def req(self, method, path, body=None, tok=None, **hdr):
        data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        headers = {
            "Accept": "application/json, text/event-stream",
            **({"Authorization": "Bearer " + tok} if tok else {}),
            **{k.replace("_", "-"): v for k, v in hdr.items()},
        }
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + path, data, headers, method=method)) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def rpc(self, tok, method, params=None, id=1, **hdr):
        status, raw, _ = self.req(
            "POST", "/mcp", {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}, tok, **hdr
        )
        return status, json.loads(raw) if raw else None

    def tool(self, tok, name, **args):
        status, out = self.rpc(tok, "tools/call", {"name": name, "arguments": args})
        return (status, json.loads(out["result"]["content"][0]["text"])) if status == 200 else (status, out)


class Discovery(Server):
    def test_protected_resource_metadata_and_challenge(self):
        self.start()
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            status, raw, _ = self.req("GET", path)
            self.assertEqual(
                (status, json.loads(raw)),
                (
                    200,
                    {
                        "resource": BASE + "/mcp",
                        "authorization_servers": [ISS],
                        "scopes_supported": [BASE + "/mcpbuzz:read", BASE + "/mcpbuzz:write"],
                        "bearer_methods_supported": ["header"],
                    },
                ),
            )
        for status, _, hdr in (self.req("POST", "/mcp", {"id": 1, "method": "ping"}), self.req("GET", "/mcp")):
            self.assertEqual(status, 401)
            self.assertIn(f'resource_metadata="{BASE}/.well-known/oauth-protected-resource"', hdr["WWW-Authenticate"])
        self.assertEqual(self.fake.calls, [])


class TokenChecks(Server):
    def test_bad_tokens_are_refused_before_any_tool_runs(self):
        self.start()
        forged_hs = token(alg="HS256")
        cases = {
            "expired": token(exp=NOW - 1),
            "wrong_audience": token(aud="https://other.example/mcp"),
            "wrong_issuer": token(iss="https://evil.example"),
            "alg_none": token(alg="none"),
            "hs256_confusion": forged_hs,
            "bad_signature": token(sign=False),
            "unknown_kid": token(kid="zz"),
            "no_exp": token(exp=None),
            "bool_exp": token(exp=True),
            "not_yet_valid": token(nbf=NOW + 60),
            "garbage": "a.b.c",
            "two_parts": "a.b",
        }
        for name, tok in cases.items():
            status, _, hdr = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, tok)
            self.assertEqual(status, 401, name)
            self.assertIn('error="invalid_token"', hdr["WWW-Authenticate"], name)
        self.assertEqual(
            self.req("POST", "/mcp", {"id": 1, "method": "ping"}, token(client_id="cid-unknown"))[0], 403
        )  # valid token, client not allowlisted
        self.assertEqual(
            self.req("POST", "/mcp", {"id": 1, "method": "ping"}, token(client_id=["x"], azp=None))[0], 403
        )
        self.assertEqual(self.fake.calls, [])

    def test_unavailable_identity_provider_fails_closed_without_detail(self):
        def broken(url):
            raise RuntimeError("secret-looking-detail")

        self.start(fetch=broken)
        status, raw, _ = self.req("POST", "/mcp", {"id": 1, "method": "ping"}, token())
        self.assertEqual((status, json.loads(raw)), (503, {"error": "auth_unavailable"}))

    def test_key_rotation_refreshes_but_unknown_kid_cannot_hammer_the_idp(self):
        fetches, keys = [], {"keys": []}
        v = Verifier(
            ISS,
            BASE + "/mcp",
            CLIENTS,
            ISS + "/jwks",
            USERS,
            fetch=lambda u: (fetches.append(u), keys)[1],
            clock=lambda: NOW,
        )
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
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"})[0], 401)  # no token at all is not logged
        self.assertEqual(
            [(a["outcome"], a["reason"], a["sub"]) for a in self.audit],
            [("invalid_token", "expired", "user-claude"), ("invalid_token", None, None)],
        )
        blob = json.dumps(self.audit)
        for secret in (expired, expired.split(".")[2], expired.split(".")[1]):
            self.assertNotIn(secret, blob)

    def test_an_expired_token_is_attributed_only_after_issuer_audience_client_and_user_all_check_out(self):
        self.start()
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        for label, bad in (
            ("wrong audience", dict(aud="https://other.example/mcp")),
            ("wrong issuer", dict(iss="https://evil.example")),
            ("unauthorized client", dict(client_id="cid-unknown")),
            ("unauthorized subject", dict(sub="user-2")),
            ("unlisted subject with a real-looking name", dict(sub="user-claude-2")),
            ("no audience", dict(aud=None)),
        ):
            status, raw, _ = self.req("POST", "/mcp", ping, token(exp=NOW - 5, **bad))
            self.assertEqual((status, label), (401, label))  # still the same fail-closed 401, never 403 or 200
            self.assertEqual(json.loads(raw), {"error": "invalid_token"}, label)
        self.assertEqual(
            [(a["outcome"], a["reason"], a["sub"]) for a in self.audit], [("invalid_token", None, None)] * 6
        )  # no subject for any of them
        self.assertEqual(self.req("POST", "/mcp", ping, token(exp=NOW - 5))[0], 401)
        self.assertEqual(self.audit[-1]["reason"], "expired")
        self.assertEqual(self.audit[-1]["sub"], "user-claude")
        for tok in (token(exp=NOW - 5, sub="user-2"), token(exp=NOW - 5, aud="https://other.example/mcp")):
            self.assertNotIn(tok.split(".")[2], json.dumps(self.audit))
            self.assertNotIn(tok.split(".")[1], json.dumps(self.audit))
        self.assertEqual(self.req("POST", "/mcp", ping, token(sub="user-2"))[0], 403)
        self.assertEqual((self.audit[-1]["outcome"], self.audit[-1]["sub"]), ("user_not_authorized", "user-2"))
        self.assertEqual(self.req("POST", "/mcp", ping, token(client_id="cid-unknown"))[0], 403)

    def test_successful_calls_record_issue_expiry_and_a_token_id_that_changes_with_each_token(self):
        self.start()
        t1, t2 = token(iat=NOW - 60, exp=NOW + 600), token(iat=NOW - 30, exp=NOW + 3600)
        for tok in (t1, t1, t2):
            self.assertEqual(self.tool(tok, "buzz_list_channels", community="life-os")[0], 200)
        rows = self.audit
        self.assertEqual(
            [(r["iat"], r["exp"]) for r in rows], [(NOW - 60, NOW + 600), (NOW - 60, NOW + 600), (NOW - 30, NOW + 3600)]
        )
        self.assertEqual(
            (rows[0]["tid"] == rows[1]["tid"], rows[0]["tid"] != rows[2]["tid"], len(rows[0]["tid"])), (True, True, 8)
        )  # a renewal shows up as a new tid
        self.assertNotIn(t1.split(".")[2], json.dumps(rows))


class DualClient(Server):
    def test_both_chat_clients_use_one_endpoint_and_see_the_same_tools_and_room(self):
        self.start()
        self.fake.events = [
            {
                "id": "c" * 64,
                "pubkey": "d" * 64,
                "created_at": 2,
                "kind": 9,
                "tags": [["h", CHAN]],
                "content": "synthetic room message",
            }
        ]
        seen = {}
        for client in ("claude", "chatgpt"):
            tok = token(sub="user-" + client)
            status, init = self.rpc(
                tok,
                "initialize",
                {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": client, "version": "0"}},
            )
            self.assertEqual((status, init["result"]["protocolVersion"]), (200, "2025-03-26"))
            seen[client] = (
                self.rpc(tok, "tools/list")[1]["result"],
                self.tool(tok, "buzz_get_messages", community="life-os", channel=CHAN),
            )
        self.assertEqual(seen["claude"], seen["chatgpt"])
        self.assertEqual(
            [t["name"] for t in seen["claude"][0]["tools"]],
            [
                "buzz_capabilities",
                "buzz_read",
                "buzz_publish",
                "buzz_list_channels",
                "buzz_get_messages",
                "buzz_search_messages",
                "buzz_get_addressed",
                "buzz_post_message",
            ],
        )
        self.assertEqual(seen["claude"][1][1][0]["untrusted_text"], "synthetic room message")
        self.assertEqual({a["client"] for a in self.audit}, {"claude", "chatgpt"})

    def test_audit_identity_comes_from_the_signed_token_not_from_the_caller(self):
        self.start()
        tok = token(sub="user-chatgpt")
        self.req(
            "POST",
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "buzz_list_channels", "arguments": {"community": "life-os"}, "client": "claude"},
            },
            tok,
            X_Client="claude",
            X_Actor="claude",
        )
        self.assertEqual(
            self.tool(tok, "buzz_get_messages", community="life-os", channel=CHAN, actor="claude")[1],
            {"error": "invalid_argument"},
        )
        self.assertEqual([a["client"] for a in self.audit], ["chatgpt", "chatgpt"])
        self.assertEqual(self.audit[1]["outcome"], "invalid_argument")

    def test_scopes_split_read_from_write(self):
        self.start()
        read = token(sub="user-chatgpt")
        status, raw, hdr = self.req(
            "POST",
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "buzz_post_message", "arguments": POST},
            },
            read,
        )
        self.assertEqual(status, 403)
        self.assertIn('error="insufficient_scope"', hdr["WWW-Authenticate"])
        self.assertIn(f'scope="{BASE}/mcpbuzz:write"', hdr["WWW-Authenticate"])
        self.assertEqual((self.fake.calls, self.fake.events), ([], []))
        self.assertEqual(self.tool(read, "buzz_list_channels", community="life-os")[0], 200)
        self.assertEqual(
            self.tool(token(scope="buzz:write"), "buzz_list_channels", community="life-os")[0], 403
        )  # write scope does not imply read

    def test_writes_stay_disabled_without_an_allowlisted_channel_even_with_the_scope(self):
        self.start(writes=())
        self.assertEqual(
            self.tool(token(scope="buzz:read buzz:write"), "buzz_post_message", **POST),
            (200, {"error": "write_disabled"}),
        )
        self.assertEqual(self.fake.events, [])

    def test_wrong_community_and_wrong_channel_are_denied(self):
        self.start()
        tok = token(scope="buzz:read buzz:write")
        self.assertEqual(
            self.tool(tok, "buzz_list_channels", community="other")[1], {"error": "community_not_authorized"}
        )
        self.assertEqual(
            self.tool(tok, "buzz_post_message", **dict(POST, community="bytalos"))[1], {"error": "write_disabled"}
        )
        self.assertEqual(
            self.tool(tok, "buzz_post_message", **dict(POST, channel=OTHER))[1], {"error": "write_disabled"}
        )
        self.assertEqual(self.fake.events, [])

    def test_idempotency_is_scoped_to_the_signing_identity_and_survives_restart(self):
        self.start()
        tc, tg = token(sub="user-claude", **WR), token(sub="user-chatgpt", **WR)
        first, other = self.tool(tc, "buzz_post_message", **POST)[1], self.tool(tg, "buzz_post_message", **POST)[1]
        self.assertEqual(
            (first["status"], other["status"], len(self.fake.events)), ("sent_not_read_back", "sent_not_read_back", 2)
        )
        self.assertNotEqual((first["actor"], first["event_id"]), (other["actor"], other["event_id"]))
        self.assertEqual(self.tool(tc, "buzz_post_message", **POST)[1]["status"], "already_delivered")
        self.assertEqual(
            self.tool(tg, "buzz_post_message", **dict(POST, text="different"))[1], {"error": "idempotency_conflict"}
        )
        self.start(fake=self.fake)
        self.assertEqual(
            self.tool(tc, "buzz_post_message", **dict(POST, text="different"))[1], {"error": "idempotency_conflict"}
        )
        self.assertEqual(
            (
                self.tool(tg, "buzz_post_message", **POST)[1]["status"],
                self.tool(tc, "buzz_post_message", **POST)[1]["status"],
            ),
            ("already_delivered",) * 2,
        )
        self.assertEqual(len(self.fake.events), 2)

    def test_concurrent_posts_from_both_clients_create_one_event_per_identity(self):
        self.start()
        gate, out = threading.Barrier(8), []

        def go(i):
            gate.wait()
            out.append(
                self.tool(
                    token(sub=("user-claude", "user-chatgpt")[i % 2], scope="buzz:write"), "buzz_post_message", **POST
                )[1]["status"]
            )

        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(
            (sorted(out), len(self.fake.events)), (["already_delivered"] * 6 + ["sent_not_read_back"] * 2, 2)
        )


class GrantLimitedCapabilities(Server):
    """Refusal-only tests: no Gmail or Drive adapter is invoked."""

    def test_claude_sees_google_unavailable_and_gets_no_grant_without_a_read(self):
        self.start(access=False)
        tok = token(sub="user-claude")
        status, caps = self.tool(tok, "buzz_capabilities")
        self.assertEqual(status, 200)
        for op in ("gmail_search", "gmail_read", "drive_search", "drive_read"):
            self.assertEqual(
                (caps["data"]["read"][op]["available"], caps["data"]["read"][op]["reason"]), (False, "no_grant")
            )
        status, refused = self.tool(
            tok,
            "buzz_read",
            operation="gmail_search",
            arguments={"account": "personal", "query": "synthetic-refusal-only"},
        )
        self.assertEqual((status, refused), (200, {"error": "access_denied", "reason": "no_grant"}))
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.receipts[-1]["reason"], "no_grant")


class Governed(Server):
    """every Buzz tool call goes through authorization_gate.execute, through the real HTTP handler"""

    def calls(self):
        return [c for c in self.fake.calls] if hasattr(self.fake, "calls") else None

    def test_a_granted_read_runs_and_the_receipt_names_client_tenant_and_purpose(self):
        self.start()
        tok = token(sub="user-claude")
        status, out = self.tool(tok, "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual(status, 200)
        self.assertTrue(out["ok"])
        allow, done = self.receipts[-2:]
        self.assertEqual(
            (allow["outcome"], allow["tenant"], allow["tool"], allow["purpose"], allow["action"]),
            ("allow", "personal", "buzz_read", "buzz-read", "read"),
        )
        self.assertEqual(done["outcome"], "ok")
        self.assertEqual(len(allow["principal_fp"]), 8)
        self.assertNotIn("claude-chat", json.dumps(self.receipts))  # fingerprints only

    def refused(self, tok, name, reason, **args):
        before = len(self.fake.posted) if hasattr(self.fake, "posted") else 0
        status, out = self.tool(tok, name, **args)
        self.assertEqual((status, out["error"], out["reason"]), (200, "access_denied", reason))
        self.assertEqual(self.receipts[-1]["outcome"], "deny")
        self.assertEqual(self.receipts[-1]["reason"], reason)

    def test_an_ungranted_tool_is_refused_before_the_relay(self):
        from tests.platform.mcp_access_fixture import build

        self.start(access=build(grants=lambda seeded: [g for g in seeded if g["tool"] != "buzz_publish"]))
        self.refused(
            token(sub="user-claude", **WR),
            "buzz_publish",
            "no_grant",
            operation="message",
            community="life-os",
            arguments={"channel": CHAN, "text": "hi", "idempotency_key": "k9"},
        )

    def test_the_shipped_publish_grant_passes_the_gate_with_a_write_receipt_and_the_relay_allowlist_still_applies(self):
        """the shipped seed grants buzz_publish to the two chat clients in the communities they can write to"""
        from tests.platform.mcp_access_fixture import build

        self.start(access=build())
        status, out = self.tool(
            token(sub="user-claude", **WR),
            "buzz_publish",
            operation="message",
            community="life-os",
            arguments={"channel": CHAN, "text": "hi", "idempotency_key": "k9"},
        )
        self.assertEqual(status, 200)
        allow = self.receipts[-2]
        self.assertEqual(
            (allow["outcome"], allow["tool"], allow["action"], allow["purpose"], allow["tenant"]),
            ("allow", "buzz_publish", "write", "buzz-publish", "personal"),
        )

    def test_revoked_expired_wrong_tenant_and_wrong_purpose_grants_are_refused_with_their_reason(self):
        from tests.platform.mcp_access_fixture import build

        def edit(**patch):
            return build(
                grants=lambda seeded: [
                    {**g, **patch} if g["principal"] == "claude-chat" and g["tool"] == "buzz_read" else g
                    for g in seeded
                ]
            )

        tok = token(sub="user-claude")
        self.start(access=edit(revoked=True))
        self.refused(tok, "buzz_read", "grant_revoked", operation="list_channels", community="life-os")
        self.start(access=edit(expires="2026-11-01"))
        self.refused(tok, "buzz_read", "grant_expired", operation="list_channels", community="life-os")
        self.start(access=edit(purpose="something-else"))
        self.refused(tok, "buzz_read", "purpose_mismatch", operation="list_channels", community="life-os")
        self.start(access=build(grants=lambda seeded: [g for g in seeded if g["tenant"] != "bytalos"]))
        self.refused(
            tok, "buzz_read", "no_grant", operation="list_channels", community="bytalos"
        )  # a Bytalos call with only Personal grants

    def test_a_spoofed_identity_is_ignored_and_an_unregistered_client_is_refused(self):
        from tests.platform.mcp_access_fixture import build

        self.start()
        tok = token(sub="user-chatgpt")
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "buzz_read",
                "arguments": {
                    "operation": "list_channels",
                    "community": "life-os",
                    "principal": "claude-chat",
                    "client": "claude",
                },
                "principal": "claude-chat",
            },
        }
        status, raw, _ = self.req(
            "POST", "/mcp", body, tok, X_Client="claude", X_Principal="claude-chat", X_Lifeos_Client_Id="claude-chat"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(json.loads(raw)["result"]["content"][0]["text"])["error"], "invalid_argument"
        )  # an unknown argument never reaches identity at all
        self.tool(tok, "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual(
            self.receipts[-2]["principal_fp"],
            __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint("chatgpt-chat"),
        )
        self.start(
            access=build(
                clients=[
                    c
                    for c in __import__("tests.platform.mcp_access_fixture", fromlist=["x"]).DOC["clients"]
                    if c["id"] != "chatgpt-chat"
                ]
            )
        )
        status, out = self.tool(tok, "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual((status, out["error"], out["reason"]), (200, "access_denied", "identity_unknown"))

    def test_without_readable_access_data_every_tool_call_is_refused(self):
        self.start(access=False)
        from unittest import mock

        with mock.patch("lifeos.platform.mcp_access.load", side_effect=OSError("data file missing")):
            status, out = self.tool(
                token(sub="user-claude"), "buzz_read", operation="list_channels", community="life-os"
            )
        self.assertEqual((status, out["error"]), (200, "governance_unavailable"))


class Credentials(Server):
    """scoped `lm1.` credentials for the local clients, through the real HTTP handler."""

    SECRETS = {
        "mac-claude-code": "A" * 43,
        "oracle-claude-code": "B" * 43,
        "openclaw": "D" * 43,
        "hermes-librarian": "E" * 43,
        "honcho-alerts": "F" * 43,
        "muse": "G" * 43,
        "jira-capture": "H" * 43,
    }

    def access(self, edit=None, no_identity=False):
        import copy
        from lifeos.platform import mcp_access
        from tests.platform.mcp_access_fixture import CATALOG, DOC

        doc = copy.deepcopy(DOC)
        if not any(r["id"] == "muse" for r in doc["credentials"]):  # the shipped muse row carries a real hash
            doc["credentials"].append(
                {
                    "id": "muse",
                    "sha256": "0" * 64,
                    "expires": "2027-02-22",
                    "revoked": False,
                    "buzz_identity": "muse",
                    "communities": ["life-os"],
                    "write": True,
                }
            )
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
        for cid in (
            c
            for c in self.SECRETS
            if c not in ("openclaw", "hermes-librarian", "honcho-alerts", "muse", "jira-capture")
        ):
            status, out = self.rpc(self.tok(cid), "tools/list")
            names = {t["name"] for t in out["result"]["tools"]}
            self.assertEqual(
                (
                    status,
                    {"buzz_capabilities", "buzz_read"} <= names,
                    "buzz_publish" in names or "buzz_post_message" in names,
                ),
                (200, True, False),
                cid,
            )
            status, caps = self.tool(self.tok(cid), "buzz_capabilities")
            self.assertEqual((status, caps["ok"]), (200, True), cid)
            allow, done = self.receipts[-2:]
            self.assertEqual(
                (allow["outcome"], allow["tenant"], allow["tool"], allow["purpose"], done["outcome"]),
                ("allow", "personal", "buzz_capabilities", "buzz-read", "ok"),
            )
            self.assertEqual(
                allow["principal_fp"], __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint(cid)
            )
        self.assertNotIn("mac-claude-code", json.dumps(self.receipts))  # fingerprints only

    def test_a_call_outside_the_clients_grants_is_refused_no_grant_before_the_relay(self):
        self.start(
            access=self.access(
                edit=lambda d: d.update(
                    grants=[
                        g for g in d["grants"] if not (g["principal"] == "mac-claude-code" and g["tool"] == "buzz_read")
                    ]
                )
            )
        )
        status, out = self.tool(self.tok(), "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual((status, out["error"], out["reason"]), (200, "access_denied", "no_grant"))
        self.assertEqual(self.receipts[-1]["outcome"], "deny")
        self.assertEqual(self.fake.posted if hasattr(self.fake, "posted") else [], [])

    def test_a_credential_cannot_publish_or_leave_its_community(self):
        self.start(access=self.access())
        status, out = self.tool(
            self.tok(),
            "buzz_publish",
            operation="message",
            community="life-os",
            arguments={"channel": CHAN, "text": "x", "idempotency_key": "k1"},
        )
        self.assertEqual(status, 403)  # read scope only
        status, out = self.tool(self.tok(), "buzz_read", operation="list_channels", community="bytalos")
        self.assertEqual((status, out["error"]), (200, "community_not_authorized"))

    def test_muse_bearer_reads_sends_to_jim_and_publishes_to_the_allowlisted_channel_only_and_everything_else_is_refused(
        self,
    ):
        """the muse credential row alone may hold the write scope"""
        from datetime import datetime, timezone
        from unittest import mock

        self.start(access=self.access())
        self.relays["muse"] = Relay(URLS, {"life-os": bytes([25]) * 32}, (("life-os", CHAN),), self.fake)
        tok = self.tok("muse")
        status, out = self.rpc(tok, "tools/list")
        self.assertEqual(
            (status, {"buzz_capabilities", "buzz_read", "buzz_publish"} <= {t["name"] for t in out["result"]["tools"]}),
            (200, True),
        )
        status, out = self.tool(tok, "buzz_read", operation="list_channels", community="life-os")
        self.assertEqual((status, out["ok"]), (200, True))
        status, out = self.tool(
            tok,
            "buzz_publish",
            operation="message",
            community="life-os",
            arguments={"channel": CHAN, "text": "synthetic muse post", "idempotency_key": "muse-k1"},
        )
        self.assertEqual(
            (status, out.get("ok"), self.receipts[-2]["action"], self.receipts[-2]["tool"]),
            (200, True, "write", "buzz_publish"),
        )
        status, out = self.tool(
            tok,
            "buzz_publish",
            operation="message",
            community="life-os",
            arguments={"channel": OTHER, "text": "x", "idempotency_key": "muse-k2"},
        )
        self.assertFalse(out.get("ok"), out)  # a channel outside the allowlist is refused
        sent = []
        with (
            mock.patch.dict(
                buzz_mcp.INBOX, {"client": object(), "now": lambda: datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)}
            ),
            mock.patch(
                "lifeos.platform.inbox_send.deliver",
                lambda item, client, now=None: sent.append(item) or {"ok": True, "created": True, "id": "x"},
            ),
        ):
            status, out = self.tool(
                tok,
                "buzz_read",
                operation="send_to_jim",
                community="life-os",
                arguments={"subject": "Muse synthetic test", "urgency": "FYI", "from": "claude-chat"},
            )
            self.assertEqual((out.get("ok"), sent), (False, []))  # a caller-named sender is refused
            status, out = self.tool(
                tok,
                "buzz_read",
                operation="send_to_jim",
                community="life-os",
                arguments={"subject": "Muse synthetic test", "urgency": "FYI"},
            )
        self.assertEqual((status, [i["from"] for i in sent]), (200, ["muse"]))
        for op, args in (
            ("gmail_search", {"account": "personal", "query": "x"}),
            ("drive_search", {"account": "personal", "query": "x"}),
        ):
            status, out = self.tool(tok, "buzz_read", operation=op, community="life-os", arguments=args)
            self.assertEqual((out["error"], out["reason"]), ("access_denied", "no_grant"), op)
        status, out = self.tool(tok, "buzz_read", operation="list_channels", community="bytalos")
        self.assertEqual(out["error"], "community_not_authorized")

    def test_duet_is_the_oauth_label_of_its_own_user_with_claudes_grants_and_never_claude_or_a_spoofed_sender(self):
        """the Duet connector signs in as its own IdP user, labelled `duet`"""
        from datetime import datetime, timezone
        from unittest import mock

        USERS["user-duet"] = ("duet", ("life-os", "bytalos"))
        self.addCleanup(USERS.pop, "user-duet", None)
        self.start(access=self.access())
        self.relays["duet"] = Relay(
            URLS, {"life-os": bytes([26]) * 32, "bytalos": bytes([27]) * 32}, (("life-os", CHAN),), self.fake
        )
        tok, web = token(sub="user-duet"), token(sub="user-claude")
        self.assertEqual(
            self.tool(tok, "buzz_capabilities")[1]["data"]["identity"]["client"], "duet"
        )  # Duet's own identity
        self.assertEqual(
            self.tool(web, "buzz_capabilities")[1]["data"]["identity"]["client"], "claude"
        )  # Claude web is unchanged
        status, out = self.tool(tok, "buzz_read", operation="list_channels", community="bytalos")
        self.assertEqual(
            (status, out["ok"], self.receipts[-2]["tenant"], self.receipts[-2]["purpose"]),
            (200, True, "bytalos", "buzz-read"),
        )
        sent = []
        with (
            mock.patch.dict(
                buzz_mcp.INBOX, {"client": object(), "now": lambda: datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)}
            ),
            mock.patch(
                "lifeos.platform.inbox_send.deliver", lambda item, client, now=None: sent.append(item) or {"ok": True}
            ),
        ):
            status, out = self.tool(
                tok,
                "buzz_read",
                operation="send_to_jim",
                community="life-os",
                arguments={"subject": "Duet spoof", "urgency": "FYI", "from": "claude-chat"},
            )
            self.assertEqual((out.get("ok"), sent), (False, []))
            self.tool(
                tok,
                "buzz_read",
                operation="send_to_jim",
                community="life-os",
                arguments={"subject": "Duet synthetic test", "urgency": "FYI"},
            )
        self.assertEqual([i["from"] for i in sent], ["duet"])  # the sender is the verified label
        before = len(self.fake.calls)
        for op, args in (
            ("gmail_search", {"account": "personal", "query": "x"}),
            ("drive_search", {"account": "personal", "query": "x"}),
        ):
            status, out = self.tool(tok, "buzz_read", operation=op, community="life-os", arguments=args)
            self.assertEqual((out["error"], out["reason"]), ("access_denied", "no_grant"), op)  # refused by the gate
        self.assertEqual(len(self.fake.calls), before)  # and before any request left the service

    def test_a_second_public_host_names_its_own_resource_and_challenge_and_accepts_its_own_audience_and_nothing_else_changes(
        self,
    ):
        """the same service answers on a second public origin"""
        from lifeos.buzz.auth import Verifier as V

        second = "https://second.example.test"
        self.start()
        verifier = V(
            ISS,
            BASE + "/mcp",
            CLIENTS,
            ISS + "/jwks",
            USERS,
            fetch=lambda url: JWKS,
            clock=lambda: NOW,
            extra_audiences=(second + "/mcp",),
        )
        srv = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            buzz_mcp.handler(self.relays, verifier, BASE, (), self.audit.append, open_access(), self.receipts.append),
        )
        threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.url = f"http://127.0.0.1:{srv.server_address[1]}"
        for host, origin in (
            ("edge.example.test", BASE),
            ("second.example.test", second),
            ("unknown.example.test", BASE),
        ):
            status, raw, _ = self.req("GET", "/.well-known/oauth-protected-resource", Host=host)
            self.assertEqual(
                (status, json.loads(raw)["resource"], json.loads(raw)["scopes_supported"][0]),
                (200, origin + "/mcp", BASE + "/mcpbuzz:read"),
                host,
            )  # scope names keep the primary prefix
            status, _, headers = self.req(
                "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, Host=host
            )
            self.assertEqual(status, 401)
            self.assertIn(
                f'resource_metadata="{origin}/.well-known/oauth-protected-resource"', headers["WWW-Authenticate"], host
            )
            both = f'scope="{BASE}/mcpbuzz:read {BASE}/mcpbuzz:write"'
            self.assertEqual(both in headers["WWW-Authenticate"], origin != BASE, host)
        self.assertEqual(
            self.rpc(token(), "tools/list", Host="edge.example.test")[0], 200
        )  # the primary audience still works on every host
        self.assertEqual(
            self.rpc(token(aud=second + "/mcp"), "tools/list", Host="second.example.test")[0], 200
        )  # the second audience works
        self.assertEqual(
            self.rpc(
                token(aud=[second + "/mcp"], scope=BASE + "/mcpbuzz:read"), "tools/list", Host="second.example.test"
            )[0],
            200,
        )
        self.assertEqual(
            self.rpc(token(aud="https://other.example.test/mcp"), "tools/list")[0], 401
        )  # any other audience is refused
        self.assertEqual(
            self.rpc(token(aud=second + "/mcp", sub="user-nobody"), "tools/list")[0], 403
        )  # the allowlists are the same on both hosts

    def test_duet_may_address_agents_when_it_is_a_mention_sender_and_claude_and_chatgpt_are_unchanged(self):
        """addressing is a per-identity flag (BUZZ_MENTION_SENDERS)"""
        base = {
            "BUZZ_URL_LIFE_OS": "https://life-os.communities.buzz.xyz",
            "BUZZ_KEY_CLAUDE_LIFE_OS": "01" * 32,
            "BUZZ_KEY_CHATGPT_LIFE_OS": "02" * 32,
            "BUZZ_KEY_DUET_LIFE_OS": "03" * 32,
            "BUZZ_OAUTH_ISSUER": ISS,
            "BUZZ_OAUTH_JWKS_URL": ISS + "/jwks",
            "BUZZ_OAUTH_CLIENTS": "cid-one",
            "BUZZ_MCP_PUBLIC_URL": BASE,
            "BUZZ_OAUTH_USERS": "user-claude:claude:life-os,user-chatgpt:chatgpt:life-os,user-duet:duet:life-os",
            "BUZZ_RECIPIENTS": "hermes:life-os:" + "ab" * 32,
        }
        flags = lambda env: {i: r.may_address for i, r in buzz_mcp.from_env(env)[0].items()}
        self.assertEqual(
            flags({**base, "BUZZ_MENTION_SENDERS": "claude,chatgpt"}),
            {"claude": True, "chatgpt": True, "duet": False, "openclaw": False},
        )
        self.assertEqual(
            flags({**base, "BUZZ_MENTION_SENDERS": "claude,chatgpt,duet"}),
            {"claude": True, "chatgpt": True, "duet": True, "openclaw": False},
        )

    def test_the_extra_public_hosts_setting_is_origin_only_and_never_the_primary(self):
        env = {"BUZZ_MCP_PUBLIC_URL": BASE, "BUZZ_MCP_PUBLIC_URLS_EXTRA": "https://second.example.test/"}
        self.assertEqual(self.extras(env), ("https://second.example.test/mcp",))
        for bad in ("https://second.example.test/path", BASE, "http://second.example.test"):
            with self.assertRaises(ValueError):
                self.extras({**env, "BUZZ_MCP_PUBLIC_URLS_EXTRA": bad})

    @staticmethod
    def extras(env):
        full = {
            "BUZZ_URL_LIFE_OS": "https://life-os.communities.buzz.xyz",
            "BUZZ_KEY_CLAUDE_LIFE_OS": "01" * 32,
            "BUZZ_OAUTH_ISSUER": ISS,
            "BUZZ_OAUTH_JWKS_URL": ISS + "/jwks",
            "BUZZ_OAUTH_CLIENTS": "cid-one",
            "BUZZ_OAUTH_USERS": "user-claude:claude:life-os",
            **env,
        }
        return buzz_mcp.from_env(full)[1].extra_audiences

    def test_no_other_credential_can_hold_the_write_scope_and_the_file_refuses_to_load_if_one_tries(self):
        from lifeos.platform import mcp_access
        from tests.platform.mcp_access_fixture import CATALOG, DOC

        self.start(access=self.access())
        for cid in ("mac-claude-code", "oracle-claude-code", "hermes-librarian", "honcho-alerts"):
            status, out = self.tool(
                self.tok(cid),
                "buzz_publish",
                operation="message",
                community="life-os",
                arguments={"channel": CHAN, "text": "x", "idempotency_key": "k1"},
            )
            self.assertEqual(status, 403, cid)  # read scope only, whatever the grants say
            doc = json.loads(json.dumps(DOC))
            next(r for r in doc["credentials"] if r["id"] == cid)["write"] = True
            with self.assertRaises(mcp_access.AccessError, msg=cid):
                mcp_access.load(json.dumps(doc), json.dumps(CATALOG))
        self.assertEqual(
            sorted(r["id"] for r in DOC["credentials"] if r.get("write")),
            ["muse", "openclaw"] if any(r["id"] == "muse" for r in DOC["credentials"]) else ["openclaw"],
        )  # muse and openclaw only

    def test_bad_credentials_are_one_401_and_no_tool_runs(self):
        self.start(access=self.access(edit=lambda d: d["credentials"][1].update(revoked=True)))
        expired = self.access(edit=lambda d: d["credentials"][0].update(expires="2026-11-01"))
        bad = [
            self.tok(secret="C" * 43),
            self.tok("no-such-client", "A" * 43),
            self.tok("mac-claude-code", self.SECRETS["oracle-claude-code"]),
            self.tok("oracle-claude-code"),
            "lm1.mac-claude-code",
            "lm1.mac-claude-code.short",
            "lm1..x.y.z",
        ]  # revoked, malformed
        for tok in bad:
            status, out = self.rpc(tok, "tools/call", {"name": "buzz_capabilities", "arguments": {}})
            self.assertEqual((status, out), (401, {"error": "invalid_token"}), tok[:20])
        self.assertEqual(self.receipts, [])
        self.start(access=expired)
        self.assertEqual(self.rpc(self.tok(), "tools/list")[0], 401)

    def test_one_credential_never_becomes_another_client_whatever_the_caller_sends(self):
        self.start(access=self.access())
        status, raw, _ = self.req(
            "POST",
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "buzz_capabilities", "arguments": {}},
            },
            self.tok("oracle-claude-code"),
            X_Client="mac-claude-code",
            X_Principal="mac-claude-code",
            X_Lifeos_Client_Id="mac-claude-code",
            X_Forwarded_User="jimmarkunas",
        )
        self.assertEqual(status, 200)
        fp = __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint
        self.assertEqual(self.receipts[-2]["principal_fp"], fp("oracle-claude-code"))
        self.tool(self.tok("mac-claude-code"), "buzz_capabilities")
        self.assertNotEqual(
            self.receipts[-2]["principal_fp"], fp("oracle-claude-code")
        )  # same host, two clients, two identities

    def test_a_credential_with_no_buzz_identity_sees_capabilities_but_makes_no_relay_call(self):
        self.start(access=self.access(no_identity=True))
        status, caps = self.tool(self.tok("oracle-claude-code"), "buzz_capabilities")
        self.assertEqual((status, caps["ok"], caps["data"]["identity"]["communities"]), (200, True, {}))
        status, out = self.tool(
            self.tok("oracle-claude-code"), "buzz_read", operation="list_channels", community="life-os"
        )
        self.assertEqual((status, out["error"]), (200, "client_identity_not_configured"))

    def test_an_unknown_operation_is_named_with_the_callers_own_valid_operations_not_an_identity_error(self):
        """a guessed operation (even the tool name buzz_capabilities) is unknown_operation with the caller's granted
        list, before any identity check
        """
        self.start(access=self.access(no_identity=True))
        tok = self.tok("oracle-claude-code")
        for wrong in ("nonsense", "buzz_capabilities", "memory_recll"):
            status, out = self.tool(tok, "buzz_read", operation=wrong)
            self.assertEqual((status, out["error"], out["operation"]), (200, "unknown_operation", wrong))
            self.assertIn("list_channels", out["valid"])
            self.assertNotIn("buzz_capabilities", out["valid"])
        status, out = self.tool(tok, "buzz_publish", operation="buzz_capabilities")
        self.assertEqual(out["error"], "insufficient_scope")
        out = self.tool(self.tok("honcho-alerts"), "buzz_read", operation="nonsense")[1]
        self.assertEqual((out["error"], "valid" in out), ("access_denied", False))
        self.assertEqual(
            self.tool(tok, "buzz_read", operation="list_channels", community="life-os")[1]["error"],
            "client_identity_not_configured",
        )  # a real operation that needs an identity: unchanged
        self.assertEqual(
            self.tool(tok, "buzz_read", operation="list_channels", community="bytalos")[1]["error"],
            "community_not_authorized",
        )  # refusals unchanged

    def test_a_memory_client_is_told_its_memory_operations_when_it_guesses_wrong(self):
        self.start(access=self.access())
        status, out = self.tool(self.tok("openclaw"), "buzz_read", operation="buzz_capabilities")
        self.assertEqual((status, out["error"]), (200, "unknown_operation"))
        self.assertTrue({"memory_recall", "memory_store"} <= set(out["valid"]), out["valid"])
        self.assertNotIn("buzz_capabilities", out["valid"])

    def test_honcho_alerts_send_to_jim_needs_no_community_and_names_none(self):
        """the alert client sends without a community; the sender is still the verified client"""
        from datetime import datetime, timezone
        from unittest import mock

        sent = []
        self.start(access=self.access(no_identity=True))
        with (
            mock.patch.dict(
                buzz_mcp.INBOX, {"client": object(), "now": lambda: datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)}
            ),
            mock.patch(
                "lifeos.platform.inbox_send.deliver",
                lambda item, client, now=None: sent.append(item) or {"ok": True, "created": 1},
            ),
        ):
            status, out = self.tool(
                self.tok("honcho-alerts"),
                "buzz_read",
                operation="send_to_jim",
                arguments={"subject": "Honcho budget stopped", "urgency": "Today"},
            )
            self.assertEqual((status, out.get("ok"), [i["from"] for i in sent]), (200, True, ["honcho-alerts"]))
            status, out = self.tool(
                self.tok("honcho-alerts"),
                "buzz_read",
                operation="send_to_jim",
                community="not-granted",
                arguments={"subject": "x", "urgency": "Today"},
            )
            self.assertEqual(
                (out.get("ok"), len(sent)), (False, 1)
            )  # a community the client does not hold is still refused

    def test_honcho_alerts_may_send_to_jim_only_and_is_named_as_the_sender(self):
        """the alert client holds one grant, send_to_jim"""
        from datetime import datetime, timezone
        from unittest import mock

        sent = []
        self.start(access=self.access(no_identity=True))
        with (
            mock.patch.dict(
                buzz_mcp.INBOX, {"client": object(), "now": lambda: datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)}
            ),
            mock.patch(
                "lifeos.platform.inbox_send.deliver",
                lambda item, client, now=None: sent.append(item) or {"ok": True, "created": 1},
            ),
        ):
            status, out = self.tool(
                self.tok("honcho-alerts"),
                "buzz_read",
                operation="send_to_jim",
                community="life-os",
                arguments={"subject": "Honcho backup failed", "urgency": "Today"},
            )
            self.assertEqual(
                (status, out.get("ok"), (out.get("data") or out).get("sender", out), sent[0]["from"] if sent else None),
                (200, True, "honcho-alerts", "honcho-alerts"),
            )
            status, out = self.tool(
                self.tok("honcho-alerts"),
                "buzz_read",
                operation="source_read",
                community="life-os",
                arguments={"source": "github", "path": "AGENTS.md"},
            )
            self.assertFalse(out.get("ok"), out)
            self.assertEqual(len(sent), 1)

    def test_source_read_and_agent_lookup_are_their_own_gate_tools_for_every_proved_client_and_not_for_the_chat_clients(
        self,
    ):
        """the two lookups are granted to the four proved clients only"""
        from datetime import datetime, timedelta
        from unittest import mock
        from lifeos.platform import agent_directory
        from tests.platform import agent_directory_fixture as F

        fp = __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint
        now = datetime(2026, 11, 16, 12, 0, tzinfo=agent_directory.CENTRAL)
        github = lambda path, ref: {
            "commit": "a" * 40,
            "last_change": "b" * 40,
            "blob": "c" * 40,
            "modified": now - timedelta(days=1),
            "text": "policy text",
        }
        self.start(access=self.access(no_identity=True))
        with mock.patch.dict(
            buzz_mcp.LOOKUP, {"fetchers": {"github": github}, "rows": lambda: F.rows(), "now": lambda: now}
        ):
            for cid in (c for c in self.SECRETS if c not in ("honcho-alerts", "muse", "jira-capture")):
                comm = "pilot" if cid == "openclaw" else "life-os"  # openclaw's only community
                status, out = self.tool(
                    self.tok(cid),
                    "buzz_read",
                    operation="source_read",
                    community=comm,
                    arguments={"source": "github", "path": "AGENTS.md"},
                )
                self.assertEqual(
                    (status, out["ok"], out["data"]["revision"]["commit_sha"], out["data"]["freshness"]),
                    (200, True, "a" * 40, "fresh"),
                    cid,
                )
                allow = self.receipts[-2]
                self.assertEqual(
                    (allow["tool"], allow["purpose"], allow["outcome"], allow["principal_fp"]),
                    ("source_read", "source-read", "allow", fp(cid)),
                )
                status, out = self.tool(
                    self.tok(cid),
                    "buzz_read",
                    operation="agent_lookup",
                    community=comm,
                    arguments={"agent_id": "bot_hermes_claude-hermes"},
                )
                self.assertEqual(
                    (status, out["ok"], out["data"]["status"], out["data"]["authorizes"]),
                    (200, True, "configured-untested", []),
                    cid,
                )
                self.assertEqual(
                    (self.receipts[-2]["tool"], self.receipts[-2]["purpose"]), ("agent_lookup", "agent-lookup")
                )
            status, out = self.tool(
                self.tok(),
                "buzz_read",
                operation="source_read",
                community="life-os",
                arguments={"source": "github", "path": "AGENTS.md", "max_age_seconds": 60},
            )
            self.assertEqual((status, out["error"]), (200, "stale_source"))
            status, out = self.tool(
                self.tok(), "buzz_read", operation="source_read", community="life-os", arguments={"source": "jira"}
            )
            self.assertEqual(out["error"], "unknown_source")
            before = len(self.receipts)
            status, out = self.tool(
                token(sub="user-claude"),
                "buzz_read",
                operation="source_read",
                community="life-os",
                arguments={"source": "github", "path": "AGENTS.md"},
            )
            self.assertEqual(
                (out["error"], out["reason"], self.receipts[-1]["outcome"]),
                ("access_denied", "catalog_unknown_capability", "deny"),
            )  # chat clients hold no lookup grant
            self.assertEqual(len(self.receipts), before + 1)

    def test_an_unwired_lookup_says_so_with_a_fixed_reason(self):
        from unittest import mock

        self.start(access=self.access())
        with mock.patch.dict(buzz_mcp.LOOKUP, {"fetchers": {}, "rows": None}):
            self.assertEqual(
                self.tool(
                    self.tok(),
                    "buzz_read",
                    operation="source_read",
                    community="life-os",
                    arguments={"source": "notion", "page_id": "4183c5a0-5926-81be-9e19-e85714dc256f"},
                )[1]["error"],
                "source_unavailable",
            )
            self.assertEqual(
                self.tool(
                    self.tok(),
                    "buzz_read",
                    operation="agent_lookup",
                    community="life-os",
                    arguments={"agent_id": "bot_hermes_claude-hermes"},
                )[1]["error"],
                "directory_unavailable",
            )

    def test_google_reads_are_granted_to_hermes_librarian_only_per_account_and_the_named_account_is_the_boundary(self):
        """read-only Gmail and Drive grants for the Hermes librarian credential, personal and bytalos"""
        from unittest import mock
        from tests.platform.test_google_read import FakeGoogle

        fake = FakeGoogle()
        fp = __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint
        self.start(access=self.access(no_identity=True))
        creds = lambda account, service: {
            "client_id": "cid",
            "client_secret": "csecret-xxxxxxxx",
            "refresh_token": account,
        }
        from lifeos.platform import google_read
        from tests.platform.test_google_read import SYNTHETIC_OWNER_SHA256

        with (
            mock.patch.dict(google_read.OWNER_SHA256, SYNTHETIC_OWNER_SHA256),
            mock.patch.dict(buzz_mcp.GOOGLE, {"creds": creds, "opener": fake}),
        ):
            lib = self.tok("hermes-librarian")
            for account in ("personal", "bytalos"):
                for op, args, key in (
                    ("gmail_search", {"query": "q"}, "results"),
                    ("gmail_read", {"message_id": "msg111111"}, "id"),
                    ("drive_search", {"query": "q"}, "results"),
                    ("drive_read", {"file_id": "file111111"}, "id"),
                ):
                    status, out = self.tool(lib, "buzz_read", operation=op, arguments={"account": account, **args})
                    self.assertEqual((status, out["ok"], out["data"]["account"]), (200, True, account), (account, op))
                    allow = self.receipts[-2]
                    self.assertEqual(
                        (allow["tool"], allow["purpose"], allow["tenant"], allow["outcome"], allow["principal_fp"]),
                        (
                            op,
                            "governed-google-read",
                            account,
                            "allow",
                            fp("hermes-librarian" if account == "personal" else "hermes-librarian-bytalos"),
                        ),
                    )
            reached = len(fake.requests)
            for cid in ("mac-claude-code", "oracle-claude-code", "openclaw"):  # granted to no other client
                status, out = self.tool(
                    self.tok(cid),
                    "buzz_read",
                    operation="gmail_search",
                    arguments={"account": "personal", "query": "q"},
                )
                self.assertEqual((out["error"], out["reason"]), ("access_denied", "no_grant"), cid)
            status, out = self.tool(
                token(sub="user-claude"),
                "buzz_read",
                operation="drive_read",
                arguments={"account": "personal", "file_id": "file111111"},
            )
            self.assertEqual(out["error"], "access_denied")  # nor to the chat clients
            for args, reason in (
                ({"query": "q"}, "account_required"),
                ({"account": "work", "query": "q"}, "unknown_account"),
            ):
                status, out = self.tool(lib, "buzz_read", operation="gmail_search", arguments=args)
                self.assertEqual((out["error"], out["reason"]), ("access_denied", reason))
            self.assertEqual(len(fake.requests), reached)  # none of the refusals reached Google
            names = {t["name"] for t in self.rpc(lib, "tools/list")[1]["result"]["tools"]}
            self.assertFalse(
                {n for n in names if "send" in n or "delete" in n or "modify" in n}
            )  # no write tool exists

    def test_a_revoked_google_consent_and_an_unwired_service_have_fixed_reasons(self):
        from unittest import mock
        from tests.platform.test_google_read import FakeGoogle

        self.start(access=self.access(no_identity=True))
        lib = self.tok("hermes-librarian")
        from lifeos.platform import google_read
        from tests.platform.test_google_read import SYNTHETIC_OWNER_SHA256

        with (
            mock.patch.dict(google_read.OWNER_SHA256, SYNTHETIC_OWNER_SHA256),
            mock.patch.dict(
                buzz_mcp.GOOGLE,
                {
                    "creds": lambda a, s: {"client_id": "c", "client_secret": "s" * 10, "refresh_token": a},
                    "opener": FakeGoogle(revoked=True),
                },
            ),
        ):
            self.assertEqual(
                self.tool(lib, "buzz_read", operation="drive_search", arguments={"account": "bytalos", "query": "q"})[
                    1
                ]["error"],
                "consent_revoked",
            )
        with mock.patch.dict(buzz_mcp.GOOGLE, {"creds": None, "opener": None}):
            self.assertEqual(
                self.tool(lib, "buzz_read", operation="gmail_search", arguments={"account": "personal", "query": "q"})[
                    1
                ]["error"],
                "not_configured",
            )

    def test_send_to_jim_is_governed_for_the_four_local_clients_and_the_oauth_chat_client_with_the_sender_always_the_verified_client(
        self,
    ):
        """the same governed operation for every granted client"""
        from unittest import mock
        from tests.inbox.test_agent_inbox import FakeClient

        fp = __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint
        inbox = FakeClient()
        self.start(access=self.access(no_identity=True))
        with mock.patch.dict(buzz_mcp.INBOX, {"client": inbox, "now": None}):
            for cid in ("mac-claude-code", "oracle-claude-code", "openclaw", "hermes-librarian"):
                status, out = self.tool(
                    self.tok(cid),
                    "buzz_read",
                    operation="send_to_jim",
                    arguments={"subject": f"Synthetic LIFE-876 item from {cid}"},
                )
                self.assertEqual(
                    (status, out["ok"], out["data"]["created"], out["data"]["sender"]), (200, True, 1, cid), cid
                )
                allow = self.receipts[-2]
                self.assertEqual(
                    (
                        allow["tool"],
                        allow["purpose"],
                        allow["tenant"],
                        allow["action"],
                        allow["outcome"],
                        allow["principal_fp"],
                    ),
                    ("send_to_jim", "agent-inbox", "personal", "write", "allow", fp(cid)),
                )
            status, out = self.tool(
                token(sub="user-claude"),
                "buzz_read",
                operation="send_to_jim",
                arguments={"subject": "Synthetic LIFE-876 item from the Claude chat client"},
            )
            self.assertEqual(
                (out["ok"], out["data"]["sender"]), (True, "claude")
            )  # the verified OAuth client is the sender
            self.assertEqual(len(inbox.rows), 5)
            status, again = self.tool(
                self.tok("mac-claude-code"),
                "buzz_read",
                operation="send_to_jim",
                arguments={"subject": "Synthetic LIFE-876 item from mac-claude-code"},
            )
            self.assertEqual(
                (again["ok"], "updated" in again["data"], len(inbox.rows)), (True, True, 5)
            )  # a repeat updates the same row
            status, other = self.tool(
                self.tok("oracle-claude-code"),
                "buzz_read",
                operation="send_to_jim",
                arguments={"subject": "x", "key": "k-own"},
            )
            status, taken = self.tool(
                self.tok("openclaw"), "buzz_read", operation="send_to_jim", arguments={"subject": "y", "key": "k-own"}
            )
            self.assertEqual(taken["error"], "key_other_sender")  # nobody takes another client's row
            for args, error, reason in (
                ({"subject": "x", "from": "claude-chat"}, "invalid_argument", None),
                ({"subject": "x", "workspace": "Bytalos"}, "access_denied", "catalog_unknown_principal"),
                ({"subject": "x", "workspace": "TGP"}, "access_denied", "unknown_tenant"),
            ):
                status, out = self.tool(
                    self.tok("mac-claude-code"), "buzz_read", operation="send_to_jim", arguments=args
                )
                self.assertEqual((out["error"], out.get("reason")), (error, reason), args)
            rows = len(inbox.rows)
            for cid in ("mac-claude-code",):
                self.assertEqual(
                    self.tool(
                        self.tok(cid),
                        "buzz_read",
                        operation="send_to_jim",
                        arguments={"subject": "x", "from": "someone"},
                    )[1]["error"],
                    "invalid_argument",
                )
            self.assertEqual(len(inbox.rows), rows)  # no refusal wrote a row

    def test_send_to_jim_without_a_notion_client_says_inbox_not_configured(self):
        from unittest import mock

        self.start(access=self.access(no_identity=True))
        with mock.patch.dict(buzz_mcp.INBOX, {"client": None, "now": None}):
            self.assertEqual(
                self.tool(
                    self.tok("mac-claude-code"), "buzz_read", operation="send_to_jim", arguments={"subject": "x"}
                )[1]["error"],
                "inbox_not_configured",
            )

    def test_hermes_librarian_ships_capabilities_only_like_openclaw_and_is_its_own_identity(self):
        self.start(access=self.access())
        tok = self.tok("hermes-librarian")
        names = {t["name"] for t in self.rpc(tok, "tools/list")[1]["result"]["tools"]}
        self.assertEqual(
            (
                len(names),
                {"buzz_capabilities", "buzz_read"} <= names,
                "buzz_publish" in names or "buzz_post_message" in names,
            ),
            (6, True, False),
        )
        status, caps = self.tool(tok, "buzz_capabilities")
        self.assertEqual((status, caps["ok"], caps["data"]["identity"]["communities"]), (200, True, {}))
        self.assertEqual(
            self.receipts[-2]["principal_fp"],
            __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint("hermes-librarian"),
        )
        self.assertEqual(
            self.tool(tok, "buzz_read", operation="list_channels", community="life-os")[1]["error"],
            "client_identity_not_configured",
        )
        self.assertEqual(
            self.rpc(self.tok("hermes-librarian", self.SECRETS["openclaw"]), "tools/list")[0], 401
        )  # another client's secret is a 401

    PILOT_BASE = {
        "BUZZ_URL_LIFE_OS": "https://life-os.communities.buzz.xyz",
        "BUZZ_KEY_CLAUDE_LIFE_OS": "01" * 32,
        "BUZZ_OAUTH_ISSUER": ISS,
        "BUZZ_OAUTH_JWKS_URL": ISS + "/jwks",
        "BUZZ_OAUTH_CLIENTS": "cid-one",
        "BUZZ_MCP_PUBLIC_URL": BASE,
        "BUZZ_OAUTH_USERS": "user-claude:claude:life-os",
    }

    def test_openclaw_has_its_own_pilot_only_relay_identity_reads_and_posts_on_the_pilot_and_nothing_hosted(self):
        """OpenClaw's own relay identity on community `pilot` only: read plus write to the pilot channels, no hosted
        life-os, no bytalos, and nobody else's key.
        """
        from lifeos.platform import pilot_community

        self.start(access=self.access())
        relay = buzz_mcp.from_env(
            {**self.PILOT_BASE, "BUZZ_RELAY": "pilot", "BUZZ_PILOT_CHANNEL": CHAN, "BUZZ_KEY_OPENCLAW_PILOT": "07" * 32}
        )[0]["openclaw"]
        self.assertEqual(
            (dict(relay.urls), dict(relay.keys), set(relay.writes)), ({}, {}, set())
        )  # before wiring: no community at all
        self.assertEqual(
            pilot_community.wire(
                {
                    **self.PILOT_BASE,
                    "BUZZ_RELAY": "pilot",
                    "BUZZ_PILOT_CHANNEL": CHAN,
                    "BUZZ_KEY_OPENCLAW_PILOT": "07" * 32,
                },
                {"openclaw": relay},
                nip98.secret_from,
            ),
            ("openclaw",),
        )
        relay.send = self.fake
        self.relays["openclaw"] = relay
        tok = self.tok("openclaw")
        names = {t["name"] for t in self.rpc(tok, "tools/list")[1]["result"]["tools"]}
        self.assertEqual((len(names), {"buzz_capabilities", "buzz_read", "buzz_publish"} <= names), (8, True))
        status, caps = self.tool(tok, "buzz_capabilities")
        self.assertEqual(
            (status, caps["ok"], sorted(caps["data"]["identity"]["communities"]), caps["data"]["writable_channels"]),
            (200, True, ["pilot"], {"pilot": [CHAN]}),
        )
        for comm in ("life-os", "bytalos"):
            self.assertEqual(
                self.tool(tok, "buzz_read", operation="list_channels", community=comm)[1]["error"],
                "community_not_authorized",
                comm,
            )
            out = self.tool(
                tok,
                "buzz_publish",
                operation="message",
                community=comm,
                arguments={"channel": CHAN, "text": "x", "idempotency_key": "oc-" + comm},
            )[1]
            self.assertFalse(out.get("ok"), comm)  # no hosted write, whatever the channel
        out = self.tool(
            tok,
            "buzz_publish",
            operation="message",
            community="pilot",
            arguments={"channel": CHAN, "text": "synthetic openclaw pilot post", "idempotency_key": "oc-pilot"},
        )[1]
        self.assertTrue(out.get("ok"), out)
        out = self.tool(
            tok,
            "buzz_publish",
            operation="message",
            community="pilot",
            arguments={"channel": OTHER, "text": "x", "idempotency_key": "oc-other"},
        )[1]
        self.assertFalse(out.get("ok"), out)  # a channel outside the pilot list
        self.assertEqual(
            self.rpc(self.tok("openclaw", self.SECRETS["mac-claude-code"]), "tools/list")[0], 401
        )  # another client's secret is a 401

    def memory(self, cid, op, **arguments):
        return self.tool(self.tok(cid), "buzz_read", operation=op, arguments=arguments)

    def with_memory(self):
        """the shipped grants and a fake Honcho behind the real handler."""
        from unittest import mock
        from tests.platform.test_memory_honcho import FakeHoncho

        self.honcho = FakeHoncho()
        self.refusals = []
        patch = mock.patch.dict(
            buzz_mcp.MEMORY,
            {"client": self.honcho, "look": lambda text: None, "receipt": self.refusals.append, "asks": {}},
        )  # no model second look in tests
        patch.start()
        self.addCleanup(patch.stop)
        self.start(access=self.access())

    def test_memory_store_recall_correct_revoke_ask_go_through_the_gate_and_the_text_goes_when_it_is_revoked(self):
        self.with_memory()
        status, stored = self.memory(
            "hermes-librarian",
            "memory_store",
            workspace="Personal",
            text="Synthetic fact: the demo bird is a kestrel.",
            source="uat",
        )
        self.assertEqual(
            (status, stored["ok"], stored["data"]["client"]), (200, True, "hermes-librarian")
        )  # the verified credential is the provenance
        fid = stored["data"]["id"]
        _, recalled = self.memory("openclaw", "memory_recall", workspace="Personal", query="demo bird")
        self.assertEqual([(i["id"], i["client"]) for i in recalled["data"]["items"]], [(fid, "hermes-librarian")])
        _, asked = self.memory("mac-claude-code", "memory_ask", workspace="Personal", question="What is the demo bird?")
        self.assertEqual(
            (
                asked["ok"],
                asked["data"]["label"],
                "kestrel" in asked["data"]["answer"],
                [c["id"] for c in asked["data"]["considered"]],
            ),
            (True, "memory-derived", True, [fid]),
        )
        _, corrected = self.memory(
            "hermes-librarian",
            "memory_correct",
            workspace="Personal",
            id=fid,
            text="Synthetic fact: the demo bird is a heron.",
            source="uat",
        )
        self.assertEqual(corrected["data"]["supersedes"], fid)
        _, seen = self.memory("openclaw", "memory_recall", workspace="Personal", query="demo bird")
        self.assertEqual([i["text"] for i in seen["data"]["items"]], ["Synthetic fact: the demo bird is a heron."])
        self.memory("hermes-librarian", "memory_revoke", workspace="Personal", id=corrected["data"]["id"])
        self.assertEqual(
            self.memory("openclaw", "memory_recall", workspace="Personal", query="demo bird")[1]["data"]["items"], []
        )
        after = self.memory(
            "oracle-claude-code", "memory_ask", workspace="Personal", question="What is the demo bird?"
        )[1]["data"]
        self.assertEqual(
            ("kestrel" in after["answer"], "heron" in after["answer"], after["considered"]), (False, False, [])
        )  # the text is gone, so the ask cannot repeat it
        self.assertNotIn("kestrel", json.dumps(self.honcho.rows))
        receipts = [r for r in self.receipts if r.get("tool", "").startswith("memory_")]
        self.assertEqual(
            {r["tool"] for r in receipts},
            {"memory_store", "memory_recall", "memory_correct", "memory_revoke", "memory_ask"},
        )

    def test_a_sensitive_store_is_refused_over_the_wire_with_the_fixed_shape_and_never_reaches_honcho(self):
        self.with_memory()
        status, out = self.memory(
            "openclaw", "memory_store", workspace="Personal", text="Her SSN is 123-45-6789.", source="uat"
        )
        self.assertEqual(
            (out["ok"], out["error"], out["category"], out["stored"]), (False, "sensitive_refused", "identity", False)
        )
        self.assertIn("Nothing was stored", out["hint"])
        self.assertNotIn("123-45-6789", json.dumps(out))  # never echoed
        self.assertEqual((self.honcho.rows, self.honcho.peers), ([], 0))
        self.assertEqual([sorted(r) for r in self.refusals], [["category", "client", "ct", "event"]])
        self.assertEqual(self.refusals[0]["client"], "openclaw")
        self.assertNotIn("123-45-6789", json.dumps(self.refusals) + json.dumps(self.receipts))

    def test_memory_is_refused_for_a_client_without_a_grant_and_for_bytalos_and_tgp(self):
        self.with_memory()
        for op, args in (("memory_store", {"text": "t", "source": "s"}), ("memory_ask", {"question": "q"})):
            self.assertEqual(
                self.memory("honcho-alerts", op, workspace="Personal", **args)[1]["error"], "access_denied", op
            )  # a credential with no memory grant
        for workspace in ("Bytalos", "TGP", "personal"):
            for op, args in (("memory_store", {"text": "t", "source": "s"}), ("memory_ask", {"question": "q"})):
                out = self.memory("openclaw", op, workspace=workspace, **args)[1]
                self.assertEqual(out["error"], "access_denied", (workspace, op))  # Bytalos has no grant
        self.assertEqual((self.honcho.rows, self.honcho.chats), ([], []))  # nothing reached Honcho

    def test_a_credential_cannot_name_the_client_and_an_expired_grant_refuses(self):
        self.with_memory()
        status, out = self.tool(
            self.tok("openclaw"),
            "buzz_read",
            operation="memory_store",
            arguments={"workspace": "Personal", "text": "t", "source": "s", "_client": "someone-else"},
        )
        self.assertEqual(out["error"], "invalid_argument")  # the schema has no client argument
        from datetime import datetime, timezone
        from lifeos.platform import mcp_access

        self.start(access=self.access())
        late = datetime(2100, 1, 1, 6, 0, tzinfo=timezone.utc)
        import unittest.mock as um

        with um.patch.object(mcp_access, "datetime", wraps=datetime) as clock:
            clock.now.return_value = late
            self.assertIn(
                self.memory("openclaw", "memory_store", workspace="Personal", text="t", source="s")[1]["error"],
                ("access_denied", "governance_unavailable", "invalid_token"),
            )

    def test_memory_not_configured_and_honcho_down_are_fixed_codes(self):
        from unittest import mock

        self.start(access=self.access())
        with mock.patch.dict(buzz_mcp.MEMORY, {"client": None}):
            self.assertEqual(
                self.memory("openclaw", "memory_recall", workspace="Personal", query="x")[1]["error"],
                "memory_not_configured",
            )
        self.with_memory()
        self.honcho.down = True
        self.assertEqual(
            self.memory("openclaw", "memory_recall", workspace="Personal", query="x")[1]["error"], "memory_unavailable"
        )

    def test_the_memory_operations_are_listed_for_a_granted_credential_through_buzz_read(self):
        self.with_memory()
        names = {t["name"] for t in self.rpc(self.tok("hermes-librarian"), "tools/list")[1]["result"]["tools"]}
        self.assertIn("buzz_read", names)

    def test_an_oauth_token_never_matches_a_credential_row_and_unreadable_data_fails_closed(self):
        from unittest import mock

        self.start(access=self.access())
        self.assertEqual(self.tool(token(sub="user-claude"), "buzz_capabilities")[1]["ok"], True)  # unchanged path
        self.assertEqual(
            self.receipts[-2]["principal_fp"],
            __import__("lifeos.platform.authorization_gate", fromlist=["x"]).fingerprint("claude-chat"),
        )
        self.start(access=False)
        with mock.patch("lifeos.platform.mcp_access.load", side_effect=OSError("data file missing")):
            status, out = self.rpc(self.tok(), "tools/list")
        self.assertEqual((status, out), (503, {"error": "governance_unavailable"}))


class RateLimit(Server):
    """a verified caller is limited per minute before any tool or gate runs"""

    def test_a_caller_over_the_limit_gets_429_and_a_sanitized_log_line_and_others_are_unaffected(self):
        from unittest import mock

        self.start()
        with mock.patch.object(call_limit, "LIMIT", 3):
            tok = token(sub="user-claude")
            for _ in range(3):
                self.assertEqual(self.rpc(tok, "tools/list")[0], 200)
            status, raw, headers = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tok)
            self.assertEqual((status, json.loads(raw), headers["Retry-After"]), (429, {"error": "rate_limited"}, "60"))
            line = self.audit[-1]
            self.assertEqual(
                (
                    line["outcome"],
                    line["sub"],
                    line["tool"],
                    set(line) - {"client", "identity", "sub", "tool", "community", "outcome", "reason"},
                ),
                ("rate_limited", "user-claude", None, set()),
            )
            self.assertNotIn(tok, json.dumps(self.audit))
            self.assertEqual(
                self.rpc(token(sub="user-chatgpt"), "tools/list")[0], 200
            )  # another verified user has their own count
            self.assertEqual(self.rpc("not-a-token", "tools/list")[0], 401)

    def test_the_window_slides_and_each_credential_has_its_own_count(self):
        self.assertEqual([call_limit.allow("k", now=t) for t in (0.0, 1.0, 2.0)], [True] * 3)
        with __import__("unittest.mock", fromlist=["x"]).patch.object(call_limit, "LIMIT", 3):
            call_limit.reset()
            self.assertEqual([call_limit.allow("a", now=t) for t in (0.0, 1.0, 2.0, 3.0)], [True, True, True, False])
            self.assertTrue(call_limit.allow("a", now=60.5))  # the first call left the window
            self.assertTrue(call_limit.allow("b", now=3.0))  # another key is untouched


class FourIdentities(Server):
    def test_each_client_and_community_pairing_signs_with_its_own_key_and_reads_back_independently(self):
        self.start(writes=BOTH)
        pairs = [(c, k) for c in ("claude", "chatgpt") for k in ("life-os", "bytalos")]
        receipts = {}
        for client, comm in pairs:
            tok = token(sub="user-" + client, **WR)
            status, receipts[client, comm] = self.tool(
                tok,
                "buzz_post_message",
                community=comm,
                channel=CHANS[comm],
                text=f"synthetic from {client} to {comm}",
                idempotency_key="id-" + client + comm[:2],
            )
            self.assertEqual((status, receipts[client, comm]["status"]), (200, "sent_not_read_back"))
        pubs = {pair: nip98.pubkey(KEYS[pair]) for pair in pairs}
        self.assertEqual(len(set(pubs.values())), 4)
        by_id = {e["id"]: e for e in self.fake.events}
        for pair in pairs:
            ev = by_id[receipts[pair]["event_id"]]
            self.assertEqual(
                (ev["pubkey"], dict(ev["tags"])["h"], dict(ev["tags"])["actor"]),
                (pubs[pair], CHANS[pair[1]], "key:" + pubs[pair][:16]),
            )
            other = (
                {"claude": "chatgpt", "chatgpt": "claude"}[pair[0]],
                pair[1],
            )  # the OTHER product's identity reads it back
            got = self.tool(
                token(sub="user-" + other[0]), "buzz_get_messages", community=pair[1], channel=CHANS[pair[1]]
            )[1]
            self.assertIn(
                (receipts[pair]["event_id"], f"synthetic from {pair[0]} to {pair[1]}", "key:" + pubs[pair][:16]),
                [(m["event_id"], m["untrusted_text"], m["actor"]) for m in got],
            )
        self.assertEqual(
            {a["identity"] for a in self.audit if a["tool"] == "buzz_post_message"}, {p[:16] for p in pubs.values()}
        )
        self.assertNotIn(KEY_A.hex(), json.dumps(self.audit))

    def test_a_missing_credential_denies_only_its_pairing_and_never_falls_back(self):
        keys = {k: v for k, v in KEYS.items() if k != ("chatgpt", "bytalos")}
        self.start(writes=BOTH, keys=keys)
        tg = token(sub="user-chatgpt", **WR)
        for call in (
            ("buzz_list_channels", dict(community="bytalos")),
            ("buzz_post_message", dict(POST, community="bytalos", channel=OTHER)),
        ):
            self.assertEqual(self.tool(tg, *call[:1], **call[1]), (200, {"error": "key_not_configured"}))
        self.assertEqual((self.fake.calls, self.fake.events), ([], []))  # claude's bytalos key was never borrowed
        self.assertEqual(self.tool(token(sub="user-claude", **WR), "buzz_list_channels", community="bytalos")[0], 200)
        self.assertEqual(self.tool(tg, "buzz_list_channels", community="life-os")[0], 200)
        self.assertEqual(
            self.tool(tg, "buzz_post_message", **POST)[1]["actor"],
            "key:" + nip98.pubkey(KEYS["chatgpt", "life-os"])[:16],
        )

    def test_a_client_with_no_identity_at_all_is_denied_before_any_relay_request(self):
        self.start(writes=BOTH, keys={k: v for k, v in KEYS.items() if k[0] == "claude"})
        for call in (("buzz_list_channels", dict(community="life-os")), ("buzz_post_message", POST)):
            self.assertEqual(
                self.tool(token(sub="user-chatgpt", **WR), call[0], **call[1]),
                (200, {"error": "client_identity_not_configured"}),
            )
        self.assertEqual((self.fake.calls, self.fake.events), ([], []))
        self.assertEqual(self.tool(token(sub="user-claude"), "buzz_list_channels", community="life-os")[0], 200)

    def test_the_caller_cannot_choose_the_identity(self):
        self.start(writes=BOTH)
        tok = token(sub="user-chatgpt", **WR)
        for forged in (
            dict(client="claude"),
            dict(actor="key:" + nip98.pubkey(KEYS["claude", "life-os"])[:16]),
            dict(identity="claude"),
        ):
            self.assertEqual(
                self.tool(tok, "buzz_post_message", **dict(POST, **forged)), (200, {"error": "invalid_argument"})
            )
        self.req(
            "POST",
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "buzz_post_message", "arguments": POST},
            },
            tok,
            X_Client="claude",
            X_Actor="claude",
            User_Agent="Claude-User",
        )
        self.assertEqual([e["pubkey"] for e in self.fake.events], [nip98.pubkey(KEYS["chatgpt", "life-os"])])


class UserAuthorization(Server):
    def test_a_different_user_on_an_authorized_client_gets_nothing(self):
        self.start()
        for name, tok in (
            ("other_user", token(sub="user-2")),
            ("absent_sub", token(sub=None)),
            ("numeric_sub", token(sub=1)),
            ("list_sub", token(sub=["user-1"])),
            ("write_scope_other_user", token(sub="user-2", scope="buzz:read buzz:write")),
        ):
            for call in (("buzz_list_channels", dict(community="life-os")), ("buzz_post_message", POST)):
                status, raw, _ = self.req(
                    "POST",
                    "/mcp",
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": call[0], "arguments": call[1]},
                    },
                    tok,
                )
                self.assertEqual((status, json.loads(raw)), (403, {"error": "user_not_authorized"}), name)
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, token(sub="user-2"))[0], 403)
        self.assertEqual((self.fake.calls, self.fake.events), ([], []))
        self.assertTrue(
            self.audit
            and all(
                a["outcome"] == "user_not_authorized" and a["tool"] is None and a["sub"] in ("user-2", None, "1")
                for a in self.audit
            )
        )

    def test_community_boundary_is_per_user_and_checked_before_any_relay_request(self):
        self.start()
        tok = token(sub="user-lifeos-only", scope="buzz:read buzz:write")
        self.assertEqual(
            self.tool(tok, "buzz_list_channels", community="bytalos"), (200, {"error": "community_not_authorized"})
        )
        self.assertEqual(
            self.tool(tok, "buzz_post_message", **dict(POST, community="bytalos"))[1],
            {"error": "community_not_authorized"},
        )
        self.assertEqual(
            self.tool(tok, "buzz_search_messages", query="x")[1], {"error": "community_not_authorized"}
        )  # no community: denied, not defaulted
        self.assertEqual((self.fake.calls, self.fake.events), ([], []))
        self.assertEqual(self.tool(tok, "buzz_list_channels", community="life-os")[0], 200)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(
            self.tool(token(scope="buzz:read"), "buzz_list_channels", community="bytalos")[0], 200
        )  # a user granted both communities still reaches both
        self.assertEqual(self.audit[0]["outcome"], "community_not_authorized")


class Startup(unittest.TestCase):
    """Runs the unit's real ExecStart, parsed from the unit file, from the staged /opt layout (only lifeos/__init__.py,
    lifeos/buzz/*.py and the old-name.
    """

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
        self.assertEqual(unit.count("LoadCredentialEncrypted="), 4)  # exactly the four identity slots
        for line in (
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "User=buzz-mcp",
            "LoadCredentialEncrypted=buzz_key_claude_life_os",
            "LoadCredentialEncrypted=buzz_key_chatgpt_bytalos",
            "Environment=PYTHONPATH=/opt/lifeos-buzz-mcp/current",
        ):
            self.assertIn(line, unit)

    def test_execstart_imports_from_the_staged_layout_without_secrets(self):
        stage = self.stage()
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        done = subprocess.run(
            self.exec_argv(), cwd=other, env=self.env(stage), capture_output=True, text=True, timeout=30
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("config error", done.stderr)
        self.assertNotIn("ModuleNotFoundError", done.stderr)
        isolated = subprocess.run(
            [sys.executable, "-I", *self.exec_argv()[3:]],
            cwd=other,
            env=self.env(stage),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertIn("ModuleNotFoundError", isolated.stderr)

    def test_staged_service_serves_loopback_discovery(self):
        stage = self.stage()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = self.env(
            stage,
            BUZZ_MCP_PORT=str(port),
            BUZZ_MCP_PUBLIC_URL=BASE,
            BUZZ_OAUTH_ISSUER=ISS,
            BUZZ_OAUTH_JWKS_URL=ISS + "/jwks",
            BUZZ_OAUTH_CLIENTS="cid-one",
            BUZZ_OAUTH_USERS="user-claude:claude:life-os",
            BUZZ_URL_LIFE_OS=URLS["life-os"],
            BUZZ_KEY_CLAUDE_LIFE_OS=KEY_A.hex(),
        )
        proc = subprocess.Popen(
            self.exec_argv(),
            cwd=tempfile.gettempdir(),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
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
            self.assertEqual(
                self.req(
                    "POST",
                    "/mcp",
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "buzz_post_message", "arguments": POST},
                    },
                    token(scope=bad),
                )[0],
                403,
            )
        self.assertEqual(len(self.fake.events), 1)  # only the properly scoped write went through

    def test_issuer_trailing_slash_is_equivalent_but_nothing_else(self):
        v = Verifier(
            "https://identity.example.test",
            BASE + "/mcp",
            CLIENTS,
            ISS + "/jwks",
            USERS,
            fetch=lambda u: JWKS,
            clock=lambda: NOW,
        )
        self.assertEqual(
            v.verify("Bearer " + token(iss="https://identity.example.test/"))["client"], "claude"
        )  # Oracle IAM issues iss with a trailing slash
        self.assertEqual(v.verify("Bearer " + token(iss="https://identity.example.test"))["client"], "claude")
        for bad in (
            "https://identity.example.test.evil",
            "https://identity.example.test//x",
            None,
            ["https://identity.example.test"],
            5,
        ):
            with self.assertRaises(AuthError):
                v.verify("Bearer " + token(iss=bad))

    def test_authorization_server_address_is_separate_from_the_issuer(self):
        env = {
            **Config.ENV,
            "BUZZ_OAUTH_ISSUER": "https://identity.example.test/",
            "BUZZ_OAUTH_AS_URL": "https://idcs-x.example.test/",
            "BUZZ_OAUTH_CLIENTS": "cid-oracle",
        }
        auth = buzz_mcp.from_env(env)[1]
        self.assertEqual(
            (auth.issuer, auth.as_url, auth.clients),
            ("https://identity.example.test", "https://idcs-x.example.test", frozenset({"cid-oracle"})),
        )
        self.assertEqual(buzz_mcp.from_env(Config.ENV)[1].as_url, ISS)
        with self.assertRaises(ValueError):
            buzz_mcp.from_env({**env, "BUZZ_OAUTH_AS_URL": "http://insecure.example.test"})


class Transport(Server):
    def test_streamable_http_shapes(self):
        self.start(origins=("https://claude.ai",))
        tok = token()
        self.assertEqual(self.req("POST", "/mcp", {"method": "notifications/initialized"}, tok)[0], 202)
        self.assertEqual(
            self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 5, "result": {}}, tok)[0], 202
        )  # a client's reply to a server request
        self.assertEqual(
            self.req("POST", "/mcp", {"id": 1, "method": "ping"}, tok, MCP_Protocol_Version="1999-01-01")[0], 400
        )
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, tok, Accept="text/html")[0], 400)
        self.assertEqual(
            self.req("POST", "/mcp", {"id": 1, "method": "ping"}, tok, Origin="https://evil.example")[0], 403
        )
        self.assertEqual(self.req("POST", "/mcp", {"id": 1, "method": "ping"}, tok, Origin="https://claude.ai")[0], 200)
        self.assertEqual(self.req("POST", "/mcp", b"not json", tok)[0], 400)
        self.assertEqual(self.req("POST", "/mcp", b"[1]", tok)[0], 400)
        self.assertEqual(self.req("POST", "/mcp", b"x" * (buzz_mcp.MAX_BODY + 1), tok)[0], 400)
        self.assertEqual(self.req("POST", "/other", {"id": 1}, tok)[0], 404)
        status, _, hdr = self.req("GET", "/mcp", tok=tok)
        self.assertEqual((status, hdr["Allow"]), (405, "POST"))
        self.assertEqual(
            self.rpc(tok, "initialize", {"protocolVersion": "2099-01-01"})[1]["result"]["protocolVersion"],
            buzz_mcp.PROTOCOL,
        )

    def test_no_secret_is_logged_or_returned(self):
        self.start()
        tok = token(sub="user-chatgpt", scope="buzz:read buzz:write")
        self.fake.fail_query = True
        results = [
            self.tool(tok, "buzz_list_channels", community="life-os"),
            self.tool(tok, "buzz_post_message", **POST),
        ]
        blob = json.dumps([self.audit, results])
        for secret in (tok, tok.split(".")[2], *(k.hex() for k in KEYS.values()), POST["text"]):
            self.assertNotIn(secret, blob)


class Config(unittest.TestCase):
    FOUR = {
        f"BUZZ_KEY_{i}_{c}": KEYS[i.lower(), c.lower().replace("_", "-")].hex()
        for i in ("CLAUDE", "CHATGPT")
        for c in ("LIFE_OS", "BYTALOS")
    }
    ENV = {
        "BUZZ_MCP_PUBLIC_URL": BASE + "/",
        "BUZZ_OAUTH_ISSUER": ISS,
        "BUZZ_OAUTH_JWKS_URL": ISS + "/jwks",
        "BUZZ_OAUTH_CLIENTS": "cid-one",
        "BUZZ_OAUTH_USERS": "user-claude:claude:life-os+bytalos,user-chatgpt:chatgpt:life-os+bytalos,svc+x@example.com:chatgpt:life-os",
        "BUZZ_URL_LIFE_OS": URLS["life-os"] + "/",
        "BUZZ_URL_BYTALOS": URLS["bytalos"],
        **FOUR,
    }

    def test_from_env_builds_four_identities_defaults_to_no_writes_and_rejects_unsafe_config(self):
        relays, auth, base, origins = buzz_mcp.from_env(self.ENV)
        self.assertEqual(
            (
                {i: {c: nip98.pubkey(k) for c, k in r.keys.items()} for i, r in relays.items()},
                {r.writes.__len__() for r in relays.values()},
            ),
            (
                {
                    **{i: {c: nip98.pubkey(KEYS[i, c]) for c in ("life-os", "bytalos")} for i in ("claude", "chatgpt")},
                    "openclaw": {},
                },
                {0},
            ),
        )
        self.assertEqual(
            (base, origins, auth.audience, auth.clients), (BASE, (), BASE + "/mcp", frozenset({"cid-one"}))
        )
        self.assertEqual(auth.users["svc+x@example.com"], ("chatgpt", frozenset({"life-os"})))
        url_ids = buzz_mcp.from_env(
            {**self.ENV, "BUZZ_OAUTH_CLIENTS": "https://chatgpt.com/oauth/client.json, dcr-abc123"}
        )[1]
        self.assertEqual(
            url_ids.clients, frozenset({"https://chatgpt.com/oauth/client.json", "dcr-abc123"})
        )  # a client id may be a URL
        no_chatgpt = {k: v for k, v in self.ENV.items() if "CHATGPT_BYTALOS" not in k}
        self.assertEqual(
            {i: set(r.keys) for i, r in buzz_mcp.from_env(no_chatgpt)[0].items()},
            {"claude": {"life-os", "bytalos"}, "chatgpt": {"life-os"}, "openclaw": set()},
        )
        bad = [
            {**self.ENV, "BUZZ_MCP_PUBLIC_URL": "http://edge.example.test"},
            {**self.ENV, "BUZZ_MCP_PUBLIC_URL": BASE + "/mcp"},
            {**self.ENV, "BUZZ_OAUTH_ISSUER": ""},
            {**self.ENV, "BUZZ_OAUTH_JWKS_URL": "http://x"},
            {**self.ENV, "BUZZ_OAUTH_USERS": "user-claude:admin:life-os"},
            {**self.ENV, "BUZZ_OAUTH_USERS": "user-claude:shared:life-os"},
            {**self.ENV, "BUZZ_OAUTH_USERS": ""},
            {**self.ENV, "BUZZ_OAUTH_USERS": "user-claude:claude:other"},
            {**self.ENV, "BUZZ_OAUTH_USERS": "user-claude"},
            {**self.ENV, "BUZZ_OAUTH_USERS": "user-claude:claude"},
            {**self.ENV, "BUZZ_OAUTH_CLIENTS": ""},
            {**self.ENV, "BUZZ_KEY_CLAUDE_LIFE_OS": "nothex"},
            {**self.ENV, "BUZZ_AUTH_TAG": "x"},
            {**self.ENV, "BUZZ_KEY_LIFE_OS": KEY_A.hex()},
            {
                **self.ENV,
                "BUZZ_KEY_CHATGPT_BYTALOS": self.FOUR["BUZZ_KEY_CLAUDE_BYTALOS"],
            },  # one key may not fill two identity slots
            {k: v for k, v in self.ENV.items() if not k.startswith("BUZZ_KEY_")},
        ]
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
