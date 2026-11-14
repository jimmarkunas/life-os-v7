"""LIFE-690: the health model document lists every alert, and the alert gateway is the only sender and refuses a push with no state key."""
import io
import contextlib
import pathlib
import re
import unittest

from lifeos.platform import alert_registry, alerts

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/platform/health-model.md"


class HealthModelDocument(unittest.TestCase):
    def rows(self):
        return {m.group(1): m.group(2) for m in re.finditer(r"^\| `([^`]+)` \| (PAGE|INFO) \|", DOC.read_text(), re.M)}

    def test_every_registered_alert_is_in_the_table_with_its_severity(self):
        rows = self.rows()
        for key, spec in alert_registry.REGISTRY.items():
            self.assertEqual(rows.get(key), spec.severity.upper(), f"{key} is missing from docs/platform/health-model.md or its severity differs")

    def test_the_table_lists_nothing_that_is_not_registered(self):
        self.assertEqual(sorted(set(self.rows()) - set(alert_registry.REGISTRY)), [])

    def test_the_rules_name_the_three_numbers(self):
        text = DOC.read_text()
        for phrase in ("every 3 hours", "last 3 finished runs", "after 7 days"):
            self.assertIn(phrase, text)


class TheGatewayIsTheOnlySender(unittest.TestCase):
    READER = "lifeos/whatsapp/oracle/forward_alerts.py"          # the Oracle relay only READS the topic (a poll) to forward the Telegram and WhatsApp copies; it never sends

    def test_nothing_but_alerts_py_sends_to_the_push_service_or_calls_the_raw_senders(self):
        offenders = []
        for path in sorted((ROOT / "lifeos").rglob("*.py")):
            relative = str(path.relative_to(ROOT))
            text = path.read_text()
            if relative == "lifeos/platform/alerts.py":
                continue
            if re.search(r"alerts\.ntfy\(|alerts\._post\(|(?<![\w.])ntfy\(", text) or ("ntfy.sh" in text and relative != self.READER):
                offenders.append(relative)
        self.assertEqual(offenders, [], "a push must go through alerts.push, alerts.deliver or `alerts push --key` (the alert gateway)")
        self.assertNotIn("method=\"POST\"", (ROOT / self.READER).read_text().split("ntfy.sh")[1][:400])

    def test_the_command_refuses_a_push_with_no_state_key(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            alerts.main(["push", "--title", "t", "--body", "b"])
        self.assertEqual(raised.exception.code, 2)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(alerts.main(["push", "--key", "never_registered", "--title", "t"]), 2)
        self.assertIn("unregistered_key", out.getvalue())


if __name__ == "__main__":
    unittest.main()
