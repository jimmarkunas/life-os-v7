"""LIFE-704: plain switches are read from Bitwarden without masking, validated, with fixed fail modes (each one tested)."""
import importlib.util
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("bws_plain", ROOT / ".github/actions/bws-plain/plain.py")
plain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plain)

SWITCHES = ["V7_LIVE", "ALERTS_ENABLED", "ALERTS_MUTED_UNTIL", "ALERT_SETTINGS", "V7_CLOUD_FIT", "V7_CLOUD_FIT_SCHEDULED", "V7_CLOUD_FIT_BACKLOG_BUDGET",
            "V7_CLOUD_FIT_BACKLOG_MAX", "V7_CLOUD_FIT_PARALLEL", "LIFEOS_ACQ_SSH_PORT"]


def run(names, bitwarden, live_mode="false"):
    return plain.decide(plain.requested(names, live_mode), live_mode, bitwarden)


class FailModes(unittest.TestCase):
    def test_v7_live_unreadable_is_a_dry_run(self):
        exports, report, failures = run("LIVE", None, "follow")
        self.assertEqual((exports["LIVE"], failures), ("false", []))
        self.assertEqual(report["LIVE"], "dry_run_default")

    def test_v7_live_true_goes_live_only_when_read_and_valid(self):
        self.assertEqual(run("LIVE", {"V7_LIVE": "true"}, "follow")[0]["LIVE"], "true")
        self.assertEqual(run("LIVE", {"V7_LIVE": "yes"}, "follow")[0]["LIVE"], "false")
        self.assertEqual(run("LIVE", {}, "follow")[0]["LIVE"], "false")

    def test_a_manual_input_decides_without_reading_bitwarden(self):
        self.assertEqual(plain.requested("LIVE", "true"), ["LIVE"])
        self.assertEqual(run("LIVE", None, "true")[0]["LIVE"], "true")
        self.assertEqual(run("LIVE", {"V7_LIVE": "true"}, "false")[0]["LIVE"], "false")

    def test_cloud_fit_unreadable_is_off_with_no_spend(self):
        exports, _, failures = run(",".join(n for n in SWITCHES if n.startswith("V7_CLOUD_FIT")), None)
        self.assertEqual(failures, [])
        self.assertEqual(exports["V7_CLOUD_FIT"], "off")
        self.assertEqual(exports["V7_CLOUD_FIT_SCHEDULED"], "")
        self.assertEqual(exports["V7_CLOUD_FIT_BACKLOG_MAX"], "0")
        self.assertEqual(exports["V7_CLOUD_FIT_PARALLEL"], "1")

    def test_cloud_fit_refuses_on_and_junk(self):
        self.assertEqual(run("V7_CLOUD_FIT", {"V7_CLOUD_FIT": "on"})[0]["V7_CLOUD_FIT"], "off")
        self.assertEqual(run("V7_CLOUD_FIT", {"V7_CLOUD_FIT": "shadow"})[0]["V7_CLOUD_FIT"], "shadow")
        self.assertEqual(run("V7_CLOUD_FIT_PARALLEL", {"V7_CLOUD_FIT_PARALLEL": "9"})[0]["V7_CLOUD_FIT_PARALLEL"], "1")
        self.assertEqual(run("V7_CLOUD_FIT_BACKLOG_MAX", {"V7_CLOUD_FIT_BACKLOG_MAX": "-5"})[0]["V7_CLOUD_FIT_BACKLOG_MAX"], "0")

    def test_alerts_unreadable_stay_on_with_defaults(self):
        exports, _, failures = run("ALERTS_ENABLED,ALERTS_MUTED_UNTIL,ALERT_SETTINGS", None)
        self.assertEqual(failures, [])
        self.assertEqual(exports, {"ALERTS_ENABLED": "on", "ALERTS_MUTED_UNTIL": "", "ALERT_SETTINGS": ""})

    def test_alerts_junk_values_fall_open_not_closed(self):
        exports, _, _ = run("ALERTS_ENABLED,ALERTS_MUTED_UNTIL,ALERT_SETTINGS", {"ALERTS_ENABLED": "maybe", "ALERTS_MUTED_UNTIL": "soon", "ALERT_SETTINGS": "[1]"})
        self.assertEqual(exports, {"ALERTS_ENABLED": "on", "ALERTS_MUTED_UNTIL": "", "ALERT_SETTINGS": ""})

    def test_alerts_a_deliberate_off_and_a_mute_are_honoured(self):
        exports, _, _ = run("ALERTS_ENABLED,ALERTS_MUTED_UNTIL,ALERT_SETTINGS", {"ALERTS_ENABLED": "off", "ALERTS_MUTED_UNTIL": "2026-12-01T08:00:00-06:00", "ALERT_SETTINGS": '{"a": "b"}'})
        self.assertEqual(exports, {"ALERTS_ENABLED": "off", "ALERTS_MUTED_UNTIL": "2026-12-01T08:00:00-06:00", "ALERT_SETTINGS": '{"a":"b"}'})

    def test_the_ssh_port_has_no_fallback_it_fails_with_a_fixed_code(self):
        for bitwarden in (None, {}, {"LIFEOS_ACQ_SSH_PORT": "abc"}, {"LIFEOS_ACQ_SSH_PORT": "0"}, {"LIFEOS_ACQ_SSH_PORT": "70000"}, {"LIFEOS_ACQ_SSH_PORT": ""}):
            exports, _, failures = run("LIFEOS_ACQ_SSH_PORT", bitwarden)
            self.assertEqual((exports, failures), ({}, ["LIFEOS_ACQ_SSH_PORT"]), bitwarden)
        self.assertEqual(run("LIFEOS_ACQ_SSH_PORT", {"LIFEOS_ACQ_SSH_PORT": "65535"})[0], {"LIFEOS_ACQ_SSH_PORT": "65535"})

    def test_a_name_off_the_allowlist_is_refused(self):
        for name in ("JIRA_API_TOKEN", "V7_STORE_JUMP_HOST", "BUZZ_STAGE_KEY", "NOTION_FIT_SYNC_TOKEN"):
            with self.assertRaises(ValueError):
                plain.requested(name, "false")


class Plumbing(unittest.TestCase):
    def fake_helper(self, tmp, body):
        path = Path(tmp) / "sm-action"
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    def test_parse_both_file_command_formats(self):
        got = plain.parse_commands("A=1\nB<<EOF\nx\ny\nEOF\nC=\n")
        self.assertEqual(got, {"A": "1", "B": "x\ny", "C": ""})

    def test_the_helper_is_run_with_private_files_and_its_mask_lines_never_reach_the_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = self.fake_helper(tmp, 'echo "::add-mask::true"; echo "V7_LIVE=true" >> "$GITHUB_OUTPUT"; echo "LEAK=1" >> "$GITHUB_ENV"\n')
            real_env = Path(tmp) / "real_env"
            real_env.write_text("")
            env = dict(os.environ, INPUT_ACCESS_TOKEN="t", GITHUB_ENV=str(real_env), GITHUB_OUTPUT=str(Path(tmp) / "real_out"))
            got = plain.read_bitwarden(["V7_LIVE"], env, helper, sleep=lambda s: None)
            self.assertEqual(got, {"V7_LIVE": "true"})
            self.assertEqual(real_env.read_text(), "", "the helper must not write the real job environment")

    def test_main_exports_only_validated_values_and_prints_no_value_and_no_mask(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = self.fake_helper(tmp, 'echo "V7_LIVE=true" >> "$GITHUB_OUTPUT"; echo "ALERTS_ENABLED=on" >> "$GITHUB_OUTPUT"; echo "JIRA_API_TOKEN=secret" >> "$GITHUB_OUTPUT"\n')
            real_env = Path(tmp) / "env"
            real_env.write_text("")
            env = dict(os.environ, INPUT_ACCESS_TOKEN="t", GITHUB_ENV=str(real_env), PLAIN_NAMES="LIVE,ALERTS_ENABLED", PLAIN_LIVE_MODE="follow", PLAIN_HELPER=helper)
            import contextlib
            import io
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = plain.main(env)
            self.assertEqual(code, 0)
            self.assertEqual(sorted(real_env.read_text().split()), ["ALERTS_ENABLED=on", "LIVE=true", "V7_LIVE=true"])
            self.assertNotIn("::add-mask::", out.getvalue())
            self.assertNotIn("secret", out.getvalue())
            self.assertNotIn("=true", out.getvalue())

    def test_bitwarden_unreadable_through_the_test_path_still_exports_the_safe_defaults(self):
        for names, expect in (("ALERTS_ENABLED", "ALERTS_ENABLED=on"), ("V7_CLOUD_FIT", "V7_CLOUD_FIT=off")):
            with tempfile.TemporaryDirectory() as tmp:
                real_env = Path(tmp) / "env"
                real_env.write_text("")
                env = dict(os.environ, GITHUB_ENV=str(real_env), PLAIN_NAMES=names, PLAIN_TEST_UNREADABLE="fail", PLAIN_HELPER="/nonexistent")
                self.assertEqual(plain.main(env), 0)
                self.assertEqual(real_env.read_text().strip(), expect)

    def test_a_missing_helper_fails_only_the_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_env = Path(tmp) / "env"
            real_env.write_text("")
            env = dict(os.environ, GITHUB_ENV=str(real_env), PLAIN_NAMES="LIFEOS_ACQ_SSH_PORT", PLAIN_HELPER="/nonexistent")
            self.assertEqual(plain.main(env), 1)
            self.assertEqual(real_env.read_text(), "")


class NothingPlainIsMasked(unittest.TestCase):
    """The LIFE-703 outage: a plain switch loaded through bws-load is masked everywhere. No plain name may reach bws-load, and bws-plain may never print a mask line."""

    def test_no_plain_name_is_in_any_bws_load_secrets_block(self):
        offenders = []
        for path in (ROOT / ".github").rglob("*.yml"):
            for block in re.findall(r"uses: \./\.github/actions/bws-load.*?secrets: \|\n((?:\s+\S.*\n)+)", path.read_text()):
                for name in SWITCHES + ["LIVE"]:
                    if re.search(r">\s*%s\s*$" % name, block, re.M):
                        offenders.append((path.name, name))
        self.assertEqual(offenders, [])

    def test_the_loader_never_prints_a_mask_line(self):
        source = (ROOT / ".github/actions/bws-plain/plain.py").read_text()
        self.assertEqual([row for row in source.splitlines() if "print(" in row and "add-mask" in row], [])

    def test_no_workflow_reads_a_plain_name_from_github_or_passes_it_through_a_step_output(self):
        pattern = re.compile(r"(?:vars|secrets)\.(%s)\b|steps\.\w+\.outputs\.(%s)\b" % ("|".join(SWITCHES), "|".join(SWITCHES)))
        hits = [(p.name, m.group(0)) for p in (ROOT / ".github").rglob("*.yml") for m in pattern.finditer(p.read_text())]
        self.assertEqual(hits, [])

    def test_the_plain_ids_are_the_items_in_the_ticket(self):
        self.assertEqual(len(plain.IDS), 10)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f-]{36}", v) for v in plain.IDS.values()))


class PendingIdentifier(unittest.TestCase):
    NAME = "FOLLOWUP_GCAL_BYTALOS_CALENDAR_ID"

    def test_the_personal_calendar_id_is_the_same_kind_of_pending_identifier(self):
        self.assertIn("FOLLOWUP_GCAL_PERSONAL_CALENDAR_ID", plain.PENDING)
        self.assertEqual(run("FOLLOWUP_GCAL_PERSONAL_CALENDAR_ID", {})[0], {"FOLLOWUP_GCAL_PERSONAL_CALENDAR_ID": ""})

    def test_a_pending_identifier_is_allowed_but_not_asked_for_and_falls_back_to_empty(self):
        self.assertIn(self.NAME, plain.PENDING)
        self.assertNotIn(self.NAME, plain.IDS)                                               # no Bitwarden item yet
        exports, report, failures = run(self.NAME, {})
        self.assertEqual((exports, failures), ({self.NAME: ""}, []))
        self.assertEqual(report[self.NAME], "NOT_RETURNED")

    def test_the_identifier_is_validated_and_an_unreadable_store_still_never_fails_the_job(self):
        self.assertEqual(run(self.NAME, {self.NAME: "cal@example.com"})[0][self.NAME], "cal@example.com")
        self.assertEqual(run(self.NAME, {self.NAME: "two words"})[1][self.NAME], "REFUSED_USED_DEFAULT")
        self.assertEqual(run(self.NAME, None)[2], [])


if __name__ == "__main__":
    unittest.main()
