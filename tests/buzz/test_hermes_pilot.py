"""LIFE-870 (S1 A): deploy/buzz-mcp/hermes-pilot with a fake systemd-creds and a STUB sender in a temporary release (no Jira, no relay, no network); synthetic values only."""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/buzz-mcp/hermes-pilot"
FAKE_CREDS = ('#!/bin/sh\ncase "$1" in\n decrypt) f="$3"; python3 -c "import sys,base64; d=open(sys.argv[1]).read(); sys.stdout.write(base64.b64decode(d[4:]).decode())" "$f";;\nesac\n')
STUB = ('import os\nclass HermesError(Exception):\n    pass\n'
        'def run(limit, live):\n'
        '    if os.path.exists(os.path.join(os.path.dirname(__file__), "FAIL")):\n        raise HermesError("HERMES_BUZZ_RELAY_UNREACHABLE")\n'
        '    return {"limit": limit, "live": live, "relay": os.environ["BUZZ_RELAY"], "url": os.environ["BUZZ_PILOT_URL"], "has_key": len(os.environ["BUZZ_PILOT_KEY"]) > 10,\n'
        '            "has_jira": bool(os.environ["JIRA_API_TOKEN"] and os.environ["JIRA_EMAIL"] and os.environ["JIRA_BASE_URL"]), "channel": os.environ["BUZZ_PILOT_CHANNEL"]}\n')
SECRETS = {"pilot_key": "SYNTHETIC-PILOT-KEY-1a2b3c", "jira_email": "synthetic@example.com", "jira_api_token": "SYNTHETIC-JIRA-TOKEN-4d5e6f"}


class HermesPilot(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "bin").mkdir()
        (self.tmp / "bin/systemd-creds").write_text(FAKE_CREDS)
        (self.tmp / "bin/systemd-creds").chmod(0o755)
        (self.tmp / "rel/lifeos").mkdir(parents=True)
        (self.tmp / "rel/lifeos/__init__.py").write_text("")
        (self.tmp / "rel/lifeos/hermes_requests.py").write_text(STUB)
        self.dir = self.tmp / "etc"
        self.dir.mkdir()

    def place(self, skip=()):
        for name, value in {"channel": "c" * 8, "hermes_pubkey": "ab" * 32, "jira_base_url": "https://example.atlassian.net"}.items():
            if name not in skip:
                (self.dir / name).write_text(value + "\n")
        for name, value in SECRETS.items():
            if name not in skip:
                (self.dir / (name + ".cred")).write_text("ENC:" + base64.b64encode(value.encode()).decode())

    def run_script(self, *args, fail=False):
        (self.tmp / "rel/lifeos/FAIL").write_text("x") if fail else None
        env = {**os.environ, "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "HERMES_PILOT_DIR": str(self.dir), "HERMES_PILOT_RELEASE": str(self.tmp / "rel")}
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)

    def test_a_dry_run_starts_the_sender_on_the_pilot_with_every_setting_and_prints_only_its_counts(self):
        self.place()
        done = self.run_script()
        out = json.loads(done.stdout)
        self.assertEqual((done.returncode, out["relay"], out["url"], out["live"], out["limit"], out["has_key"], out["has_jira"]), (0, "pilot", "http://127.0.0.1:3100", False, 5, True, True))
        for secret in SECRETS.values():
            self.assertNotIn(secret, done.stdout + done.stderr)

    def test_live_and_limit_are_passed_and_nothing_else_is(self):
        self.place()
        out = json.loads(self.run_script("--live", "--limit", "2").stdout)
        self.assertEqual((out["live"], out["limit"]), (True, 2))
        for bad in (("--limit",), ("--limit", "x"), ("--nope",)):
            self.assertEqual(self.run_script(*bad).returncode, 64, bad)

    def test_a_missing_setting_or_credential_is_named_and_nothing_runs(self):
        self.place(skip=("channel", "jira_api_token"))
        done = self.run_script("--live")
        self.assertEqual((done.stdout.strip(), done.returncode), ("not_ready channel,jira_api_token", 2))

    def test_a_sender_failure_is_one_fixed_code_and_a_crash_is_never_passed_on(self):
        self.place()
        done = self.run_script(fail=True)
        self.assertEqual((done.stdout.strip(), done.returncode), ("HERMES_BUZZ_RELAY_UNREACHABLE", 1))
        (self.tmp / "rel/lifeos/hermes_requests.py").write_text('raise RuntimeError("SYNTHETIC-JIRA-TOKEN-4d5e6f in a traceback")\n')
        crash = self.run_script()
        self.assertEqual((crash.stdout.strip(), crash.returncode), ("HERMES_NO_OUTPUT", 1))
        self.assertNotIn("SYNTHETIC", crash.stdout + crash.stderr)

    def test_the_source_has_no_timer_loop_or_scheduler_and_is_executable(self):
        text = SCRIPT.read_text()
        for word in ("while True", "sleep(", "crontab", "OnCalendar", "systemctl"):
            self.assertNotIn(word, text)
        self.assertEqual(os.stat(SCRIPT).st_mode & 0o111, 0o111)


if __name__ == "__main__":
    unittest.main()
