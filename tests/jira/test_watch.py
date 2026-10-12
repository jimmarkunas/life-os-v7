"""D181: a scheduled Jira rollover that keeps failing reaches the phone once it has not worked for three hours, then reminds, then says when it recovers."""
import contextlib
import io
import unittest
from datetime import datetime, timedelta, timezone

from lifeos.jira import watch
from lifeos.platform import alerts

T0 = datetime(2026, 10, 12, 13, 0)                          # naive UTC, as stored


class FakeDb:
    """The one v7_jira_rollover row in memory, behind the few statements the module runs."""
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


def wrapped(db, pushes, outcome, live=True, now=T0):
    environ = {"NTFY_TOPIC": "topic-example", "JIRA_ROLLOVER_OUTCOME": outcome}
    with contextlib.redirect_stdout(io.StringIO()):
        return watch.run(1, live, environ=environ, now=now.replace(tzinfo=timezone.utc), send=pushes, connect=lambda: db)


class DecideTests(unittest.TestCase):
    def test_the_first_failure_is_not_an_incident_and_starts_the_clock(self):
        state, plan = watch.decide(None, False, T0)
        self.assertEqual((state["last_ok"], state["alert_open"], plan.notify), (T0, False, []))

    def test_it_pages_once_after_three_hours_then_reminds_every_six(self):
        state = {"last_ok": T0, "alert_open": False, "last_notified": None}
        state, plan = watch.decide(state, False, T0 + timedelta(hours=2))
        self.assertEqual(plan.notify, [])                                            # two hours is a blip
        state, plan = watch.decide(state, False, T0 + timedelta(hours=3))
        self.assertEqual([k for k, _ in plan.notify], ["open"])
        self.assertTrue(state["alert_open"])
        state["last_notified"] = T0 + timedelta(hours=3)
        _, plan = watch.decide(state, False, T0 + timedelta(hours=8))
        self.assertEqual(plan.notify, [])                                            # not yet: reminders are 6 hours apart
        _, plan = watch.decide(state, False, T0 + timedelta(hours=9))
        self.assertEqual([k for k, _ in plan.notify], ["remind"])

    def test_a_success_closes_an_open_alert_and_says_so_once(self):
        state = {"last_ok": T0, "alert_open": True, "last_notified": T0 + timedelta(hours=3)}
        state, plan = watch.decide(state, True, T0 + timedelta(hours=5))
        self.assertEqual(([k for k, _ in plan.notify], state["alert_open"], state["last_ok"]), (["resolved"], False, T0 + timedelta(hours=5)))
        _, plan = watch.decide(state, True, T0 + timedelta(hours=6))
        self.assertEqual(plan.notify, [])

    def test_a_blip_followed_by_a_success_never_pages(self):
        state, plan = watch.decide({"last_ok": T0, "alert_open": False, "last_notified": None}, False, T0 + timedelta(hours=1))
        self.assertEqual(plan.notify, [])
        state, plan = watch.decide(state, True, T0 + timedelta(hours=2))
        self.assertEqual((plan.notify, state["alert_open"]), ([], False))


class RunTests(unittest.TestCase):
    def stuck_row(self, hours=4):
        return (T0 - timedelta(hours=hours), 0, None)

    def test_a_failing_rollover_pages_the_phone_with_counts_only_then_does_not_repeat(self):
        db, pushes = FakeDb(self.stuck_row()), Pushes()
        out = wrapped(db, pushes, "failure")
        (topic, title, body, severity), = pushes.sent
        self.assertEqual((topic, severity, title), ("topic-example", alerts.PAGE, "Jira sprint rollover has not worked for 4 hours"))
        self.assertEqual(body, "A board kept its old sprint. Next: open the domains run (jira job, rollover step) and look for the JIRA_ code")   # fixed words: no board, project or issue name
        self.assertEqual((out["recorded"], out["alert_open"], out["sent"]), (True, True, 1))
        wrapped(db, pushes, "failure", now=T0 + timedelta(hours=1))
        self.assertEqual(len(pushes.sent), 1)                                        # still failing, reminder not due
        self.assertEqual(db.row[1], 1)

    def test_a_push_the_phone_service_refused_is_retried_next_run(self):
        db, pushes = FakeDb(self.stuck_row()), Pushes(accept=False)
        wrapped(db, pushes, "failure")
        self.assertEqual((db.row[1], db.row[2]), (1, None))                          # open, never notified
        pushes.accept = True
        wrapped(db, pushes, "failure", now=T0 + timedelta(hours=1))
        self.assertEqual(len(pushes.sent), 2)
        self.assertIsNotNone(db.row[2])

    def test_a_success_closes_the_alert_and_resets_the_clock(self):
        db, pushes = FakeDb((T0 - timedelta(hours=5), 1, T0 - timedelta(hours=2))), Pushes()
        wrapped(db, pushes, "success")
        self.assertEqual([p[1] for p in pushes.sent], ["Resolved: jira_rollover_stuck"])
        self.assertEqual((db.row[0], db.row[1]), (T0, 0))

    def test_the_first_ever_failure_records_the_start_and_pushes_nothing(self):
        db, pushes = FakeDb(), Pushes()
        wrapped(db, pushes, "failure")
        self.assertEqual((pushes.sent, db.row[0], db.row[1]), ([], T0, 0))

    def test_a_dry_run_or_a_skipped_step_records_nothing(self):
        for live, outcome in ((False, "failure"), (True, "skipped"), (True, ""), (True, "cancelled")):
            db, pushes = FakeDb(self.stuck_row()), Pushes()
            self.assertEqual(wrapped(db, pushes, outcome, live=live), {"recorded": False})
            self.assertEqual((db.sql, pushes.sent), ([], []))

    def test_a_store_that_cannot_be_reached_never_raises(self):
        pushes = Pushes()
        self.assertEqual(wrapped(FakeDb(broken=True), pushes, "failure"), {"recorded": False, "state": "unavailable"})
        self.assertEqual(pushes.sent, [])

    def test_the_stage_is_registered(self):
        from lifeos import run as entry
        self.assertEqual(entry.STAGES["jira-rollover-watch"].target, ("lifeos.jira.watch", "run"))


if __name__ == "__main__":
    unittest.main()
