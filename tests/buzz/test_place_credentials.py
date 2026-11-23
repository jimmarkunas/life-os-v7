"""LIFE-875: deploy/buzz-mcp/place-credentials with fake sudo, bws and systemd-creds. Synthetic values only; the script must print names and ok/missing and never a value."""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/buzz-mcp/place-credentials"
SENTINEL = "SYNTHETIC-VALUE-9a8b7c6d5e4f"
FAKES = {
    "sudo": '#!/bin/sh\nwhile [ "$1" = -u ] || [ "$1" = -H ]; do [ "$1" = -u ] && shift 2 || shift; done\nexec "$@"\n',
    "bws": '#!/bin/sh\ncat "$FAKE_BWS_JSON"\n',
    "systemd-creds": '#!/bin/sh\n# encrypt --name=X - OUT: reads stdin, writes a non-plaintext file\nout="$4"; python3 -c "import sys,hashlib; sys.stdout.write(\'ENC:\'+hashlib.sha256(sys.stdin.buffer.read()).hexdigest())" > "$out"\n',
    "systemctl": "#!/bin/sh\nexit 0\n",
}


class PlaceCredentials(unittest.TestCase):
    def run_script(self, secrets, *names, creds_fails=False):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        bin_dir, cred_dir = tmp / "bin", tmp / "creds"
        bin_dir.mkdir()
        cred_dir.mkdir()
        for name, text in FAKES.items():
            if name == "systemd-creds" and creds_fails:
                text = "#!/bin/sh\nexit 1\n"
            (bin_dir / name).write_text(text)
            (bin_dir / name).chmod(0o755)
        (tmp / "bws.json").write_text(json.dumps(secrets))
        (tmp / "env").write_text("")                                                  # the vault owner's env file (empty here; the fake bws needs no token)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_BWS_JSON": str(tmp / "bws.json"), "PLACE_CREDENTIALS_DIR": str(cred_dir), "PLACE_CREDENTIALS_ENV": str(tmp / "env"),
               "PLACE_CREDENTIALS_DROPIN": str(tmp / "dropin.conf")}
        done = subprocess.run(["sh", str(SCRIPT), *names], capture_output=True, text=True, env=env, cwd=tmp)
        return done, tmp, cred_dir

    def everything_written(self, tmp):
        return "\n".join(p.read_text(errors="replace") for p in tmp.rglob("*") if p.is_file() and p.name != "bws.json")

    def test_it_prints_only_names_with_ok_or_missing_and_never_a_value(self):
        secrets = [{"key": "openclaw/gmail/clientId", "value": SENTINEL}, {"key": "openclaw/drive/refreshToken", "value": SENTINEL + "-2"}]
        done, tmp, creds = self.run_script(secrets, "openclaw/gmail/clientId", "openclaw/drive/refreshToken", "GMAIL_OAUTH_REFRESH_TOKEN", "not/allowed/name")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.splitlines(), ["openclaw/gmail/clientId ok", "openclaw/drive/refreshToken ok", "GMAIL_OAUTH_REFRESH_TOKEN missing", "not/allowed/name missing"])
        self.assertNotIn(SENTINEL, done.stdout + done.stderr)
        self.assertEqual(sorted(p.name for p in creds.iterdir()), ["google_read_client_id.cred", "google_read_personal_drive_refresh.cred"])      # missing and unknown names place nothing
        self.assertNotIn(SENTINEL, self.everything_written(tmp))                                                                                 # not in a credential file, the drop-in or anywhere else
        for p in creds.iterdir():
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertTrue(p.read_text().startswith("ENC:"))

    def test_the_drop_in_loads_exactly_the_credentials_that_exist(self):
        done, tmp, _ = self.run_script([{"key": "openclaw/gmail/clientId", "value": SENTINEL}, {"key": "NOTION_API_TOKEN", "value": SENTINEL}], "openclaw/gmail/clientId", "NOTION_API_TOKEN")
        lines = (tmp / "dropin.conf").read_text().splitlines()
        self.assertEqual(lines[0], "[Service]")
        self.assertEqual([line.split("=", 1)[1].split(":", 1)[0] for line in lines[1:]], ["google_read_client_id", "notion_api_token"])

    def test_an_empty_value_a_failing_encrypt_and_no_arguments_change_nothing_and_say_missing(self):
        done, tmp, creds = self.run_script([{"key": "NOTION_API_TOKEN", "value": "  "}], "NOTION_API_TOKEN")
        self.assertEqual((done.stdout.strip(), list(creds.iterdir())), ("NOTION_API_TOKEN missing", []))
        done, tmp, creds = self.run_script([{"key": "NOTION_API_TOKEN", "value": SENTINEL}], "NOTION_API_TOKEN", creds_fails=True)
        self.assertEqual((done.stdout.strip(), list(creds.iterdir())), ("NOTION_API_TOKEN missing", []))
        self.assertNotIn(SENTINEL, done.stdout + done.stderr)
        self.assertEqual(self.run_script([])[0].returncode, 64)

    def test_the_source_never_echoes_a_value_and_only_runs_bws_as_the_vault_owner(self):
        text = SCRIPT.read_text()
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("set -x", code)
        self.assertIn('sudo -u "$RUNAS" -H sh -c', code)
        self.assertIn("2>/dev/null", code)                                           # bws errors are not shown
        self.assertEqual(stat.S_IMODE(SCRIPT.stat().st_mode) & 0o111, 0o111)


if __name__ == "__main__":
    unittest.main()
