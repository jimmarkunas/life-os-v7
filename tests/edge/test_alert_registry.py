"""LIFE-75: every alert LIFE OS can send is in lifeos/platform/alert_registry.py, with the severity Jim approved, and nothing sends around it."""
import ast
import io
import json
import pathlib
import re
import unittest
from contextlib import redirect_stdout
from unittest import mock

from lifeos.platform import alert_registry as registry, alerts

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY = "lifeos/platform/alerts.py"
APPROVED_PAGE = {"source_stopped_*", "expiry_*", "jira_rollover_stuck", "calendar_sync_stuck", "imessage_action", "pipeline_stalled", "pipeline_failing", "megibow_stale",
                 "run_failed", "domain_jobs_failed", "purge_failed", "safety_failed", "test_push"}          # Jim, Oct 25 2026 (LIFE-75 comment 11182); everything else is INFO


def sources():
    return {str(path.relative_to(ROOT)): path.read_text() for path in sorted((ROOT / "lifeos").rglob("*.py"))}


def constants(tree):
    return {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
            for t in n.targets if isinstance(t, ast.Name)}


def key_of(node, names):
    """A string key or the prefix of an f-string key (as `prefix*`), else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return node.values[0].value + "*"
    return None


def severity_of(node):
    name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
    return {"PAGE": "page", "INFO": "info"}.get(name)


def findings(texts):
    """-> (alert_calls, push_calls, ntfy_users): what the code builds. alert_calls: [(file, key, severity)]; push_calls: [(file, key)]; ntfy_users: [file]."""
    alert_calls, push_calls, ntfy_users = [], [], []
    for name, text in texts.items():
        tree = ast.parse(text)
        names = constants(tree)
        for node in ast.walk(tree):
            if name != GATEWAY and isinstance(node, ast.Attribute) and node.attr == "ntfy" and isinstance(node.value, ast.Name) and node.value.id == "alerts":
                ntfy_users.append(name)
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
            if callee == "Alert" and owner == "alerts" and len(node.args) >= 2:
                alert_calls.append((name, key_of(node.args[0], names), severity_of(node.args[1])))
            if callee == "push" and owner == "alerts" and node.args:
                push_calls.append((name, key_of(node.args[0], names)))
    return alert_calls, push_calls, ntfy_users


class Registry(unittest.TestCase):
    def test_every_entry_is_complete(self):
        for key, spec in registry.REGISTRY.items():
            with self.subTest(key=key):
                self.assertIn(spec.severity, (registry.PAGE, registry.INFO))
                self.assertTrue(spec.owner and spec.meaning.strip())
                self.assertTrue(set(spec.channels) <= set(registry.CHANNELS) and spec.channels)
                self.assertRegex(key, r"^[a-z0-9_]+\*?$")

    def test_page_is_exactly_the_set_jim_approved(self):
        self.assertEqual({k for k, s in registry.REGISTRY.items() if s.severity == registry.PAGE}, APPROVED_PAGE)

    def test_lookup_prefers_the_exact_key_then_the_longest_family(self):
        self.assertEqual(registry.severity("source_stopped_dice"), "page")
        self.assertEqual(registry.severity("source_stuck_web_gh"), "info")
        self.assertEqual(registry.severity("expiry_cronjob"), "page")
        self.assertIsNone(registry.lookup("brand_new_alert"))
        self.assertEqual(registry.channels("quiet_24h"), ("ntfy",))
        self.assertEqual(registry.channels("run_failed"), registry.CHANNELS)


class TheCodeUsesOnlyRegisteredAlerts(unittest.TestCase):
    def test_every_alert_built_in_the_code_is_registered_with_the_registrys_severity(self):
        alert_calls, _, _ = findings(sources())
        self.assertGreaterEqual(len(alert_calls), 14)
        for name, key, severity in alert_calls:
            with self.subTest(file=name, key=key):
                self.assertIsNotNone(key, "an alert key must be a literal, a module constant or an f-string with a literal start")
                spec = registry.REGISTRY.get(key) if key.endswith("*") else registry.lookup(key)         # an f-string key is a registered family: "source_stopped_{name}" is source_stopped_*
                self.assertIsNotNone(spec, f"{key} is not in lifeos/platform/alert_registry.py")
                self.assertEqual(severity, spec.severity)

    def test_every_push_in_the_code_names_a_registered_alert(self):
        _, push_calls, _ = findings(sources())
        literal = [(name, key) for name, key in push_calls if key is not None]
        self.assertGreaterEqual(len(literal), 3)
        for name, key in literal:
            with self.subTest(file=name, key=key):
                self.assertIsNotNone(registry.lookup(key), f"{key} is not in lifeos/platform/alert_registry.py")
        self.assertEqual(sorted(name for name, key in push_calls if key is None), ["lifeos/platform/gate.py"])      # the one with a computed key: checked by its own test below

    def test_no_module_but_the_gateway_references_alerts_ntfy(self):
        self.assertEqual(sorted(set(findings(sources())[2])), [])

    def test_the_incident_pushes_in_the_gate_carry_registered_keys_and_agree_on_severity(self):
        tree = ast.parse(sources()["lifeos/platform/gate.py"])
        rows = [tuple(e.value for e in n.elts) for n in ast.walk(tree) if isinstance(n, ast.Tuple) and len(n.elts) == 4 and all(isinstance(e, ast.Constant) for e in n.elts)
                and n.elts[2].value in ("page", "info")]
        self.assertEqual(len(rows), 4)
        for title, body, severity, key in rows:
            with self.subTest(key=key):
                self.assertEqual(registry.severity(key), severity)

    def test_the_imessage_pushes_use_registered_keys(self):
        from lifeos.imessage import stage
        self.assertEqual(registry.severity(stage.KEY), "page")

    def test_every_workflow_push_names_a_registered_key(self):
        found = []
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for line in path.read_text().splitlines():
                if "lifeos.platform.alerts push" in line:
                    match = re.search(r"--key (\w+)", line)
                    found.append((path.name, match.group(1) if match else None))
        self.assertGreaterEqual(len(found), 6)
        for name, key in found:
            with self.subTest(file=name, key=key):
                self.assertIsNotNone(key, "a workflow push needs --key")
                self.assertIsNotNone(registry.lookup(key))

    def test_the_checks_turn_red_for_an_unlisted_alert_a_wrong_severity_and_a_direct_send(self):
        probe = {"lifeos/evil/watch.py": 'from lifeos.platform import alerts\nalerts.Alert("brand_new_alert", alerts.PAGE, "t")\nalerts.Alert(f"source_stopped_{n}", alerts.INFO, "t")\n'
                                         'alerts.push("another_new_one", "t", "b")\nalerts.ntfy("topic", "t", "b", "page")\n'}
        alert_calls, push_calls, ntfy_users = findings(probe)
        self.assertEqual(alert_calls, [("lifeos/evil/watch.py", "brand_new_alert", "page"), ("lifeos/evil/watch.py", "source_stopped_*", "info")])
        self.assertIsNone(registry.lookup("brand_new_alert"))
        self.assertNotEqual("info", registry.severity("source_stopped_dice"))              # a wrong severity is caught
        self.assertEqual(push_calls, [("lifeos/evil/watch.py", "another_new_one")])
        self.assertEqual(ntfy_users, ["lifeos/evil/watch.py"])


class Push(unittest.TestCase):
    def setUp(self):
        self._hibernated, alerts.HIBERNATED = alerts.HIBERNATED, False
        alerts.reset_flood()
        self.addCleanup(self.restore)
        self.sent = []

    def restore(self):
        alerts.HIBERNATED = self._hibernated
        alerts.reset_flood()

    def post(self, request, timeout=None):
        self.sent.append((request.get_header("Title"), request.get_header("Priority")))
        return mock.MagicMock(__enter__=lambda s: mock.Mock(status=200), __exit__=lambda *a: False)

    def test_a_registered_key_is_sent_with_the_registrys_severity(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertTrue(alerts.push("run_failed", "V7 run failed", "lane x", "topic", self.post))
            self.assertTrue(alerts.push("attention_new", "LIFE OS Attention", "2 new items", "topic", self.post))
        self.assertEqual(self.sent, [("V7 run failed", "high"), ("LIFE OS Attention", "low")])          # PAGE is high priority, INFO low

    def test_an_unlisted_key_is_refused_and_nothing_is_sent(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(alerts.push("not_listed", "t", "b", "topic", self.post))
        self.assertEqual(self.sent, [])

    def test_the_mute_and_hibernation_still_apply(self):
        with mock.patch.dict("os.environ", {"ALERTS_MUTED_UNTIL": "2999-01-01T00:00:00Z"}):
            self.assertFalse(alerts.push("run_failed", "t", "b", "topic", self.post))
        alerts.HIBERNATED = True
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(alerts.push("run_failed", "t", "b", "topic", self.post))
        self.assertEqual(self.sent, [])

    def test_the_registry_overrides_the_severity_a_call_site_typed(self):
        item = alerts.Alert("quiet_24h", alerts.PAGE, "Nothing published", "", "")
        self.assertEqual(alerts.render("open", item)[2], "info")

    def test_the_command_line_needs_a_registered_key_and_ignores_a_typed_severity(self):
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"NTFY_TOPIC": "t"}), redirect_stdout(out):
            self.assertEqual(alerts.main(["push", "--key", "no_such_alert", "--title", "x"]), 2)
        self.assertEqual(json.loads(out.getvalue()), {"push": "unregistered_key"})
        with self.assertRaises(SystemExit):
            alerts.main(["push", "--title", "x"])                                           # --key is required


if __name__ == "__main__":
    unittest.main()
