"""LIFE-876 / LIFE-867: deploy/buzz-mcp/honcho-alert with a fake systemd-creds and an in-process fake MCP service. Synthetic credentials only; nothing touches a network or a real unit."""
import base64
import hashlib
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

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/buzz-mcp/honcho-alert"
FAKE_CREDS = ('#!/bin/sh\ncase "$1" in\n encrypt) out="$4"; python3 -c "import sys,base64; sys.stdout.write(\'ENC:\'+base64.b64encode(sys.stdin.buffer.read()).decode())" > "$out";;\n'
              ' decrypt) f="$3"; python3 -c "import sys,base64; d=open(sys.argv[1]).read(); sys.stdout.write(base64.b64decode(d[4:]).decode())" "$f";;\nesac\n')


class FakeMcp(ThreadingHTTPServer):
    def __init__(self, reply):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.reply, self.seen = reply, []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def stop(self):
        self.shutdown()                                                              # stop serve_forever before the socket closes, or the thread keeps polling a dead descriptor
        self.server_close()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.seen.append((self.headers.get("Authorization"), body["params"]["arguments"]))
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

    def run_script(self, *args, server=None, unit_dir=None):
        env = {**os.environ, "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "HONCHO_ALERT_CRED_FILE": str(self.cred), "HONCHO_ALERT_URL": f"http://127.0.0.1:{server.server_address[1]}/mcp" if server else "http://127.0.0.1:9/mcp"}
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
        for reply, word in (({"ok": True, "data": {"created": 1}}, "alert created"), ({"ok": True, "data": {"updated": 1}}, "alert updated")):
            server = FakeMcp((200, reply))
            self.addCleanup(server.stop)
            done = self.run_script("send", "Honcho backup failed", "see journal", "--urgency", "Now", server=server)
            self.assertEqual((done.stdout.strip(), done.returncode), (word, 0))
            auth, args = server.seen[0]
            self.assertEqual(auth, "Bearer " + self.token())
            self.assertEqual(args["operation"], "send_to_jim")
            self.assertEqual((args["arguments"]["subject"], args["arguments"]["urgency"], args["arguments"]["details"]), ("Honcho backup failed", "Now", "see journal"))
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
        for code, reply, line in ((401, None, "alert_failed http_401"), (200, {"ok": False, "error": {"code": "not_granted"}}, "alert_failed not_granted")):
            server = FakeMcp((code, reply))
            self.addCleanup(server.stop)
            done = self.run_script("send", "x", server=server)
            self.assertEqual((done.stdout.strip(), done.returncode), (line, 1))

    def test_bad_usage_is_refused_without_a_call(self):
        for args in (("send",), ("send", "x", "--urgency", "Whenever"), ("send", "  "), ("nope",)):
            self.assertEqual(self.run_script(*args).returncode, 64, args)

    def test_the_source_prints_no_credential_and_is_executable(self):
        self.assertEqual(os.stat(SCRIPT).st_mode & 0o111, 0o111)


if __name__ == "__main__":
    unittest.main()
