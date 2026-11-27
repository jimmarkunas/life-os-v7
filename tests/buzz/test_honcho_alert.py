"""deploy/buzz-mcp/honcho-alert with a fake systemd-creds and an in-process fake MCP service."""

import base64
import hashlib
import http.client
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from lifeos.platform import agent_inbox
from tests.inbox.test_agent_inbox import FakeClient

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/buzz-mcp/honcho-alert"
FAKE_CREDS = (
    '#!/bin/sh\ncase "$1" in\n encrypt) out="$4"; python3 -c "import sys,base64; sys.stdout.write(\'ENC:\'+base64.b64encode(sys.stdin.buffer.read()).decode())" > "$out";;\n'
    ' decrypt) f="$3"; python3 -c "import sys,base64; d=open(sys.argv[1]).read(); sys.stdout.write(base64.b64decode(d[4:]).decode())" "$f";;\nesac\n'
)


class FakeMcp(ThreadingHTTPServer):
    def __init__(self, reply, delay=0):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.reply, self.seen, self.delay = reply, [], delay
        threading.Thread(target=lambda: self.serve_forever(poll_interval=0.01), daemon=True).start()

    def stop(self):
        self.shutdown()
        self.server_close()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.seen.append((self.headers.get("Authorization"), body["params"]["arguments"]))
            time.sleep(
                self.server.delay
            )  # the write is recorded first: a late reply is a committed write whose answer the client may never see
            code, payload = self.server.reply
            data = json.dumps({"result": {"content": [{"text": json.dumps(payload)}]}} if code == 200 else {}).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


class HonchoAlert(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "bin").mkdir()
        (self.tmp / "bin/systemd-creds").write_text(FAKE_CREDS)
        (self.tmp / "bin/systemd-creds").chmod(0o755)
        self.cred = self.tmp / "etc/honcho_alerts_mcp.cred"

    def run_script(self, *args, server=None, unit_dir=None, extra_env=None):
        env = {
            **os.environ,
            **(extra_env or {}),
            "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}",
            "HONCHO_ALERT_CRED_FILE": str(self.cred),
            "HONCHO_ALERT_URL": f"http://127.0.0.1:{server.server_address[1]}/mcp"
            if server
            else "http://127.0.0.1:9/mcp",
        }
        env.pop("CREDENTIALS_DIRECTORY", None)
        if unit_dir:
            env["CREDENTIALS_DIRECTORY"] = str(unit_dir)
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)

    def token(self):
        return base64.b64decode(self.cred.read_text()[4:]).decode()

    def test_issue_stores_the_encrypted_credential_prints_only_its_hash_and_never_overwrites(self):
        done = self.run_script("issue")
        token = self.token()
        secret = token.split(".")[2]
        self.assertTrue(token.startswith("lm1.honcho-alerts."))
        self.assertEqual(done.stdout.strip(), "honcho-alerts ok sha256=" + hashlib.sha256(secret.encode()).hexdigest())
        self.assertNotIn(secret, done.stdout + done.stderr)
        self.assertEqual(self.cred.stat().st_mode & 0o777, 0o600)
        again = self.run_script("issue")
        self.assertEqual(again.stdout.strip(), done.stdout.strip().replace(" ok ", " exists "))
        self.assertEqual(self.token(), token)

    def test_send_calls_the_governed_tool_with_the_bearer_and_names_the_outcome_only(self):
        self.run_script("issue")
        for reply, word in (
            ({"ok": True, "data": {"created": 1}}, "alert created"),
            ({"ok": True, "data": {"updated": 1}}, "alert updated"),
        ):
            server = FakeMcp((200, reply))
            self.addCleanup(server.stop)
            done = self.run_script("send", "Honcho backup failed", "see journal", "--urgency", "Now", server=server)
            self.assertEqual((done.stdout.strip(), done.returncode), (word, 0))
            auth, args = server.seen[0]
            self.assertEqual(auth, "Bearer " + self.token())
            self.assertEqual(args["operation"], "send_to_jim")
            self.assertEqual(
                (args["arguments"]["subject"], args["arguments"]["urgency"], args["arguments"]["details"]),
                ("Honcho backup failed", "Now", "see journal"),
            )
            self.assertNotIn("from", args["arguments"])
            self.assertNotIn(self.token(), done.stdout + done.stderr)

    def test_a_unit_credential_directory_is_used_before_the_encrypted_file(self):
        unit = self.tmp / "unit"
        unit.mkdir()
        (unit / "honcho_alerts_mcp").write_text("lm1.honcho-alerts." + "A" * 43 + "\n")
        server = FakeMcp((200, {"ok": True, "data": {"created": 1}}))
        self.addCleanup(server.stop)
        done = self.run_script("send", "Budget exhausted", server=server, unit_dir=unit)
        self.assertEqual(done.stdout.strip(), "alert created")
        self.assertEqual(server.seen[0][0], "Bearer lm1.honcho-alerts." + "A" * 43)

    def test_every_failure_is_one_fixed_line_and_exit_one(self):
        self.assertEqual(self.run_script("send", "x").stdout.strip(), "alert_failed no_credential")
        self.run_script("issue")
        self.assertEqual(self.run_script("send", "x").stdout.strip(), "alert_failed unreachable")
        for code, reply, line in (
            (401, None, "alert_failed http_401"),
            (200, {"ok": False, "error": {"code": "not_granted"}}, "alert_failed not_granted"),
        ):
            server = FakeMcp((code, reply))
            self.addCleanup(server.stop)
            done = self.run_script("send", "x", server=server)
            self.assertEqual((done.stdout.strip(), done.returncode), (line, 1))

    def test_bad_usage_is_refused_without_a_call(self):
        for args in (("send",), ("send", "x", "--urgency", "Whenever"), ("send", "  "), ("nope",)):
            self.assertEqual(self.run_script(*args).returncode, 64, args)

    def test_the_source_prints_no_credential_and_is_executable(self):
        self.assertEqual(os.stat(SCRIPT).st_mode & 0o111, 0o111)

    def test_a_delayed_reply_past_the_timeout_is_one_request_one_word_and_no_retry(self):
        self.run_script("issue")
        server = FakeMcp((200, {"ok": True, "data": {"created": 1}}), delay=3)
        self.addCleanup(server.stop)
        done = self.run_script(
            "send", "Honcho budget stopped", server=server, extra_env={"HONCHO_ALERT_TIMEOUT_S": "1"}
        )
        self.assertEqual((done.stdout.strip(), done.returncode), ("alert_failed timeout", 1))
        self.assertEqual(len(server.seen), 1)  # the write reached the server; the client did not resend
        self.assertNotIn(self.token(), done.stdout + done.stderr)

    def test_a_committed_write_with_a_lost_reply_is_not_duplicated_by_the_next_send(self):
        """The server's own key: sender and subject, with the sender fixed by the authenticated client. The lost reply is the first send; the next alert run replays it."""
        client = FakeClient()
        item = {
            "from": "honcho-alerts",
            "subject": "Honcho budget stopped (EXHAUSTED)",
            "urgency": "Today",
            "details": "d",
        }
        when = datetime(2026, 11, 27, 6, 0, tzinfo=timezone.utc)
        self.assertEqual(
            agent_inbox.send(client, dict(item), now=when), {"created": 1}
        )  # committed; its reply is the one the client never saw
        self.assertEqual(agent_inbox.send(client, dict(item), now=when), {"updated": 1})
        self.assertEqual(len(client.rows), 1)
        self.assertEqual(agent_inbox.normalize(dict(item), when)["key"], agent_inbox.normalize(dict(item), when)["key"])
        other = agent_inbox.normalize({**item, "subject": "Honcho nightly backup failed"}, when)["key"]
        self.assertNotEqual(other, agent_inbox.normalize(dict(item), when)["key"])
        with self.assertRaises(agent_inbox.InboxError):  # another sender can never take the key
            agent_inbox.send(
                client,
                {**item, "from": "someone-else", "key": agent_inbox.normalize(dict(item), when)["key"]},
                now=when,
            )


def load_script():
    loader = importlib.machinery.SourceFileLoader("honcho_alert_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class Timeouts(unittest.TestCase):
    """One request, a 60 s timeout, a distinct word for each failure, and no blind retry after an ambiguous outcome."""

    def words(self, error=None, body=None):
        module = load_script()
        calls = []

        def urlopen(request, timeout=None):
            calls.append(timeout)
            if error is not None:
                raise error
            return Reply(body)

        out = io.StringIO()
        with (
            mock.patch.object(module, "decrypt", return_value="lm1.honcho-alerts." + "A" * 43),
            mock.patch.object(module.urllib.request, "urlopen", urlopen),
            redirect_stdout(out),
        ):
            code = module.send("subject", "", "Today")
        return out.getvalue().strip(), code, calls

    def test_the_default_timeout_is_sixty_seconds_and_an_override_must_be_sane(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HONCHO_ALERT_TIMEOUT_S", None)
            self.assertEqual(load_script().TIMEOUT_S, 60.0)
            for bad in ("0", "-5", "500", "abc"):
                os.environ["HONCHO_ALERT_TIMEOUT_S"] = bad
                self.assertEqual(load_script().TIMEOUT_S, 60.0, bad)
            os.environ["HONCHO_ALERT_TIMEOUT_S"] = "2"
            self.assertEqual(load_script().TIMEOUT_S, 2.0)

    def test_the_request_uses_the_timeout_and_is_made_exactly_once(self):
        ok = json.dumps({"result": {"content": [{"text": json.dumps({"ok": True, "data": {"created": 1}})}]}}).encode()
        word, code, calls = self.words(body=ok)
        self.assertEqual((word, code, calls), ("alert created", 0, [60.0]))

    def test_a_direct_and_a_wrapped_timeout_are_both_the_timeout_word_and_never_retried(self):
        for error in (
            TimeoutError("t"),
            socket.timeout("t"),
            urllib.error.URLError(TimeoutError("t")),
            urllib.error.URLError(socket.timeout("t")),
        ):
            word, code, calls = self.words(error=error)
            self.assertEqual((word, code, len(calls)), ("alert_failed timeout", 1, 1), repr(error))

    def test_other_failures_keep_their_own_bounded_words(self):
        cases = (
            (urllib.error.URLError(ConnectionRefusedError()), "alert_failed unreachable"),
            (OSError("down"), "alert_failed unreachable"),
            (ConnectionResetError(), "alert_failed interrupted"),
            (BrokenPipeError(), "alert_failed interrupted"),
            (http.client.RemoteDisconnected("gone"), "alert_failed interrupted"),
            (urllib.error.HTTPError("http://x", 503, "no", {}, io.BytesIO(b"")), "alert_failed http_503"),
        )
        for error, want in cases:
            word, code, calls = self.words(error=error)
            self.assertEqual((word, code, len(calls)), (want, 1, 1), repr(error))
        for bad in (b"not json", b"{}", json.dumps({"result": {"content": []}}).encode()):
            self.assertEqual(self.words(body=bad)[:2], ("alert_failed malformed_reply", 1), bad)

    def test_a_failure_line_carries_no_credential_subject_or_payload(self):
        for error in (TimeoutError("t"), ConnectionResetError("secret-ish"), urllib.error.URLError("secret-ish")):
            word = self.words(error=error)[0]
            self.assertRegex(word, r"^alert_failed [a-z_0-9]+$")
            self.assertNotIn("AAAA", word)
            self.assertNotIn("subject", word)


if __name__ == "__main__":
    unittest.main()
