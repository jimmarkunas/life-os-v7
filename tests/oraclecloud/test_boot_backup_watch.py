"""no free boot-volume backup slot on Oracle raises one quiet phone alert, through the gateway"""
import contextlib
import datetime as dt
import io
import unittest

from lifeos.oraclecloud import boot_backup_watch as bw
from lifeos.platform import alert_registry, alerts, codes
from tests.oraclecloud.test_backup_watch import Store

NOW = dt.datetime(2026, 11, 17, 22, 0)  # naive UTC, as the store keeps it
FULL = {"used": 5, "available": 0}


class Decide(unittest.TestCase):
    def test_no_free_slot_opens_one_quiet_alert_naming_the_count(self):
        state, plan = bw.decide(None, FULL, NOW)
        (kind, alert), = plan.notify
        self.assertEqual((kind, alert.key, alert.severity, state["alert_open"]), ("open", bw.KEY, alerts.INFO, True))
        self.assertEqual(alert.title, "Oracle boot-volume backups are full: 5 of 5 in use")

    def test_a_free_slot_stays_quiet(self):
        for reading in ({"used": 4, "available": 1}, {"used": 0, "available": 5}, {"used": 3, "available": 2}):
            state, plan = bw.decide(None, reading, NOW)
            self.assertEqual((state["alert_open"], plan.notify), (False, []), reading)

    def test_an_open_alert_closes_silently_when_a_slot_frees_up(self):
        state, plan = bw.decide({"alert_open": True, "last_notified": NOW}, {"used": 4, "available": 1}, NOW)
        self.assertFalse(state["alert_open"])
        self.assertEqual(plan.notify, [])

    def test_an_unreadable_reading_is_unknown_and_changes_nothing(self):
        self.assertIsNone(bw.healthy(None))
        self.assertIsNone(bw.decide({"alert_open": True, "last_notified": NOW}, None, NOW))  # neither opens nor resolves

    def test_negative_control_the_line_is_zero_free_not_a_fixed_count(self):
        self.assertTrue(bw.healthy({"used": 99, "available": 1}))
        self.assertFalse(bw.healthy({"used": 100, "available": 0}))


class Record(unittest.TestCase):
    def go(self, reading, store, send):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bw.record(reading, {"NTFY_TOPIC": "t"}, NOW.replace(tzinfo=dt.timezone.utc), send=send, connect=store)
        return out.getvalue()

    def test_the_push_goes_out_once_through_the_gateway_and_is_remembered(self):
        store, sent = Store(), []
        send = lambda topic, title, body, severity: sent.append((title, severity)) or True
        self.go(FULL, store, send)
        self.go(FULL, store, send)
        self.assertEqual(sent, [("Oracle boot-volume backups are full: 5 of 5 in use", alerts.INFO)])
        self.assertEqual(store.row, (1, NOW))

    def test_a_broken_store_never_raises_and_an_unknown_reading_touches_no_store(self):
        self.assertIn("state_unavailable", self.go(FULL, Store(broken=True), lambda *a: True))
        self.assertIn("unknown", self.go(None, Store(broken=True), lambda *a: True))

    def test_the_alert_and_its_code_are_registered_quiet_and_phone_only(self):
        spec = alert_registry.lookup(bw.KEY)
        self.assertEqual((spec.severity, spec.channels), (alerts.INFO, ("ntfy",)))
        self.assertIn(bw.CODE, codes.CODES)


if __name__ == "__main__":
    unittest.main()
