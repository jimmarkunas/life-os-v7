"""LIFE-867: the Honcho backup-failed unit and the meter's budget stop reach the Agent Inbox through the one honcho-alert command, and an alert problem can never block a backup or the meter."""
import pathlib
import re
import unittest

from lifeos.edge.honcho import meter_proxy

DIR = pathlib.Path(__file__).resolve().parents[3] / "lifeos/edge/honcho"
ALERT = "/opt/lifeos-buzz-mcp/current/bin/honcho-alert send"


def lines(name):
    return [ln for ln in (DIR / name).read_text().splitlines() if ln.startswith("ExecStart")]


class Units(unittest.TestCase):
    def test_the_failed_backup_unit_writes_its_marker_first_then_alerts_and_ignores_an_alert_failure(self):
        first, second = lines("lifeos-honcho-backup-failed.service")
        self.assertIn("/var/lib/lifeos-honcho-backup/FAILED", first)
        self.assertTrue(second.startswith("ExecStart=-" + ALERT + ' "Honcho nightly backup failed" '), second)
        self.assertTrue(second.endswith("--urgency Today"))

    def test_the_budget_stop_alert_is_started_by_the_exhausted_file_the_meter_writes(self):
        (path_line,) = [ln for ln in (DIR / "lifeos-honcho-meter-alert.path").read_text().splitlines() if ln.startswith("PathChanged=")]
        self.assertEqual(path_line, "PathChanged=" + meter_proxy.STATE_DIR.rstrip("/") + "/EXHAUSTED")
        (call,) = lines("lifeos-honcho-meter-alert.service")
        self.assertTrue(call.startswith("ExecStart=-" + ALERT + ' "Honcho budget stopped (EXHAUSTED)" '), call)
        self.assertIn("Unit=lifeos-honcho-meter-alert.service", (DIR / "lifeos-honcho-meter-alert.path").read_text())

    def test_the_meter_itself_runs_no_command_and_its_unit_is_unchanged_by_the_alert(self):
        unit = (DIR / "lifeos-honcho-meter.service").read_text()
        self.assertNotIn("honcho-alert", unit)
        self.assertNotIn("honcho-alert", pathlib.Path(meter_proxy.__file__).read_text())

    def test_no_secret_or_credential_text_is_in_any_alert_line(self):
        for name in ("lifeos-honcho-backup-failed.service", "lifeos-honcho-meter-alert.service"):
            for line in lines(name):
                self.assertIsNone(re.search(r"lm1\.|Bearer|token|password|key=", line, re.I), (name, line))


if __name__ == "__main__":
    unittest.main()
