"""LIFE-73: the one alert gateway (lifeos.platform.alerts.ntfy) mutes, caps and pauses; and nothing else may post to ntfy or send to Telegram or WhatsApp."""
import contextlib
import datetime as dt
import io
import json
import pathlib
import re
import unittest
from unittest import mock

from lifeos.platform import alerts

ROOT = pathlib.Path(__file__).resolve().parents[2]
UTC = dt.timezone.utc


class Post:
    """A fake ntfy that records every push."""

    def __init__(self):
        self.pushes = []

    def __call__(self, request, timeout=None):
        self.pushes.append((request.get_header("Title"), request.data.decode()))
        return contextlib.nullcontext(mock.Mock(status=200))


class GatewayCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on"})
        patcher.start()
        self.addCleanup(patcher.stop)
        alerts.reset_flood()
        self.addCleanup(alerts.reset_flood)
        self.post = Post()

    def send(self, n=1, severity=alerts.PAGE):
        return [alerts.ntfy("topic", f"T{i}", f"B{i}", severity, post=self.post) for i in range(n)]


class Mute(GatewayCase):
    NOW = dt.datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

    def test_unset_empty_past_and_unreadable_values_do_not_mute(self):
        for value in (None, "", "   ", "2001-01-01T00:00:00Z", "banana", "2026-13-45"):
            with self.subTest(value=value):
                self.assertFalse(alerts.muted({"ALERTS_MUTED_UNTIL": value} if value is not None else {}, self.NOW))

    def test_a_future_time_mutes_and_a_time_with_no_zone_is_central(self):
        self.assertTrue(alerts.muted({"ALERTS_MUTED_UNTIL": "2026-10-26T13:00:00Z"}, self.NOW))
        self.assertTrue(alerts.muted({"ALERTS_MUTED_UNTIL": "2026-10-26T08:00"}, self.NOW))        # 8 AM Central = 13:00Z, still ahead of 12:00Z
        self.assertFalse(alerts.muted({"ALERTS_MUTED_UNTIL": "2026-10-26T07:00"}, self.NOW))       # 7 AM Central = 12:00Z, reached

    def test_the_mute_ends_on_time(self):
        env = {"ALERTS_MUTED_UNTIL": "2026-10-26T13:00:00Z"}
        self.assertTrue(alerts.muted(env, self.NOW))
        self.assertTrue(alerts.muted(env, dt.datetime(2026, 10, 26, 12, 59, 59, tzinfo=UTC)))
        self.assertFalse(alerts.muted(env, dt.datetime(2026, 10, 26, 13, 0, 0, tzinfo=UTC)))

    def test_a_muted_push_posts_nothing(self):
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on", "ALERTS_MUTED_UNTIL": "2999-01-01T00:00:00Z"}):
            self.assertEqual(self.send(3), [False, False, False])
        self.assertEqual(self.post.pushes, [])

    def test_a_muted_command_line_push_is_not_a_failed_step(self):
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on", "ALERTS_MUTED_UNTIL": "2999-01-01T00:00:00Z", "NTFY_TOPIC": "t"}), contextlib.redirect_stdout(out):
            self.assertEqual(alerts.main(["push", "--key", "run_failed", "--title", "x"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"push": "muted"})

    def test_an_unset_mute_lets_pushes_through(self):
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on"}, clear=True):
            self.assertEqual(self.send(2), [True, True])
        self.assertEqual(len(self.post.pushes), 2)


class FloodBreaker(GatewayCase):
    def test_up_to_the_cap_every_push_goes_through(self):
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on"}, clear=True):
            self.assertEqual(self.send(alerts.SEND_CAP), [True] * alerts.SEND_CAP)
        self.assertEqual(len(self.post.pushes), alerts.SEND_CAP)

    def test_a_flood_sends_exactly_one_pause_message_and_holds_the_rest(self):
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on"}, clear=True):
            results = self.send(50)
        self.assertEqual(results, [True] * alerts.SEND_CAP + [False] * (50 - alerts.SEND_CAP))
        titles = [title for title, _ in self.post.pushes]
        self.assertEqual(len(titles), alerts.SEND_CAP + 1)                                  # the cap, plus ONE notice
        self.assertEqual(titles.count("Alerts paused: flood"), 1)
        self.assertEqual(titles[-1], "Alerts paused: flood")

    def test_the_pause_notice_names_no_alert_text(self):
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on"}, clear=True):
            self.send(alerts.SEND_CAP + 1)
        _, body = self.post.pushes[-1]
        self.assertNotRegex(body, r"T\d|B\d")

    def test_a_new_process_starts_at_zero(self):
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on"}, clear=True):
            self.send(20)
            alerts.reset_flood()
            self.assertEqual(self.send(2), [True, True])

    def test_alerts_off_sends_nothing_and_counts_nothing(self):
        with mock.patch.dict("os.environ", {}, clear=True):                                    # no ALERTS_ENABLED: off
            self.assertEqual(self.send(20), [False] * 20)
        self.assertEqual(self.post.pushes, [])
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on"}, clear=True):
            self.assertEqual(self.send(1), [True])

    def test_a_muted_push_does_not_use_up_the_cap(self):
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on", "ALERTS_MUTED_UNTIL": "2999-01-01T00:00:00Z"}):
            self.send(20)
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on"}, clear=True):
            self.assertEqual(self.send(alerts.SEND_CAP), [True] * alerts.SEND_CAP)


RELAY = "lifeos/whatsapp/oracle/forward_alerts.py"
GATEWAY = "lifeos/platform/alerts.py"


def ntfy_offenders(sources):
    """Files (other than the gateway and the relay that reads it) that name the ntfy host."""
    return sorted(name for name, text in sources.items() if "ntfy.sh" in text and name not in (GATEWAY, RELAY))


def openclaw_offenders(sources):
    """Files (other than the relay) that run `openclaw message send`."""
    return sorted(name for name, text in sources.items() if name != RELAY and re.search(r'"message",\s*"send"', text))


class OnlyTheGatewaySends(unittest.TestCase):
    """No code outside the gateway posts to ntfy, and no code outside the relay sends to Telegram or WhatsApp."""

    @staticmethod
    def sources():
        return {str(path.relative_to(ROOT)): path.read_text() for path in sorted((ROOT / "lifeos").rglob("*.py"))}

    def test_only_alerts_py_names_the_ntfy_host_and_only_the_relay_reads_it(self):
        self.assertEqual(ntfy_offenders(self.sources()), [], "route the push through lifeos.platform.alerts.ntfy")

    def test_no_telegram_or_whatsapp_api_is_called_directly(self):
        for name, text in self.sources().items():
            with self.subTest(file=name):
                self.assertNotIn("api.telegram.org", text)

    def test_only_the_relay_runs_openclaw_message_send(self):
        self.assertEqual(openclaw_offenders(self.sources()), [])

    def test_the_checks_turn_red_for_a_module_that_sends_on_its_own(self):
        sources = dict(self.sources())
        sources["lifeos/evil/stage.py"] = 'urllib.request.Request("https://ntfy.sh/" + topic, data=b"x", method="POST")\n'
        sources["lifeos/evil/relay.py"] = 'run(["openclaw", "message", "send", "--target", to])\n'
        self.assertEqual(ntfy_offenders(sources), ["lifeos/evil/stage.py"])
        self.assertEqual(openclaw_offenders(sources), ["lifeos/evil/relay.py"])


if __name__ == "__main__":
    unittest.main()
