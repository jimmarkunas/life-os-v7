"""deploy/buzz-mcp/place-credentials with fake sudo, bws and systemd-creds."""
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
        (tmp / "env").write_text("")  # the vault owner's env file (empty here)
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
        self.assertEqual(sorted(p.name for p in creds.iterdir()), ["google_read_client_id.cred", "google_read_personal_drive_refresh.cred"])  # missing and unknown names place nothing
        self.assertNotIn(SENTINEL, self.everything_written(tmp))
        for p in creds.iterdir():
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertTrue(p.read_text().startswith("ENC:"))

    def test_the_drop_in_loads_exactly_the_credentials_that_exist(self):
        done, tmp, _ = self.run_script([{"key": "openclaw/gmail/clientId", "value": SENTINEL}, {"key": "NOTION_READONLY_TOKEN", "value": SENTINEL}], "openclaw/gmail/clientId", "NOTION_READONLY_TOKEN")
        lines = (tmp / "dropin.conf").read_text().splitlines()
        self.assertEqual(lines[0], "[Service]")
        self.assertEqual([line.split("=", 1)[1].split(":", 1)[0] for line in lines[1:]], ["google_read_client_id", "notion_readonly_token"])

    def test_an_empty_value_a_failing_encrypt_and_no_arguments_change_nothing_and_say_missing(self):
        done, tmp, creds = self.run_script([{"key": "NOTION_READONLY_TOKEN", "value": "  "}], "NOTION_READONLY_TOKEN")
        self.assertEqual((done.stdout.strip(), list(creds.iterdir())), ("NOTION_READONLY_TOKEN missing", []))
        done, tmp, creds = self.run_script([{"key": "NOTION_READONLY_TOKEN", "value": SENTINEL}], "NOTION_READONLY_TOKEN", creds_fails=True)
        self.assertEqual((done.stdout.strip(), list(creds.iterdir())), ("NOTION_READONLY_TOKEN missing", []))
        self.assertNotIn(SENTINEL, done.stdout + done.stderr)
        self.assertEqual(self.run_script([])[0].returncode, 64)

    def test_the_project_prefixed_names_place_the_same_credentials(self):
        secrets = [{"key": "lifeos/edge/GMAIL_OAUTH_REFRESH_TOKEN", "value": SENTINEL}, {"key": "lifeos/edge/FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH", "value": SENTINEL}, {"key": "lifeos/edge/NOTION_READONLY_TOKEN", "value": SENTINEL}]
        done, tmp, creds = self.run_script(secrets, "lifeos/edge/GMAIL_OAUTH_REFRESH_TOKEN", "lifeos/edge/FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH", "lifeos/edge/NOTION_READONLY_TOKEN", "lifeos/edge/NOTION_API_TOKEN")
        self.assertEqual(done.stdout.splitlines(), ["lifeos/edge/GMAIL_OAUTH_REFRESH_TOKEN ok", "lifeos/edge/FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH ok", "lifeos/edge/NOTION_READONLY_TOKEN ok", "lifeos/edge/NOTION_API_TOKEN missing"])
        self.assertEqual(sorted(p.name for p in creds.iterdir()), ["google_read_bytalos_gmail_refresh.cred", "google_read_personal_gmail_refresh.cred", "notion_readonly_token.cred"])  # the hourly write token is still not on the list

    def test_every_identity_the_pilot_wires_has_its_key_in_the_service_credential_list(self):
        """a place-credentials run rewrites the drop-in from its own list, so a pilot key missing from that list is
        silently dropped from the service.
        """
        import re
        wiring = (SCRIPT.parents[2] / "lifeos/platform/pilot_community.py").read_text()
        script = SCRIPT.read_text()
        creds = re.search(r'^CREDS="([^"]*)"', script, re.M).group(1).split()
        for identity in re.findall(r'"(\w+)": env\.get\("BUZZ_KEY_(\w+)_PILOT"\)', wiring):
            self.assertIn(f"buzz_key_{identity[0]}_pilot", creds, identity)
            self.assertIn(f"echo buzz_key_{identity[0]}_pilot ;;", script, identity)

    def test_the_private_relay_key_is_placed_for_the_pilot_but_never_loaded_by_the_mcp_service(self):
        done, tmp, creds = self.run_script([{"key": "lifeos/edge/BUZZ_SELFHOST_RELAY_KEY", "value": SENTINEL}, {"key": "NOTION_READONLY_TOKEN", "value": SENTINEL}], "lifeos/edge/BUZZ_SELFHOST_RELAY_KEY", "NOTION_READONLY_TOKEN")
        self.assertEqual(done.stdout.splitlines(), ["lifeos/edge/BUZZ_SELFHOST_RELAY_KEY ok", "NOTION_READONLY_TOKEN ok"])
        self.assertEqual(sorted(p.name for p in creds.iterdir()), ["buzz_selfhost_relay_key.cred", "notion_readonly_token.cred"])
        dropin = (tmp / "dropin.conf").read_text()
        self.assertIn("notion_readonly_token", dropin)
        self.assertNotIn("buzz_selfhost_relay_key", dropin)  # the MCP service never loads the relay's key

    def test_the_claude_handoff_token_is_placed_for_hermes_but_never_loaded_by_the_mcp_service(self):
        done, tmp, creds = self.run_script([{"key": "lifeos/edge/CLAUDE_CODE_OAUTH_TOKEN", "value": SENTINEL}, {"key": "NOTION_READONLY_TOKEN", "value": SENTINEL}], "lifeos/edge/CLAUDE_CODE_OAUTH_TOKEN", "NOTION_READONLY_TOKEN")
        self.assertEqual(done.stdout.splitlines(), ["lifeos/edge/CLAUDE_CODE_OAUTH_TOKEN ok", "NOTION_READONLY_TOKEN ok"])
        self.assertEqual(sorted(p.name for p in creds.iterdir()), ["claude_code_oauth_token.cred", "notion_readonly_token.cred"])
        self.assertNotIn("claude_code_oauth_token", (tmp / "dropin.conf").read_text())  # the MCP service never loads Hermes's Claude token
        self.assertNotIn(SENTINEL, done.stdout + done.stderr + self.everything_written(tmp))

    def test_list_names_prints_only_matching_names_never_a_value_and_places_nothing(self):
        secrets = [{"key": "GMAIL_OAUTH_REFRESH_TOKEN", "value": SENTINEL}, {"key": "gmail_other", "value": SENTINEL}, {"key": "NOTION_READONLY_TOKEN", "value": SENTINEL}]
        done, tmp, creds = self.run_script(secrets, "--list-names", "gmail")
        self.assertEqual((done.returncode, done.stdout.splitlines(), list(creds.iterdir())), (0, ["'GMAIL_OAUTH_REFRESH_TOKEN'", "'gmail_other'"], []))
        self.assertNotIn(SENTINEL, done.stdout + done.stderr)
        self.assertEqual(self.run_script(secrets, "--list-names", "nothing-matches")[0].stdout.strip(), "<none>")
        self.assertEqual(self.run_script(secrets, "--list-names")[0].returncode, 64)

    def test_the_source_never_echoes_a_value_and_only_runs_bws_as_the_vault_owner(self):
        text = SCRIPT.read_text()
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("set -x", code)
        self.assertIn('sudo -u "$RUNAS" -H sh -c', code)
        self.assertIn("2>/dev/null", code)  # bws errors are not shown
        self.assertEqual(stat.S_IMODE(SCRIPT.stat().st_mode) & 0o111, 0o111)


if __name__ == "__main__":
    unittest.main()
