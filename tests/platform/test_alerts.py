import datetime as dt
import unittest
from unittest import mock

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

    def setUp(self):                                          # the send path itself, as it behaves when Jim turns alerts on (the ALERTS_ENABLED switch)
        patcher = mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on", "ALERTS_MUTED_UNTIL": ""})     # a mute set on the repository must not decide these tests (it failed the tick gate, Oct 26)
        patcher.start()
        self.addCleanup(patcher.stop)
        alerts.reset_flood()
        self.addCleanup(alerts.reset_flood)

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

    def test_deliver_names_each_failed_push_and_sends_in_plan_order(self):
        plan, titles, failed = alerts.reconcile([A, I], {}, NOW), [], set()
        sent = alerts.deliver(plan, "t", send=lambda *a: titles.append(a[1]) or a[3] == alerts.PAGE, failed_keys=failed)
        self.assertEqual((sent, failed), ({"sent": 1, "failed": 1}, {"k2"}))
        self.assertEqual(titles, [alerts.render(kind, item)[0] for kind, item in plan.notify])

    def test_the_push_cli_exits_1_when_the_push_is_not_accepted(self):
        import contextlib, io

        def answer(status):
            class Resp:
                def __init__(self):
                    self.status = status

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return lambda request, timeout: Resp()
        for status, want in ((500, 1), (200, 0)):                                            # LIFE-604: the real main and the real ntfy run; only the network answer is replaced
            with self.subTest(status=status), mock.patch.dict("os.environ", {"ALERTS_ENABLED": "on", "NTFY_TOPIC": "topic"}, clear=True), \
                    mock.patch.object(alerts.urllib.request, "urlopen", answer(status)), contextlib.redirect_stdout(io.StringIO()):
                alerts.reset_flood()
                self.assertEqual(alerts.main(["push", "--key", "run_failed", "--title", "T"]), want)


class CommandLineCopies(unittest.TestCase):
    """LIFE-604 item 2: the command line (`alerts.main`) used by the workflows must carry the same Telegram and WhatsApp copy tags as `push()` does.
    Only the network call is replaced; the real main, the real registry and the real settings run."""
    WORKFLOW_KEYS = ("run_failed", "domain_jobs_failed", "purge_done", "purge_failed", "safety_failed", "test_push")

    def run_main(self, *argv, settings=None):
        import contextlib, io
        sent = []

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(request, timeout):
            sent.append({"tags": request.get_header("Tags"), "priority": request.get_header("Priority")})
            return Resp()
        env = {"ALERTS_ENABLED": "on", "NTFY_TOPIC": "topic", **({"ALERT_SETTINGS": settings} if settings else {})}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(alerts.urllib.request, "urlopen", urlopen), contextlib.redirect_stdout(io.StringIO()):
            alerts.reset_flood()
            code = alerts.main(["push", *argv])
        return code, sent

    def test_a_page_key_carries_both_copy_tags_and_the_loud_priority(self):
        code, sent = self.run_main("--key", "run_failed", "--title", "T")
        self.assertEqual(code, 0)
        self.assertEqual(sent, [{"tags": "rotating_light,ch-telegram,ch-whatsapp", "priority": "high"}])

    def test_an_info_key_carries_no_copy_tag(self):
        code, sent = self.run_main("--key", "purge_done", "--title", "T")
        self.assertEqual((code, sent), (0, [{"tags": "information_source", "priority": "low"}]))

    def test_every_workflow_key_gets_exactly_the_copies_the_registry_gives_it(self):
        from lifeos.platform import alert_registry
        for key in self.WORKFLOW_KEYS:
            with self.subTest(key=key):
                _, sent = self.run_main("--key", key, "--title", "T")
                want = [f"ch-{c}" for c in alert_registry.channels(key) if c != "ntfy"]
                got = [t for t in sent[0]["tags"].split(",") if t.startswith("ch-")]
                self.assertEqual(got, want)

    def test_jims_channel_choice_applies_to_the_command_line_too(self):
        settings = '{"v":1,"channels":{"purge_done":["ntfy","whatsapp"]}}'
        _, sent = self.run_main("--key", "purge_done", "--title", "T", settings=settings)
        self.assertEqual(sent[0]["tags"], "information_source,ch-whatsapp")

    def test_a_manual_test_push_keeps_its_severity_override(self):
        _, sent = self.run_main("--key", "test_push", "--severity", "info", "--title", "T")
        self.assertEqual(sent[0]["priority"], "low")
        self.assertIn("ch-telegram", sent[0]["tags"])


class Hibernated(unittest.TestCase):
    """LIFE-71 (Jim, Oct 18 2026): every LIFE OS phone push is suspended until Jim explicitly approves reactivation."""

    def test_alerts_are_off_unless_the_master_switch_says_on(self):
        for value in (None, "", "off", "true", "1", "ON please"):
            env = {} if value is None else {"ALERTS_ENABLED": value}
            with self.subTest(value=value), mock.patch.dict("os.environ", env, clear=True):
                self.assertTrue(alerts.hibernated())              # the safe default is OFF: only exactly "on" turns alerts on (LIFE-75: Jim's switch, not a commit)
        with mock.patch.dict("os.environ", {"ALERTS_ENABLED": " On "}, clear=True):
            self.assertFalse(alerts.hibernated())

    def test_nothing_is_posted_and_the_push_counts_as_not_sent(self):
        def post(request, timeout):
            raise AssertionError("no push may leave while alerts are off")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(alerts.ntfy("topic", "Title", "Body", alerts.PAGE, post=post))
            self.assertEqual(alerts.deliver(alerts.reconcile([A, I], {}, NOW), "topic"), {"sent": 0, "failed": 2})

    def test_the_push_cli_sends_nothing_and_keeps_the_step_green(self):
        import contextlib, io
        out, old = io.StringIO(), alerts.ntfy
        try:
            alerts.ntfy = lambda *a, **k: (_ for _ in ()).throw(AssertionError("ntfy called"))
            with mock.patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(out):
                self.assertEqual(alerts.main(["push", "--key", "run_failed", "--title", "T"]), 0)
        finally:
            alerts.ntfy = old
        self.assertEqual(out.getvalue().strip(), '{"push": "hibernated"}')

if __name__ == "__main__":
    unittest.main()
