"""The Mac stdio bridge relays JSON-RPC with the Keychain credential as a bearer header, answers notifications with
nothing, and never puts the credential on a command line or in a reply. No network: Keychain, tunnel and HTTP are fakes."""

import importlib.util
import io
import json
import pathlib
import sys
import unittest
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lifeos_mcp_stdio", ROOT / "tools" / "lifeos_mcp_stdio.py")
bridge = importlib.util.module_from_spec(spec)
sys.modules["lifeos_mcp_stdio"] = bridge
spec.loader.exec_module(bridge)

SECRET = "S" * 43


class Run:
    def __init__(self, code=0, out=SECRET):
        self.code, self.out, self.argv = code, out, None

    def __call__(self, argv, **kw):
        self.argv = argv
        return type("R", (), {"returncode": self.code, "stdout": self.out})()


class Opener:
    def __init__(self, reply='{"jsonrpc":"2.0","id":1,"result":{}}', error=None):
        self.reply, self.error, self.requests = reply, error, []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        return io.BytesIO(self.reply.encode())


class Keychain(unittest.TestCase):
    def test_the_secret_comes_from_the_keychain_item_and_becomes_an_lm1_token(self):
        run = Run()
        self.assertEqual(bridge.keychain_secret("codex", run), f"lm1.codex.{SECRET}")
        self.assertEqual(
            run.argv[:6], ["security", "find-generic-password", "-s", "lifeos-mcp-codex", "-a", "lifeos-mcp"]
        )
        self.assertNotIn(SECRET, " ".join(run.argv))

    def test_a_missing_item_stops_without_printing_anything_secret(self):
        with self.assertRaises(SystemExit) as stop:
            bridge.keychain_secret("codex", Run(code=44, out=""))
        self.assertIn("lifeos-mcp-codex", str(stop.exception))


class Relay(unittest.TestCase):
    def test_a_request_is_posted_with_the_bearer_header_and_its_reply_is_one_line(self):
        opener = Opener('{"jsonrpc":"2.0",\n"id":1,"result":{"ok":true}}')
        out = bridge.relay_line(
            '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n', "http://x/mcp", "lm1.codex.S", opener
        )
        self.assertEqual(json.loads(out)["result"], {"ok": True})
        self.assertNotIn("\n", out)
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer lm1.codex.S")

    def test_a_notification_and_a_blank_line_get_no_reply(self):
        opener = Opener("")
        self.assertIsNone(
            bridge.relay_line('{"jsonrpc":"2.0","method":"notifications/initialized"}', "http://x/mcp", "t", opener)
        )
        self.assertIsNone(bridge.relay_line("   \n", "http://x/mcp", "t", opener))

    def test_errors_become_json_rpc_errors_without_the_token(self):
        for err in (
            urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b'{"error":"invalid_token"}')),
            OSError("down"),
        ):
            out = bridge.relay_line(
                '{"jsonrpc":"2.0","id":7,"method":"tools/list"}', "http://x/mcp", "lm1.codex.S", Opener(error=err)
            )
            doc = json.loads(out)
            self.assertEqual(doc["id"], 7)
            self.assertNotIn("lm1.codex.S", out)
        self.assertEqual(
            json.loads(bridge.relay_line("not json", "http://x/mcp", "t", Opener()))["error"]["code"], -32700
        )


class Tunnel(unittest.TestCase):
    def test_the_tunnel_targets_oracle_loopback_8787_and_carries_no_credential(self):
        seen = {}

        def popen(argv, **kw):
            seen["argv"] = argv
            return type("P", (), {"terminate": lambda self: None})()

        class Conn:
            def close(self):
                pass

        bridge.open_tunnel(40123, popen=popen, connect=lambda addr, timeout: Conn())
        self.assertIn("40123:127.0.0.1:8787", seen["argv"])
        self.assertEqual(seen["argv"][-1], "opc@lifeos-edge-01")
        self.assertFalse(any("lm1." in a for a in seen["argv"]))


if __name__ == "__main__":
    unittest.main()
