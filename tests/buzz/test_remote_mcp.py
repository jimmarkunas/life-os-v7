"""LIFE-216 / BUZZ-16: the shared remote MCP (OAuth 2.1 resource server + Streamable HTTP) for Claude Chat and ChatGPT Chat, proved over real loopback HTTP against a MOCKED relay and a LOCAL test identity provider.
These are fixture results: they say nothing about the real claude.ai / chatgpt.com connectors or live Buzz."""
import base64, hashlib, json, random, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer

from lifeos import buzz_mcp, buzz_nip98 as nip98
from lifeos.buzz_auth import AuthError, Verifier
from lifeos.buzz_relay import Relay
from tests.buzz.test_bridge import CHAN, KEY_A, KEY_B, OTHER, URLS, FakeRelay

BASE, ISS = "https://edge.example.test", "https://idp.example.test"
CLIENTS = {"cid-claude": "claude", "cid-chatgpt": "chatgpt"}
NOW = 1_800_000_000
POST = dict(community="life-os", channel=CHAN, text="synthetic hello", idempotency_key="k1")


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
    claims = {"iss": ISS, "aud": BASE + "/mcp", "exp": NOW + 600, "client_id": "cid-claude", "scope": "buzz:read", "sub": "user-1", **over}
    h, p = b64(json.dumps({"alg": alg, "kid": kid}).encode()), b64(json.dumps(claims).encode())
    info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(f"{h}.{p}".encode()).digest()
    em = b"\0\1" + b"\xff" * (256 - len(info) - 3) + b"\0" + info
    sig = pow(int.from_bytes(em, "big"), D, N).to_bytes(256, "big") if sign else bytes(256)
    return f"{h}.{p}.{b64(sig)}"


class Server(unittest.TestCase):
    def start(self, fake=None, writes=((("life-os", CHAN)),), fetch=None, origins=()):
        self.fake = fake or FakeRelay()
        self.relay = Relay(URLS, {"life-os": KEY_A, "bytalos": KEY_B}, writes, self.fake)
        self.audit = []
        verifier = Verifier(ISS, BASE + "/mcp", CLIENTS, ISS + "/jwks", fetch or (lambda url: JWKS), clock=lambda: NOW)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), buzz_mcp.handler(self.relay, verifier, BASE, origins, self.audit.append))
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
            self.assertEqual((status, json.loads(raw)), (200, {"resource": BASE + "/mcp", "authorization_servers": [ISS], "scopes_supported": ["buzz:read", "buzz:write"], "bearer_methods_supported": ["header"]}))
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
        v = Verifier(ISS, BASE + "/mcp", CLIENTS, ISS + "/jwks", lambda u: (fetches.append(u), keys)[1], clock=lambda: NOW)
        for _ in range(5):
            with self.assertRaises(AuthError):
                v.verify("Bearer " + token())
        self.assertEqual(len(fetches), 1)
        keys.update(JWKS)
        v.clock = lambda: NOW + 31
        self.assertEqual(v.verify("Bearer " + token(exp=NOW + 9999))["client"], "claude")


class DualClient(Server):
    def test_both_chat_clients_use_one_endpoint_and_see_the_same_tools_and_room(self):
        self.start()
        self.fake.events = [{"id": "c" * 64, "pubkey": "d" * 64, "created_at": 2, "kind": 9, "tags": [["h", CHAN]], "content": "synthetic room message"}]
        seen = {}
        for client in ("claude", "chatgpt"):
            tok = token(client_id="cid-" + client)
            status, init = self.rpc(tok, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": client, "version": "0"}})
            self.assertEqual((status, init["result"]["protocolVersion"]), (200, "2025-03-26"))
            seen[client] = (self.rpc(tok, "tools/list")[1]["result"], self.tool(tok, "buzz_get_messages", community="life-os", channel=CHAN))
        self.assertEqual(seen["claude"], seen["chatgpt"])
        self.assertEqual([t["name"] for t in seen["claude"][0]["tools"]], ["buzz_list_channels", "buzz_get_messages", "buzz_search_messages", "buzz_post_message"])
        self.assertEqual(seen["claude"][1][1][0]["untrusted_text"], "synthetic room message")
        self.assertEqual({a["client"] for a in self.audit}, {"claude", "chatgpt"})

    def test_audit_identity_comes_from_the_signed_token_not_from_the_caller(self):
        self.start()
        tok = token(client_id="cid-chatgpt")
        self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "buzz_list_channels", "arguments": {"community": "life-os"}, "client": "claude"}}, tok, X_Client="claude", X_Actor="claude")
        self.assertEqual(self.tool(tok, "buzz_get_messages", community="life-os", channel=CHAN, actor="claude")[1], {"error": "invalid_argument"})
        self.assertEqual([a["client"] for a in self.audit], ["chatgpt", "chatgpt"])
        self.assertEqual(self.audit[1]["outcome"], "invalid_argument")

    def test_scopes_split_read_from_write(self):
        self.start()
        read = token(client_id="cid-chatgpt")
        status, raw, hdr = self.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "buzz_post_message", "arguments": POST}}, read)
        self.assertEqual(status, 403)
        self.assertIn('error="insufficient_scope"', hdr["WWW-Authenticate"])
        self.assertIn('scope="buzz:write"', hdr["WWW-Authenticate"])
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

    def test_cross_client_duplicate_refusal_and_restart_safe_conflict(self):
        self.start()
        wr = dict(scope="buzz:read buzz:write")
        first = self.tool(token(client_id="cid-claude", **wr), "buzz_post_message", **POST)[1]
        again = self.tool(token(client_id="cid-chatgpt", **wr), "buzz_post_message", **POST)[1]
        self.assertEqual((first["status"], again["status"], again["event_id"], len(self.fake.events)), ("sent_not_read_back", "already_delivered", first["event_id"], 1))
        self.assertEqual(self.tool(token(client_id="cid-chatgpt", **wr), "buzz_post_message", **dict(POST, text="different"))[1], {"error": "idempotency_conflict"})
        self.start(fake=self.fake)                                                  # restart: new process, empty in-memory cache, same relay
        self.assertEqual(self.tool(token(**wr), "buzz_post_message", **dict(POST, text="different"))[1], {"error": "idempotency_conflict"})
        self.assertEqual(self.tool(token(**wr), "buzz_post_message", **POST)[1]["status"], "already_delivered")
        self.assertEqual(len(self.fake.events), 1)

    def test_concurrent_posts_from_both_clients_create_one_event(self):
        self.start()
        gate, out = threading.Barrier(8), []
        def go(i):
            gate.wait()
            out.append(self.tool(token(client_id=("cid-claude", "cid-chatgpt")[i % 2], scope="buzz:write"), "buzz_post_message", **POST)[1]["status"])
        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((sorted(out), len(self.fake.events)), (["already_delivered"] * 7 + ["sent_not_read_back"], 1))


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
        tok = token(client_id="cid-chatgpt", scope="buzz:read buzz:write")
        self.fake.fail_query = True
        results = [self.tool(tok, "buzz_list_channels", community="life-os"), self.tool(tok, "buzz_post_message", **POST)]
        blob = json.dumps([self.audit, results])
        for secret in (tok, tok.split(".")[2], KEY_A.hex(), KEY_B.hex(), POST["text"]):
            self.assertNotIn(secret, blob)


class Config(unittest.TestCase):
    ENV = {"BUZZ_MCP_PUBLIC_URL": BASE + "/", "BUZZ_OAUTH_ISSUER": ISS, "BUZZ_OAUTH_JWKS_URL": ISS + "/jwks", "BUZZ_OAUTH_CLIENTS": "cid-claude:claude,cid-chatgpt:chatgpt",
           "BUZZ_URL_LIFE_OS": URLS["life-os"] + "/", "BUZZ_KEY_LIFE_OS": KEY_A.hex()}

    def test_from_env_defaults_to_no_writes_and_rejects_unsafe_config(self):
        relay, auth, base, origins = buzz_mcp.from_env(self.ENV)
        self.assertEqual((relay.writes, base, origins, auth.audience, auth.clients), (set(), BASE, (), BASE + "/mcp", {"cid-claude": "claude", "cid-chatgpt": "chatgpt"}))
        url_ids = buzz_mcp.from_env({**self.ENV, "BUZZ_OAUTH_CLIENTS": "https://chatgpt.com/oauth/client.json:chatgpt, dcr-abc123:claude"})[1]
        self.assertEqual(url_ids.clients, {"https://chatgpt.com/oauth/client.json": "chatgpt", "dcr-abc123": "claude"})   # ChatGPT's CIMD client_id is a URL
        bad = [{**self.ENV, "BUZZ_MCP_PUBLIC_URL": "http://edge.example.test"}, {**self.ENV, "BUZZ_MCP_PUBLIC_URL": BASE + "/mcp"}, {**self.ENV, "BUZZ_OAUTH_ISSUER": ""}, {**self.ENV, "BUZZ_OAUTH_JWKS_URL": "http://x"},
               {**self.ENV, "BUZZ_OAUTH_CLIENTS": "x:admin"}, {**self.ENV, "BUZZ_OAUTH_CLIENTS": ""}, {**self.ENV, "BUZZ_KEY_LIFE_OS": "nothex"}, {**self.ENV, "BUZZ_AUTH_TAG": "x"}]
        for env in bad:
            with self.assertRaises(ValueError) as c:
                buzz_mcp.from_env(env)
            self.assertNotIn("nothex", str(c.exception))

    def test_systemd_credentials_overlay_the_environment(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "buzz_key_life_os").write_text(KEY_A.hex() + "\n")
            env = buzz_mcp.credentials({**self.ENV, "BUZZ_KEY_LIFE_OS": "", "CREDENTIALS_DIRECTORY": d})
        self.assertEqual(nip98.secret_from(env["BUZZ_KEY_LIFE_OS"]), KEY_A)
        self.assertEqual(buzz_mcp.credentials({"A": "1"}), {"A": "1"})


if __name__ == "__main__":
    unittest.main()
