"""D173: a calendar sync that keeps failing reaches the phone, once it has failed for three hours, then reminds, then says when it recovers."""
import contextlib
import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from lifeos.calendar_bridge import sync, watch
from lifeos.calendar_bridge.sync import BridgeError
from lifeos.platform import alerts

T0 = datetime(2026, 10, 12, 3, 0)                          # naive UTC, as stored


class FakeDb:
    """One v7_calendar_sync row in memory, behind the few statements the module runs."""
    def __init__(self, row=None, broken=False):
        self.row, self.broken, self.sql = row, broken, []
        self._last = None

    def __enter__(self):
        if self.broken:
            raise OSError("STORE_SSH_NETWORK")
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        self.sql.append(sql.split()[0])
        if sql.startswith("SELECT"):
            self._last = self.row
        elif sql.startswith("INSERT"):
            self.row = (params[1], params[2], params[3])

    def fetchone(self):
        return self._last


class Pushes:
    def __init__(self, accept=True):
        self.sent, self.accept = [], accept

    def __call__(self, topic, title, body, severity):
        self.sent.append((topic, title, body, severity))
        return self.accept


def wrapped(db, pushes, outcome, live=True, now=T0, environ=None):
    environ = {"NTFY_TOPIC": "topic-example", **(environ or {})}
    effect = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
    with patch.object(sync, "run", **effect), contextlib.redirect_stdout(io.StringIO()):
        return watch.run(1, live, environ=environ, now=now.replace(tzinfo=timezone.utc), send=pushes, connect=lambda: db)


class DecideTests(unittest.TestCase):
    def test_the_first_failure_is_not_an_incident_and_starts_the_clock(self):
        state, plan = watch.decide(None, False, T0, "CALENDAR_MASS_DELETE_GUARD")
        self.assertEqual((state["last_ok"], state["alert_open"], plan.notify), (T0, False, []))

    def test_it_pages_once_after_three_hours_then_reminds_every_six(self):
        state = {"last_ok": T0, "alert_open": False, "last_notified": None}
        state, plan = watch.decide(state, False, T0 + timedelta(hours=2), "X")
        self.assertEqual(plan.notify, [])                                            # two hours is a blip
        state, plan = watch.decide(state, False, T0 + timedelta(hours=3), "X")
        self.assertEqual([k for k, _ in plan.notify], ["open"])
        self.assertTrue(state["alert_open"])
        state["last_notified"] = T0 + timedelta(hours=3)
        _, plan = watch.decide(state, False, T0 + timedelta(hours=8), "X")
        self.assertEqual(plan.notify, [])                                            # not yet: reminders are 6 hours apart
        _, plan = watch.decide(state, False, T0 + timedelta(hours=9), "X")
        self.assertEqual([k for k, _ in plan.notify], ["remind"])

    def test_a_success_closes_an_open_alert_and_says_so_once(self):
        state = {"last_ok": T0, "alert_open": True, "last_notified": T0 + timedelta(hours=3)}
        state, plan = watch.decide(state, True, T0 + timedelta(hours=5))
        self.assertEqual(([k for k, _ in plan.notify], state["alert_open"], state["last_ok"]), (["resolved"], False, T0 + timedelta(hours=5)))
        _, plan = watch.decide(state, True, T0 + timedelta(hours=6))
        self.assertEqual(plan.notify, [])

    def test_a_blip_followed_by_a_success_never_pages(self):
        state, plan = watch.decide({"last_ok": T0, "alert_open": False, "last_notified": None}, False, T0 + timedelta(hours=1), "X")
        self.assertEqual(plan.notify, [])
        state, plan = watch.decide(state, True, T0 + timedelta(hours=2))
        self.assertEqual((plan.notify, state["alert_open"]), ([], False))


class WrapperTests(unittest.TestCase):
    def stuck_row(self, hours=4):
        return (T0 - timedelta(hours=hours), 0, None)

    def test_a_failing_sync_pages_the_phone_with_counts_and_a_fixed_code_then_does_not_repeat(self):
        db, pushes = FakeDb(self.stuck_row()), Pushes()
        with self.assertRaises(BridgeError) as error:
            wrapped(db, pushes, BridgeError("CALENDAR_MASS_DELETE_GUARD"))
        self.assertEqual(str(error.exception), "CALENDAR_MASS_DELETE_GUARD")        # the sync's own error is untouched
        (topic, title, body, severity), = pushes.sent
        self.assertEqual((topic, severity, title), ("topic-example", alerts.PAGE, "Calendar sync has not worked for 4 hours"))
        self.assertIn("CALENDAR_MASS_DELETE_GUARD", body)
        with self.assertRaises(BridgeError):
            wrapped(db, pushes, BridgeError("CALENDAR_MASS_DELETE_GUARD"), now=T0 + timedelta(hours=1))
        self.assertEqual(len(pushes.sent), 1)                                        # still failing, reminder not due
        self.assertEqual(db.row[1], 1)

    def test_a_push_the_phone_service_refused_is_retried_next_run(self):
        db, pushes = FakeDb(self.stuck_row()), Pushes(accept=False)
        with self.assertRaises(BridgeError):
            wrapped(db, pushes, BridgeError("X"))
        self.assertEqual((db.row[1], db.row[2]), (1, None))                          # open, never notified
        pushes.accept = True
        with self.assertRaises(BridgeError):
            wrapped(db, pushes, BridgeError("X"), now=T0 + timedelta(hours=1))
        self.assertEqual(len(pushes.sent), 2)
        self.assertIsNotNone(db.row[2])

    def test_a_success_returns_the_sync_result_unchanged_and_closes_the_alert(self):
        db, pushes = FakeDb((T0 - timedelta(hours=5), 1, T0 - timedelta(hours=2))), Pushes()
        out = wrapped(db, pushes, {"outlook": 13, "same": 13})
        self.assertEqual(out, {"outlook": 13, "same": 13})
        self.assertEqual([p[1] for p in pushes.sent], ["Resolved: calendar_sync_stuck"])
        self.assertEqual((db.row[0], db.row[1]), (T0, 0))

    def test_a_dry_run_touches_no_state_and_pushes_nothing(self):
        db, pushes = FakeDb(self.stuck_row()), Pushes()
        with self.assertRaises(BridgeError):
            wrapped(db, pushes, BridgeError("X"), live=False)
        self.assertEqual((db.sql, pushes.sent), ([], []))

    def test_a_store_that_cannot_be_reached_never_changes_the_sync_result(self):
        pushes = Pushes()
        with self.assertRaises(BridgeError) as error:
            wrapped(FakeDb(broken=True), pushes, BridgeError("CALENDAR_MASS_DELETE_GUARD"))
        self.assertEqual(str(error.exception), "CALENDAR_MASS_DELETE_GUARD")
        self.assertEqual(wrapped(FakeDb(broken=True), pushes, {"ok": 1}), {"ok": 1})
        self.assertEqual(pushes.sent, [])

    def test_an_error_that_is_not_a_bridge_error_is_named_by_its_class_only(self):
        db, pushes = FakeDb(self.stuck_row()), Pushes()
        with self.assertRaises(OSError):
            wrapped(db, pushes, OSError("detail with a private path"))
        self.assertIn("OSError", pushes.sent[0][2])
        self.assertNotIn("private", str(pushes.sent))

    def test_the_calendar_stage_runs_through_the_watch(self):
        from lifeos import run as entry
        self.assertEqual(entry.STAGES["calendar-sync"].target, ("lifeos.calendar_bridge.watch", "run"))


if __name__ == "__main__":
    unittest.main()
