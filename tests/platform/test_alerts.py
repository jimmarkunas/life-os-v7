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
        a = alerts.Alert("x", alerts.PAGE, "Token problem", "Bearer abcdef123456 failed for someone@example.test", "renew it")
        title, body, severity = alerts.render("open", a)
        self.assertNotIn("abcdef123456", body)
        self.assertNotIn("someone@example.test", body)
        self.assertIn("Next: renew it", body)
        self.assertEqual(severity, alerts.PAGE)

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


if __name__ == "__main__":
    unittest.main()
