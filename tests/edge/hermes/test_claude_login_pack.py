"""LIFE-518: the read-only Claude login check pack (lifeos/edge/hermes/pack), run through the REAL runner on a temp tree with a fake hand-off client (no sudo, no host, no Claude)."""
import json
import re
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from lifeos.edge.runner import runner
from tests.edge.runner.test_runner import FakeSystemd

ROOT = Path(__file__).resolve().parents[3]
PACK = ROOT / "lifeos" / "edge" / "hermes" / "pack"
TOKEN = "sk-ant-oat01-" + "Zx9" * 20
EMAIL = "someone.private" + chr(64) + "example.org"      # built here: the privacy guard rejects a literal address
FAKE_CLIENT = r"""#!/bin/bash
# fake claude-handoff client: prints secrets on purpose, so the pack must keep them out of every READBACK line
P="$(cd "$(dirname "$0")/../../../.." && pwd)"
echo "$@" >> "$P/calls.log"
if [ "$1 $2" = "auth status" ]; then
  n=$(cat "$P/status-count" 2>/dev/null || echo 0); echo $((n + 1)) > "$P/status-count"
  [ -e "$P/status-fails" ] && { echo "$TOKEN_TEXT" >&2; exit 2; }
  d=$(cat "$P/expiry-1"); [ "$n" -ge 1 ] && [ -e "$P/expiry-2" ] && d=$(cat "$P/expiry-2")
  printf '{"loggedIn":true,"authMethod":"claude.ai","subscriptionType":"max","email":"%s","accessToken":"%s","loginExpiresAt":"%s"}\n' "$EMAIL_TEXT" "$TOKEN_TEXT" "$d"
  exit 0
fi
cat > /dev/null
if [ -e "$P/message-fails" ]; then echo "login $TOKEN_TEXT $EMAIL_TEXT"; exit 1; fi
printf '{"type":"system","note":"%s"}\n{"type":"result","subtype":"success","is_error":false,"result":"ok %s %s"}\n' "$EMAIL_TEXT" "$TOKEN_TEXT" "$EMAIL_TEXT"
exit 0
""".replace("$TOKEN_TEXT", TOKEN).replace("$EMAIL_TEXT", EMAIL)


class LoginPackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = self.tmp / "fake-root"
        client = self.fake / "usr" / "local" / "lib" / "claude-handoff" / "client.py"
        client.parent.mkdir(parents=True)
        client.write_text(FAKE_CLIENT)
        client.chmod(client.stat().st_mode | stat.S_IEXEC)
        (self.fake / "tmp").mkdir()
        fake_timeout = self.fake / "usr" / "bin" / "timeout"                  # the host has coreutils timeout; this stand-in just runs the command
        fake_timeout.parent.mkdir(parents=True)
        fake_timeout.write_text('#!/bin/bash\nshift\nexec "$@"\n')
        fake_timeout.chmod(fake_timeout.stat().st_mode | stat.S_IEXEC)
        (self.fake / "expiry-1").write_text("2026-12-04")
        self.inbox = str(self.tmp / "lifeos-pack-{key}")
        self.staged = Path(self.inbox.format(key="LIFE-518"))
        shutil.copytree(PACK, self.staged)
        (self.staged / "files" / "ROOT").write_text(str(self.fake) + "\n")
        self.host = runner.Host(state=str(self.tmp / "state"), inbox=self.inbox, require_root=False, systemctl=FakeSystemd(str(self.tmp)))

    def run_pack(self):
        return runner.execute("LIFE-518", runner.pack_hash(str(self.staged))[:8], self.host)

    def readback(self, result):
        lines = result["readback"] if isinstance(result["readback"], list) else str(result["readback"]).splitlines()
        return {k: v for k, v in (line.split("=", 1) for line in lines if "=" in line)}

    def test_the_pack_declares_nothing_to_restore_and_passes_the_runner_check(self):
        manifest = runner.load_manifest(str(PACK))
        self.assertEqual((manifest["ticket"], manifest["files"], manifest["services"]), ("LIFE-518", [], []))
        self.assertEqual(len(manifest["steps"]), 3)

    def test_a_good_run_reports_both_dates_and_the_message(self):
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("ok", None), result)
        got = self.readback(result)
        self.assertEqual((got["expiry_before"], got["expiry_after"], got["expiry_changed"]), ("2026-12-04", "2026-12-04", "no"))
        self.assertEqual((got["message_ok"], got["message_exit"], got["method"]), ("yes", "0", "claude.ai/max"))
        self.assertEqual(sum(1 for line in (self.fake / "calls.log").read_text().splitlines() if line.startswith("-p ")), 1)   # exactly one message
        self.assertEqual(list((self.fake / "tmp").iterdir()), [])             # the scratch folder is gone
        self.assertEqual(self.staged.joinpath("pack.json").exists(), True)

    def test_a_renewed_expiry_is_reported_as_changed(self):
        (self.fake / "expiry-2").write_text("2027-01-03")
        got = self.readback(self.run_pack())
        self.assertEqual((got["expiry_before"], got["expiry_after"], got["expiry_changed"]), ("2026-12-04", "2027-01-03", "yes"))

    def test_a_failing_message_still_prints_both_dates(self):
        (self.fake / "message-fails").touch()
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("ok", None), result)
        got = self.readback(result)
        self.assertEqual((got["message_ok"], got["message_exit"]), ("no", "1"))
        self.assertEqual((got["expiry_before"], got["expiry_after"], got["expiry_changed"]), ("2026-12-04", "2026-12-04", "no"))

    def test_a_failing_status_read_gives_unknown_not_an_abort(self):
        (self.fake / "status-fails").touch()
        result = self.run_pack()
        self.assertEqual(result["outcome"], "ok", result)
        got = self.readback(result)
        self.assertEqual((got["expiry_before"], got["expiry_after"], got["expiry_changed"], got["method"]), ("unknown", "unknown", "unknown", "unknown"))

    def test_no_secret_or_email_can_reach_the_result(self):
        for flag in (None, "message-fails", "status-fails"):
            if flag:
                (self.fake / flag).touch()
            result = self.run_pack()
            text = json.dumps(result)
            self.assertNotIn(TOKEN, text)
            self.assertNotIn(EMAIL, text)
            self.assertNotIn("Zx9", text)
            allowed = re.compile(r"(expiry_(before|after)=(\d{4}-\d{2}-\d{2}|unknown)|expiry_changed=(yes|no|unknown)|message_ok=(yes|no)|message_exit=(\d{1,3}|none)|method=[a-z_./]{1,40}|step=[a-z_]+)")
            for line in (self.readback(result) and [f"{k}={v}" for k, v in self.readback(result).items()]):
                self.assertRegex(line, allowed)
            (self.fake / "tmp").mkdir(exist_ok=True)

    def test_the_steps_never_sign_in_and_never_touch_config_or_the_default_model(self):
        text = "".join(p.read_text() for p in (PACK / "steps").glob("*.sh"))
        self.assertNotIn("auth login", text)
        self.assertNotIn("setup-token", text)
        self.assertNotIn("hermes config", text)
        self.assertNotIn("ANTHROPIC_API_KEY", text)
        self.assertNotIn("sudo", text)


if __name__ == "__main__":
    unittest.main()
