"""LIFE-531: more Codex workers than agents on Oracle raises one quiet phone alert, through the gateway; an unreadable reading changes nothing."""
import contextlib
import datetime as dt
import io
import unittest

from lifeos.oraclecloud import worker_watch as ww
from lifeos.platform import alert_registry, alerts, codes
from tests.oraclecloud.test_backup_watch import Store

NOW = dt.datetime(2026, 11, 10, 18, 0)                      # naive UTC, as the store keeps it
FINE = {"count": 5, "agents": 5}
LEAK = {"count": 6, "agents": 5}


class Decide(unittest.TestCase):
    def test_six_workers_for_five_agents_opens_one_quiet_alert(self):
        state, plan = ww.decide(None, LEAK, NOW)
        (kind, alert), = plan.notify
        self.assertEqual((kind, alert.key, alert.severity, state["alert_open"]), ("open", ww.KEY, alerts.INFO, True))
        self.assertEqual(alert.title, "OpenClaw has 6 Codex workers for 5 agents")

    def test_five_for_five_and_fewer_stay_quiet(self):
        for reading in (FINE, {"count": 0, "agents": 5}, {"count": 4, "agents": 5}):
            state, plan = ww.decide(None, reading, NOW)
            self.assertEqual((state["alert_open"], plan.notify), (False, []), reading)

    def test_an_unreadable_reading_is_unknown_and_changes_nothing(self):
        self.assertIsNone(ww.healthy(None))
        self.assertIsNone(ww.decide({"alert_open": True, "last_notified": NOW}, None, NOW))          # neither opens nor resolves

    def test_negative_control_the_threshold_is_the_agent_count_not_a_fixed_number(self):
        self.assertTrue(ww.healthy({"count": 9, "agents": 9}))
        self.assertFalse(ww.healthy({"count": 9, "agents": 8}))
        self.assertFalse(ww.healthy({"count": 1, "agents": 0}))

    def test_it_reminds_once_a_day_and_closes_quietly_when_the_count_is_back(self):
        open_state = {"alert_open": True, "last_notified": NOW - dt.timedelta(hours=23)}
        self.assertEqual(ww.decide(open_state, LEAK, NOW)[1].notify, [])
        open_state["last_notified"] = NOW - dt.timedelta(hours=24)
        self.assertEqual(ww.decide(open_state, LEAK, NOW)[1].notify[0][0], "remind")
        state, plan = ww.decide(open_state, FINE, NOW)
        self.assertEqual((state["alert_open"], plan.resolved, plan.notify), (False, [ww.KEY], []))


class Record(unittest.TestCase):
    def go(self, reading, store, send):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ww.record(reading, {"NTFY_TOPIC": "t"}, NOW.replace(tzinfo=dt.timezone.utc), send=send, connect=store)
        return out.getvalue()

    def test_the_push_goes_out_once_through_the_gateway_and_is_remembered(self):
        store, sent = Store(), []
        send = lambda topic, title, body, severity: sent.append((title, severity)) or True
        self.go(LEAK, store, send)
        self.go(LEAK, store, send)
        self.assertEqual(sent, [("OpenClaw has 6 Codex workers for 5 agents", alerts.INFO)])
        self.assertEqual(store.row, (1, NOW))

    def test_a_healthy_count_sends_nothing(self):
        sent = []
        self.go(FINE, Store(), lambda *a: sent.append(a) or True)
        self.assertEqual(sent, [])

    def test_a_refused_push_is_retried_and_a_broken_store_never_raises(self):
        store = Store()
        self.go(LEAK, store, lambda *a: False)
        self.assertEqual(store.row, (1, None))
        self.assertIn("state_unavailable", self.go(LEAK, Store(broken=True), lambda *a: True))
        self.assertIn("unknown", self.go(None, Store(broken=True), lambda *a: True))

    def test_the_alert_and_its_code_are_registered_quiet_and_phone_only(self):
        spec = alert_registry.lookup(ww.KEY)
        self.assertEqual((spec.severity, spec.channels), (alerts.INFO, ("ntfy",)))
        self.assertIn(ww.CODE, codes.CODES)


if __name__ == "__main__":
    unittest.main()
