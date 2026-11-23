"""LIFE-870: deploy/buzz-mcp/selfhost-probe against an in-process fake relay that follows the verified Buzz semantics (NIP-43 membership kinds 9030, NIP-29 kinds 9007 and 9000, POST /query and POST /events under NIP-98),
with a fake bws. The probe's keys are synthetic; the test proves the steps, the refusals and that no key reaches any output."""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lifeos.buzz import nip98

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/buzz-mcp/selfhost-probe"
OWNER_HEX, AGENT_HEX = "11" * 32, "22" * 32
OWNER_PUB, AGENT_PUB = nip98.pubkey(bytes.fromhex(OWNER_HEX)), nip98.pubkey(bytes.fromhex(AGENT_HEX))
FAKE_BWS = '#!/bin/sh\ncat "$FAKE_SECRETS"\n'


class FakeRelay(ThreadingHTTPServer):
    def __init__(self, break_membership=False):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.members, self.channels, self.events, self.break_membership = {OWNER_PUB}, {}, [], break_membership
        threading.Thread(target=self.serve_forever, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, body=None):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply(200 if self.path == "/_readiness" else 404)

        def do_POST(self):
            relay = self.server
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
            header = self.headers.get("Authorization", "")
            if not header.startswith("Nostr "):
                return self.reply(401)
            author = json.loads(base64.b64decode(header[6:]))["pubkey"]
            if author not in relay.members:                                          # relay-level membership gates every authenticated call (NIP-43)
                return self.reply(403)
            if self.path == "/query":
                out = []
                for flt in body:
                    for e in relay.events:
                        if flt.get("ids") and e["id"] not in flt["ids"]:
                            continue
                        if flt.get("kinds") and e["kind"] not in flt["kinds"]:
                            continue
                        tags = {t[0]: t[1] for t in e["tags"] if len(t) > 1}
                        if "#h" in flt and tags.get("h") not in flt["#h"]:
                            continue
                        if "#k" in flt and tags.get("k") not in flt["#k"]:
                            continue
                        out.append(e)
                return self.reply(200, out[: max(1, min(flt.get("limit", 10), 50))])
            if self.path == "/events":
                tags = {t[0]: t[1] for t in body["tags"] if len(t) > 1}
                kind = body["kind"]
                if kind == 9030 and author == OWNER_PUB and not relay.break_membership:
                    relay.members.add(tags["p"])
                elif kind == 9007 and author == OWNER_PUB:
                    relay.channels[tags["h"]] = {OWNER_PUB}
                elif kind == 9000 and author == OWNER_PUB and tags["h"] in relay.channels:
                    relay.channels[tags["h"]].add(tags["p"])
                elif kind == 9 and author in relay.channels.get(tags.get("h"), set()):
                    pass
                else:
                    return self.reply(200, {"accepted": False, "event_id": body["id"]})
                relay.events.append(body)
                return self.reply(200, {"accepted": True, "event_id": body["id"]})
            self.reply(404)


class SelfhostProbe(unittest.TestCase):
    def run_probe(self, relay, secrets=None, mode=(), stdin=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        (tmp / "bin").mkdir()
        (tmp / "bin/bws").write_text(FAKE_BWS)
        (tmp / "bin/bws").chmod(0o755)
        (tmp / "env").write_text("export BWS_ACCESS_TOKEN='synthetic'\n")
        (tmp / "secrets.json").write_text(json.dumps([{"key": "lifeos/edge/BUZZ_SELFHOST_OWNER_KEY", "value": OWNER_HEX}, {"key": "lifeos/edge/BUZZ_SELFHOST_AGENT_CLAUDE", "value": AGENT_HEX}] if secrets is None else secrets))
        env = {**os.environ, "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "FAKE_SECRETS": str(tmp / "secrets.json"), "STORE_CREDENTIALS_ENV": str(tmp / "env"), "STORE_CREDENTIALS_USER": "nobody-not-used"}
        done = subprocess.run([sys.executable, str(SCRIPT), *mode, f"http://127.0.0.1:{relay.server_address[1]}"], capture_output=True, text=True, env=env, cwd=tmp, input=stdin)
        self.addCleanup(relay.server_close)
        self.addCleanup(relay.shutdown)
        return done, tmp

    def test_every_step_passes_against_a_relay_with_the_verified_semantics_and_prints_only_public_things(self):
        relay = FakeRelay()
        done, tmp = self.run_probe(relay)
        steps = [line.split(" ")[:2] for line in done.stdout.splitlines()]
        self.assertEqual(steps, [["keys", "ok"], ["health", "ok"], ["unauth_refused", "ok"], ["non_member_refused", "ok"], ["member_add", "ok"], ["member_query_ok", "ok"], ["channel_create", "ok"], ["post_ok", "ok"],
                                 ["read_back", "ok"], ["repeat_idempotent", "ok"]], done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0)
        self.assertIn(AGENT_PUB, relay.members)
        posted = [e for e in relay.events if e["kind"] == 9]
        self.assertEqual((len(posted), posted[0]["pubkey"], posted[0]["content"].startswith("SYNTHETIC")), (1, AGENT_PUB, True))        # one post, by the agent, synthetic; the repeat made no second event
        for secret in (OWNER_HEX, AGENT_HEX):
            self.assertNotIn(secret, done.stdout + done.stderr)
            self.assertNotIn(secret, "".join(p.read_text(errors="replace") for p in tmp.rglob("*") if p.is_file() and p.name != "secrets.json"))

    def test_channel_mode_adds_the_member_creates_one_channel_and_prints_only_its_public_id(self):
        relay = FakeRelay()
        done, tmp = self.run_probe(relay, mode=("channel",))
        lines = done.stdout.splitlines()
        self.assertEqual([line.split(" ")[:2] for line in lines[:4]], [["keys", "ok"], ["member_add", "ok"], ["member_query_ok", "ok"], ["channel_create", "ok"]], done.stdout + done.stderr)
        self.assertRegex(lines[-1], r"^pilot_channel [0-9a-f-]{36}$")
        channel = lines[-1].split(" ")[1]
        self.assertEqual((done.returncode, AGENT_PUB in relay.members, relay.channels[channel] == {OWNER_PUB, AGENT_PUB}, [e for e in relay.events if e["kind"] == 9]), (0, True, True, []))   # one channel, the agent in it, no post, no health or refusal steps
        for secret in (OWNER_HEX, AGENT_HEX):
            self.assertNotIn(secret, done.stdout + done.stderr)

    def test_members_mode_adds_exactly_the_given_public_keys_to_the_relay_and_the_channel_and_nothing_else(self):
        """LIFE-927: the owner adds public keys only; no member private key is involved and nobody else is added."""
        relay = FakeRelay()
        channel = "11111111-2222-4333-8444-555555555555"
        relay.channels[channel] = {OWNER_PUB}
        other = nip98.pubkey(bytes.fromhex("33" * 32))
        done, tmp = self.run_probe(relay, mode=("members", channel), stdin=f"{AGENT_PUB}\n{other}\nnot-a-key\n")
        lines = done.stdout.splitlines()
        self.assertEqual([line.split(" ")[:2] for line in lines], [["keys", "ok"], ["member_" + AGENT_PUB[:8], "ok"], ["member_" + other[:8], "ok"]], done.stdout + done.stderr)
        self.assertEqual((done.returncode, relay.members, relay.channels[channel]), (0, {OWNER_PUB, AGENT_PUB, other}, {OWNER_PUB, AGENT_PUB, other}))
        for secret in (OWNER_HEX, AGENT_HEX):
            self.assertNotIn(secret, done.stdout + done.stderr)
        empty, _ = self.run_probe(FakeRelay(), mode=("members", channel), stdin="")
        self.assertEqual((empty.stdout.splitlines()[-1], empty.returncode), ("members fail (a channel id and at least one public key are required)", 1))

    def test_role_mode_signs_one_member_event_per_public_key_with_the_role_and_refuses_anything_else(self):
        """LIFE-927: the owner key signs the role change; the role is admin or member only (never owner) and nothing but the given public keys is touched."""
        relay = FakeRelay()
        done, _ = self.run_probe(relay, mode=("role", "admin"), stdin=f"{AGENT_PUB}\nnot-a-key\n")
        self.assertEqual([line.split(" ")[:2] for line in done.stdout.splitlines()], [["keys", "ok"], ["role_" + AGENT_PUB[:8], "ok"]], done.stdout + done.stderr)
        self.assertEqual((done.returncode, relay.members), (0, {OWNER_PUB, AGENT_PUB}))
        for secret in (OWNER_HEX, AGENT_HEX):
            self.assertNotIn(secret, done.stdout + done.stderr)
        empty, _ = self.run_probe(FakeRelay(), mode=("role", "member"), stdin="")
        self.assertEqual((empty.stdout.splitlines()[-1], empty.returncode), ("role fail (at least one public key is required)", 1))
        owner, _ = self.run_probe(FakeRelay(), mode=("role", "owner"), stdin=f"{AGENT_PUB}\n")
        self.assertNotIn("role_", owner.stdout)                                                          # "owner" is not a role this mode accepts

    def test_a_relay_that_does_not_add_the_member_fails_the_steps_after_it_and_exits_nonzero(self):
        done, _ = self.run_probe(FakeRelay(break_membership=True))
        self.assertIn("member_add fail", done.stdout)
        self.assertIn("member_query_ok fail", done.stdout)
        self.assertEqual(done.returncode, 1)

    def test_a_key_missing_from_the_vault_stops_before_any_request(self):
        relay = FakeRelay()
        done, _ = self.run_probe(relay, secrets=[{"key": "lifeos/edge/BUZZ_SELFHOST_OWNER_KEY", "value": OWNER_HEX}])
        self.assertEqual((done.stdout.strip(), done.returncode, relay.events), ("keys fail (a pilot key is not in the vault)", 1, []))

    def test_the_source_prints_no_key_and_asks_only_for_the_two_pilot_names(self):
        text = SCRIPT.read_text()
        self.assertEqual(sorted(set(__import__("re").findall(r'"lifeos/edge/(\w+)"', text))), ["BUZZ_SELFHOST_AGENT_CLAUDE", "BUZZ_SELFHOST_OWNER_KEY"])
        self.assertEqual(os.stat(SCRIPT).st_mode & 0o111, 0o111)


if __name__ == "__main__":
    unittest.main()
