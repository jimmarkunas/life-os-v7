import contextlib
import datetime as dt
import io
import unittest

from lifeos.oraclecloud import backup_watch as bw
from lifeos.platform import alert_registry, alerts

NOW = dt.datetime(2026, 10, 29, 8, 0)                       # naive UTC, as the store keeps it
GOOD = {"ok": True, "code": "ok", "age_h": 5}
FAILED = {"ok": False, "code": "backup_drive_http_403", "age_h": 1}
MISSED = {"ok": True, "code": "ok", "age_h": 40}
NEVER = {"ok": False, "code": "backup_never_ran", "age_h": None}


class Store:
    """One private row, the way v7_offsite_backup holds it."""

    def __init__(self, row=None, broken=False):
        self.row, self.broken = row, broken

    def __call__(self):
        if self.broken:
            raise OSError("store down")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self

    def execute(self, sql, args=()):
        if sql.startswith("INSERT"):
            self.row = (args[1], args[2])

    def fetchone(self):
        return self.row


class Decide(unittest.TestCase):
    def test_a_healthy_or_unknown_reading_sends_nothing(self):
        state, plan = bw.decide(None, GOOD, NOW)
        self.assertEqual((state["alert_open"], plan.notify), (False, []))
        self.assertIsNone(bw.decide({"alert_open": True, "last_notified": NOW}, None, NOW))           # Oracle unreachable: neither opens nor resolves

    def test_a_failed_night_opens_one_quiet_alert_that_names_the_failure(self):
        state, plan = bw.decide(None, FAILED, NOW)
        (kind, alert), = plan.notify
        self.assertEqual((kind, alert.key, alert.severity, state["alert_open"]), ("open", bw.KEY, alerts.INFO, True))
        self.assertEqual(alert.title, "Off-site backup failed (backup_drive_http_403)")

    def test_a_missed_night_and_a_backup_that_never_ran_are_said_plainly(self):
        self.assertEqual(bw.decide(None, MISSED, NOW)[1].notify[0][1].title, "Off-site backup missed: last good run 40 hours ago")
        self.assertEqual(bw.decide(None, NEVER, NOW)[1].notify[0][1].title, "Off-site backup has never run")
        self.assertTrue(bw.healthy({"ok": True, "code": "ok", "age_h": 35}))
        self.assertFalse(bw.healthy({"ok": True, "code": "ok", "age_h": 36}))

    def test_it_reminds_once_a_day_while_broken_and_closes_quietly_when_fixed(self):
        open_state = {"alert_open": True, "last_notified": NOW - dt.timedelta(hours=23)}
        self.assertEqual(bw.decide(open_state, FAILED, NOW)[1].notify, [])
        open_state["last_notified"] = NOW - dt.timedelta(hours=24)
        self.assertEqual(bw.decide(open_state, FAILED, NOW)[1].notify[0][0], "remind")
        state, plan = bw.decide(open_state, GOOD, NOW)
        self.assertEqual((state["alert_open"], plan.resolved, plan.notify), (False, [bw.KEY], []))      # INFO resolves without a push (platform alerts, D53)


class Record(unittest.TestCase):
    def go(self, reading, store, send):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bw.record(reading, {"NTFY_TOPIC": "t"}, NOW.replace(tzinfo=dt.timezone.utc), send=send, connect=store)
        return out.getvalue()

    def test_a_push_is_sent_once_and_remembered(self):
        store, sent = Store(), []
        send = lambda topic, title, body, severity: sent.append((title, severity)) or True
        self.go(FAILED, store, send)
        self.go(FAILED, store, send)
        self.assertEqual(sent, [("Off-site backup failed (backup_drive_http_403)", alerts.INFO)])
        self.assertEqual(store.row, (1, NOW))

    def test_a_push_the_phone_service_refused_is_retried_next_tick(self):
        store = Store()
        self.go(FAILED, store, lambda *a: False)
        self.assertEqual(store.row, (1, None))

    def test_it_never_raises_and_an_unknown_reading_touches_no_store(self):
        self.assertIn("state_unavailable", self.go(FAILED, Store(broken=True), lambda *a: True))
        self.assertIn("unknown", self.go(None, Store(broken=True), lambda *a: True))

    def test_the_alert_is_registered_quiet_and_phone_only(self):
        spec = alert_registry.lookup(bw.KEY)
        self.assertEqual((spec.severity, spec.channels), (alerts.INFO, ("ntfy",)))


if __name__ == "__main__":
    unittest.main()
