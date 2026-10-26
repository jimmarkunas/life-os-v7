"""LIFE-75: the Alert settings Jim saves (ALERTS_ENABLED, ALERT_SETTINGS) decide whether and where each alert goes."""
import json
import unittest
from unittest import mock

from lifeos.platform import alert_registry as registry, alert_settings as settings, alerts
from lifeos.whatsapp.oracle import forward_alerts  # noqa: F401  (imported by path in tests/whatsapp; here only to prove the module still loads)


def doc(**channels):
    return json.dumps({"v": 1, "channels": channels})


class Switch(unittest.TestCase):
    def test_only_the_word_on_turns_alerts_on(self):
        for value, expected in ((None, False), ("", False), ("off", False), ("true", False), ("1", False), ("on", True), (" ON ", True), ("On", True)):
            env = {} if value is None else {"ALERTS_ENABLED": value}
            self.assertEqual(settings.enabled(env), expected, value)


class Channels(unittest.TestCase):
    def test_the_registry_defaults_apply_when_nothing_is_saved(self):
        self.assertEqual(settings.channels("run_failed", {}), registry.CHANNELS)
        self.assertEqual(settings.channels("quiet_24h", {}), ("ntfy",))
        self.assertEqual(settings.channels("source_stopped_dice", {}), registry.CHANNELS)
        self.assertEqual(settings.channels("not_listed", {}), ())

    def test_a_saved_choice_replaces_the_default_for_that_alert_only(self):
        env = {"ALERT_SETTINGS": doc(quiet_24h=["ntfy", "telegram"], **{"source_stopped_*": ["ntfy"]})}
        self.assertEqual(settings.channels("quiet_24h", env), ("ntfy", "telegram"))
        self.assertEqual(settings.channels("source_stopped_dice", env), ("ntfy",))        # a family entry covers every member
        self.assertEqual(settings.channels("stuck_jobs", env), ("ntfy",))                  # untouched
        self.assertEqual(settings.channels("run_failed", env), registry.CHANNELS)

    def test_a_page_alert_always_keeps_the_phone(self):
        env = {"ALERT_SETTINGS": doc(run_failed=["telegram"], safety_failed=[])}
        self.assertEqual(settings.channels("run_failed", env), ("ntfy", "telegram"))
        self.assertEqual(settings.channels("safety_failed", env), ("ntfy",))

    def test_an_info_alert_can_be_turned_off_everywhere(self):
        self.assertEqual(settings.channels("quiet_24h", {"ALERT_SETTINGS": doc(quiet_24h=[])}), ())

    def test_anything_unreadable_means_the_defaults_and_never_silence(self):
        for raw in ("", "   ", "not json", "[]", "null", json.dumps({"v": 2, "channels": {}}), json.dumps({"v": 1}), json.dumps({"v": 1, "channels": []}),
                    "x" * 9000, json.dumps({"v": 1, "channels": {"run_failed": "ntfy"}}), json.dumps({"v": 1, "channels": {"run_failed": [1, 2]}})):
            with self.subTest(raw=raw[:30]):
                self.assertEqual(settings.channels("run_failed", {"ALERT_SETTINGS": raw}), registry.CHANNELS)
                self.assertEqual(settings.channels("quiet_24h", {"ALERT_SETTINGS": raw}), ("ntfy",))

    def test_unknown_keys_and_channels_are_ignored(self):
        env = {"ALERT_SETTINGS": doc(nonsense=["ntfy"], quiet_24h=["ntfy", "carrier_pigeon", "whatsapp"])}
        self.assertEqual(settings.load(env), {"quiet_24h": ("ntfy", "whatsapp")})

    def test_document_keeps_only_changes_and_round_trips(self):
        chosen = {"run_failed": ["ntfy", "telegram", "whatsapp"], "quiet_24h": ["ntfy", "telegram"], "stuck_jobs": ["ntfy"], "x": ["ntfy"], "safety_failed": ["telegram"]}
        text = settings.document(chosen)
        self.assertEqual(json.loads(text), {"v": 1, "channels": {"quiet_24h": ["ntfy", "telegram"], "safety_failed": ["ntfy", "telegram"]}})
        self.assertEqual(settings.load({"ALERT_SETTINGS": text}), {"quiet_24h": ("ntfy", "telegram"), "safety_failed": ("ntfy", "telegram")})
        self.assertEqual(settings.document({}), "")
        self.assertEqual(settings.document({"quiet_24h": ["ntfy"]}), "")                   # equal to the default: nothing to save


class Post:
    def __init__(self):
        self.sent = []

    def __call__(self, request, timeout=None):
        self.sent.append({"priority": request.get_header("Priority"), "tags": request.get_header("Tags")})
        return mock.MagicMock(__enter__=lambda s: mock.Mock(status=200), __exit__=lambda *a: False)


class TheGatewayFollowsTheSettings(unittest.TestCase):
    def setUp(self):
        alerts.reset_flood()
        self.addCleanup(alerts.reset_flood)
        self.post = Post()

    def push(self, key, **env):
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on", **env}, clear=True):
            return alerts.push(key, "T", "B", "topic", self.post)

    def test_a_page_alert_by_default_is_loud_and_tagged_for_both_copies(self):
        self.assertTrue(self.push("run_failed"))
        self.assertEqual(self.post.sent, [{"priority": "high", "tags": "rotating_light,ch-telegram,ch-whatsapp"}])

    def test_an_info_alert_by_default_is_phone_only_with_no_copy_tags(self):
        self.assertTrue(self.push("quiet_24h"))
        self.assertEqual(self.post.sent, [{"priority": "low", "tags": "information_source"}])

    def test_a_telegram_only_info_alert_goes_out_silent_for_the_relay(self):
        self.assertTrue(self.push("quiet_24h", ALERT_SETTINGS=doc(quiet_24h=["telegram"])))
        self.assertEqual(self.post.sent, [{"priority": "min", "tags": "information_source,ch-telegram"}])           # lowest priority: no sound or banner on the phone

    def test_an_alert_with_every_channel_off_sends_nothing_at_all(self):
        self.assertFalse(self.push("quiet_24h", ALERT_SETTINGS=doc(quiet_24h=[])))
        self.assertEqual(self.post.sent, [])

    def test_the_master_switch_off_sends_nothing_whatever_the_settings(self):
        with mock.patch.dict("os.environ", {"ALERT_SETTINGS": doc(run_failed=["ntfy"])}, clear=True):
            self.assertFalse(alerts.push("run_failed", "T", "B", "topic", self.post))
        self.assertEqual(self.post.sent, [])

    def test_a_plan_with_the_channels_off_is_handled_not_failed(self):
        plan = alerts.reconcile([alerts.Alert("quiet_24h", alerts.INFO, "Quiet", "", "")], {}, __import__("datetime").datetime(2026, 10, 26, 12, 0))
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on", "ALERT_SETTINGS": doc(quiet_24h=[])}, clear=True):
            self.assertEqual(alerts.deliver(plan, "topic"), {"sent": 0, "failed": 0, "skipped": 1})


class WorkflowsPassTheSwitchAndTheSettings(unittest.TestCase):
    def test_every_workflow_that_can_push_receives_the_mute_the_switch_and_the_settings(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        senders = [p for p in sorted(root.glob("*.yml")) if "platform.alerts push" in p.read_text() or "platform.gate" in p.read_text() or "NTFY_TOPIC" in p.read_text()]
        self.assertGreaterEqual(len(senders), 4)
        for path in senders:
            text = path.read_text()
            for name in ("ALERTS_MUTED_UNTIL", "ALERTS_ENABLED", "ALERT_SETTINGS"):
                with self.subTest(workflow=path.name, variable=name):
                    self.assertIn(f"{name}: ${{{{ vars.{name} }}}}", text)


if __name__ == "__main__":
    unittest.main()
