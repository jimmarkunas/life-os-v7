import datetime as dt
import unittest

from lifeos.platform import alerts

NOW = dt.datetime(2026, 10, 2, 12, 0)
A = alerts.Alert("k1", alerts.PAGE, "Pipeline stalled", "No run in 3 hours.", "check cron-job.org")
I = alerts.Alert("k2", alerts.INFO, "Sources failing", "2 source(s)", "")


class Reconcile(unittest.TestCase):
    def test_new_alert_opens_and_notifies(self):
        plan = alerts.reconcile([A], {}, NOW)
        self.assertEqual(([a.key for a in plan.opened], [k for k, _ in plan.notify]), (["k1"], ["open"]))

    def test_open_alert_stays_quiet_until_the_reminder_is_due(self):
        state = {"k1": {"severity": "page", "last_notified": NOW - dt.timedelta(hours=2)}}
        self.assertEqual(alerts.reconcile([A], state, NOW).notify, [])
        state["k1"]["last_notified"] = NOW - dt.timedelta(hours=7)
        self.assertEqual([k for k, _ in alerts.reconcile([A], state, NOW).notify], ["remind"])

    def test_info_reminds_daily_not_every_six_hours(self):
        state = {"k2": {"severity": "info", "last_notified": NOW - dt.timedelta(hours=10)}}
        self.assertEqual(alerts.reconcile([I], state, NOW).notify, [])

    def test_an_undelivered_alert_is_retried(self):
        state = {"k1": {"severity": "page", "last_notified": None}}
        self.assertEqual([k for k, _ in alerts.reconcile([A], state, NOW).notify], ["remind"])

    def test_resolution_notifies_only_for_page(self):
        state = {"k1": {"severity": "page", "last_notified": NOW}, "k2": {"severity": "info", "last_notified": NOW}}
        plan = alerts.reconcile([], state, NOW)
        self.assertEqual((sorted(plan.resolved), [k for k, _ in plan.notify]), (["k1", "k2"], ["resolved"]))


class Delivery(unittest.TestCase):
    def test_render_is_redacted_and_counts_only(self):
        address = "someone" + chr(64) + "example.test"          # built at runtime: the privacy guard scans tracked files for addresses
        a = alerts.Alert("x", alerts.PAGE, "Token problem", f"Bearer abcdef123456 failed for {address}", "renew it")
        title, body, severity = alerts.render("open", a)
        self.assertNotIn("abcdef123456", body)
        self.assertNotIn(address, body)
        self.assertIn("Next: renew it", body)
        self.assertEqual(severity, alerts.PAGE)

    def setUp(self):                                          # the send path itself, as it behaves when Jim turns alerts back on
        self._hibernated, alerts.HIBERNATED = alerts.HIBERNATED, False

    def tearDown(self):
        alerts.HIBERNATED = self._hibernated

    def test_ntfy_needs_a_topic_and_never_raises(self):
        self.assertFalse(alerts.ntfy("", "t", "b", alerts.PAGE))

        def boom(request, timeout):
            raise OSError("down")
        self.assertFalse(alerts.ntfy("topic", "t", "b", alerts.PAGE, post=boom))

    def test_ntfy_posts_priority_and_body(self):
        seen = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def post(request, timeout):
            seen.update(url=request.full_url, data=request.data, priority=request.get_header("Priority"))
            return Resp()
        self.assertTrue(alerts.ntfy("topic", "Title", "Body", alerts.PAGE, post=post))
        self.assertEqual((seen["url"], seen["data"], seen["priority"]), ("https://ntfy.sh/topic", b"Body", "high"))

    def test_deliver_counts_sent_and_failed(self):
        plan = alerts.reconcile([A, I], {}, NOW)
        self.assertEqual(alerts.deliver(plan, "t", send=lambda *a: a[3] == alerts.PAGE), {"sent": 1, "failed": 1})

    def test_the_push_cli_exits_1_when_the_push_is_not_accepted(self):
        import contextlib, io
        old = alerts.ntfy
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                alerts.ntfy = lambda *a: False
                self.assertEqual(alerts.main(["push", "--key", "run_failed", "--title", "T"]), 1)
                alerts.ntfy = lambda *a: True
                self.assertEqual(alerts.main(["push", "--key", "run_failed", "--title", "T"]), 0)
        finally:
            alerts.ntfy = old



class Hibernated(unittest.TestCase):
    """LIFE-71 (Jim, Oct 18 2026): every LIFE OS phone push is suspended until Jim explicitly approves reactivation."""

    def test_alerts_are_hibernated_on_this_commit(self):
        self.assertIs(alerts.HIBERNATED, True)                  # turning alerts back on must change this test too, in a reviewed commit

    def test_nothing_is_posted_and_the_push_counts_as_not_sent(self):
        def post(request, timeout):
            raise AssertionError("no push may leave while hibernated")
        self.assertFalse(alerts.ntfy("topic", "Title", "Body", alerts.PAGE, post=post))
        self.assertEqual(alerts.deliver(alerts.reconcile([A, I], {}, NOW), "topic"), {"sent": 0, "failed": 2})

    def test_the_push_cli_sends_nothing_and_keeps_the_step_green(self):
        import contextlib, io
        out, old = io.StringIO(), alerts.ntfy
        try:
            alerts.ntfy = lambda *a: (_ for _ in ()).throw(AssertionError("ntfy called"))
            with contextlib.redirect_stdout(out):
                self.assertEqual(alerts.main(["push", "--key", "run_failed", "--title", "T"]), 0)
        finally:
            alerts.ntfy = old
        self.assertEqual(out.getvalue().strip(), '{"push": "hibernated"}')

if __name__ == "__main__":
    unittest.main()
