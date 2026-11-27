"""The Mac stdio bridge relays JSON-RPC to the Oracle service over ssh with the Keychain credential, answers
notifications with nothing, and never puts the credential on a command line or in a reply. No network: Keychain and
ssh are fakes."""

import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lifeos_mcp_stdio", ROOT / "tools" / "lifeos_mcp_stdio.py")
bridge = importlib.util.module_from_spec(spec)
sys.modules["lifeos_mcp_stdio"] = bridge
spec.loader.exec_module(bridge)

SECRET = "S" * 43


class Run:
    def __init__(self, code=0, out=SECRET, raises=None):
        self.code, self.out, self.raises, self.calls = code, out, raises, []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.raises:
            raise self.raises
        return type("R", (), {"returncode": self.code, "stdout": self.out})()


class Keychain(unittest.TestCase):
    def test_the_secret_comes_from_the_keychain_item_and_becomes_an_lm1_token(self):
        run = Run()
        self.assertEqual(bridge.keychain_secret("codex", run), f"lm1.codex.{SECRET}")
        argv = run.calls[0][0]
        self.assertEqual(argv[:6], ["security", "find-generic-password", "-s", "lifeos-mcp-codex", "-a", "lifeos-mcp"])
        self.assertNotIn(SECRET, " ".join(argv))

    def test_a_missing_item_stops_without_printing_anything_secret(self):
        with self.assertRaises(SystemExit) as stop:
            bridge.keychain_secret("codex", Run(code=44, out=""))
        self.assertIn("lifeos-mcp-codex", str(stop.exception))


class Relay(unittest.TestCase):
    def test_a_request_goes_over_ssh_with_the_token_on_stdin_only_and_its_reply_is_one_line(self):
        run = Run(out='{"jsonrpc":"2.0",\n"id":1,"result":{"ok":true}}')
        out = bridge.relay_line('{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n', "lm1.codex.S", "/tmp/c", run)
        self.assertEqual(json.loads(out)["result"], {"ok": True})
        self.assertNotIn("\n", out)
        argv, kw = run.calls[0]
        self.assertTrue(kw["input"].startswith("Authorization: Bearer lm1.codex.S\n"))
        self.assertFalse(any("lm1.codex.S" in a for a in argv))  # never on the ssh command line
        self.assertIn("opc@lifeos-edge-01", argv)
        self.assertIn("@/dev/fd/3", argv[-1])  # the header reaches curl through a file descriptor, not argv

    def test_a_notification_and_a_blank_line_get_no_reply(self):
        run = Run(out="")
        self.assertIsNone(
            bridge.relay_line('{"jsonrpc":"2.0","method":"notifications/initialized"}', "t", "/tmp/c", run)
        )
        self.assertIsNone(bridge.relay_line("   \n", "t", "/tmp/c", run))

    def test_failures_become_json_rpc_errors_without_the_token(self):
        for run in (
            Run(code=255, out=""),
            Run(raises=OSError("down")),
            Run(raises=subprocess.TimeoutExpired("ssh", 90)),
        ):
            out = bridge.relay_line('{"jsonrpc":"2.0","id":7,"method":"tools/list"}', "lm1.codex.S", "/tmp/c", run)
            doc = json.loads(out)
            self.assertEqual((doc["id"], doc["error"]["message"]), (7, "lifeos_mcp_unreachable"))
            self.assertNotIn("lm1.codex.S", out)
        self.assertEqual(json.loads(bridge.relay_line("not json", "t", "/tmp/c", Run()))["error"]["code"], -32700)

    def test_a_non_json_reply_is_wrapped_as_an_error(self):
        out = bridge.relay_line('{"jsonrpc":"2.0","id":3,"method":"x"}', "t", "/tmp/c", Run(out="<html>"))
        self.assertEqual(json.loads(out)["error"]["message"], "<html>")


class ControlPath(unittest.TestCase):
    def test_the_multiplex_socket_path_fits_the_macos_limit(self):
        path = "/tmp/lm-codex-" + "f" * 40  # %C expands to 40 hex characters
        self.assertLess(len(path), 104)


if __name__ == "__main__":
    unittest.main()
