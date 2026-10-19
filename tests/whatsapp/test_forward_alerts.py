import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

PATH = pathlib.Path(__file__).resolve().parents[2] / "lifeos/whatsapp/oracle/forward_alerts.py"
spec = importlib.util.spec_from_file_location("forward_alerts", PATH)
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)


def stream(*events):
    return io.BytesIO(b"".join(json.dumps(e).encode() + b"\n" for e in events))


class Stream:
    def __init__(self, data):
        self.data, self.urls = data, []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        return self.data


class ForwardTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.patches = [mock.patch.object(fa, "STATE", d), mock.patch.object(fa, "LAST", os.path.join(d, "last-id"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_text_carries_the_redacted_alert_with_an_icon_by_priority(self):
        self.assertEqual(fa.text({"title": "2 texts need you", "message": "Open Messages", "priority": 5}), "🚨 2 texts need you\nOpen Messages")
        self.assertEqual(fa.text({"message": "Mail Alerts degraded", "priority": 3}), "🔔 LIFE OS\nMail Alerts degraded")
        self.assertLessEqual(len(fa.text({"title": "x", "message": "y" * 5000})), fa.MAX_TEXT)

    def test_only_message_events_are_forwarded_and_the_last_id_resumes_the_stream(self):
        sent = []
        opener = Stream(stream({"event": "open"}, {"event": "keepalive", "id": "k1"}, {"event": "message", "id": "a1", "title": "One"},
                               {"event": "message", "id": "a2", "title": "Two"}))
        fa.follow("topic", "+15550100001", opener, lambda to, body: sent.append((to, body)) or True)
        self.assertEqual([b for _, b in sent], ["🔔 One", "🔔 Two"])
        self.assertEqual(fa.last_id(), "a2")
        self.assertTrue(opener.urls[0].endswith("?poll=1&since=10m"))
        nxt = Stream(stream())
        fa.follow("topic", "+15550100001", nxt, lambda to, body: True)
        self.assertTrue(nxt.urls[0].endswith("?poll=1&since=a2"))

    def test_a_failed_send_is_not_remembered_so_it_is_retried(self):
        fa.remember("a0")
        fa.follow("topic", "+15550100001", Stream(stream({"event": "message", "id": "a1", "title": "One"})), lambda to, body: False)
        self.assertEqual(fa.last_id(), "a0")

    def test_each_channel_keeps_its_own_position(self):
        one = stream({"event": "message", "id": "a1", "title": "One"})
        fa.follow("topic", "+447700900001", Stream(one), lambda to, body: True, channel="whatsapp")
        fa.follow("topic", "123456789", Stream(stream({"event": "message", "id": "a1", "title": "One"})), lambda to, body: False, channel="telegram")
        self.assertEqual((fa.last_id("whatsapp"), fa.last_id("telegram")), ("a1", None))      # Telegram retries; WhatsApp does not repeat

    def test_the_recipient_must_be_exactly_one_allowlisted_number(self):
        def cfg(allow, channel="whatsapp"):
            path = os.path.join(tempfile.mkdtemp(), "c.json")
            with open(path, "w") as f:
                json.dump({"channels": {channel: {"allowFrom": allow}}}, f)
            return path
        self.assertEqual(fa.recipient(cfg(["123456789"], "telegram"), "telegram"), "123456789")
        self.assertIsNone(fa.recipient(cfg(["123456789", "987654321"], "telegram"), "telegram"))
        self.assertIsNone(fa.recipient(cfg(["@someone"], "telegram"), "telegram"))
        self.assertEqual(fa.recipient(cfg(["+44 7700 900001"])), "+447700900001")
        self.assertIsNone(fa.recipient(cfg(["+15550100001", "+15550100002"])))
        self.assertIsNone(fa.recipient(cfg([])))



def done(code, stdout="", stderr=""):
    import subprocess
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


DELIVERED_EXIT_1 = done(1, json.dumps({"action": "send", "channel": "telegram", "dryRun": False, "handledBy": "plugin", "messageId": "131", "payload": {}}, indent=2))


class Relay:
    """A sender built on the real send() with a scripted CLI: counts every CLI run (= every delivery attempt that could reach Jim)."""
    def __init__(self, *results):
        self.results, self.runs = list(results), []

    def __call__(self, argv, **kw):
        self.runs.append(argv)
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, BaseException):
            raise r
        return r

    def sender(self, channel="telegram"):
        return lambda to, body: fa.send(to, body, run=self, channel=channel)


class ReplaySafetyTests(unittest.TestCase):
    """LIFE-71: a delivered alert is never re-sent (whatever the exit code), an ambiguous one at most once, an explicit failure at most MAX_ATTEMPTS times;
    a restart never replays; every outcome is visible in the diagnostics file. Nothing here sends anything: the CLI is a script."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patches = [mock.patch.object(fa, "STATE", self.dir), mock.patch.object(fa, "LAST", os.path.join(self.dir, "last-id"))]
        for p in self.patches:
            p.start()
        self.cache = [{"event": "message", "id": "a1", "title": "V7 domain jobs failed"}, {"event": "message", "id": "a2", "title": "Second"}]

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def poll(self, relay, channel="telegram", times=1, cache=None):
        for _ in range(times):
            since = fa.last_id(channel)
            seen = [e for e in (cache or self.cache)]
            if since:                                                                            # ntfy returns only what follows `since`
                ids = [e["id"] for e in seen]
                seen = seen[ids.index(since) + 1:] if since in ids else seen
            fa.follow("topic", "123456789", Stream(stream(*seen)), relay.sender(channel), channel=channel)

    def diagnostics(self):
        path = os.path.join(self.dir, fa.DIAGNOSTICS)
        return [json.loads(x) for x in open(path).read().splitlines()] if os.path.exists(path) else []

    def test_the_cli_result_decides_delivery_not_the_exit_code(self):
        self.assertEqual(fa.classify(0, ""), (fa.DELIVERED, "exit0"))
        self.assertEqual(fa.classify(1, DELIVERED_EXIT_1.stdout), (fa.DELIVERED, "evidence_nonzero_exit"))
        self.assertEqual(fa.classify(1, "warn: x\n" + json.dumps({"ok": False, "error": "partial", "sentBeforeError": True})), (fa.DELIVERED, "evidence_nonzero_exit"))
        self.assertEqual(fa.classify(1, json.dumps({"action": "send", "dryRun": False, "channel": "whatsapp", "payload": {}})), (fa.DELIVERED, "ok_result_nonzero_exit"))
        self.assertEqual(fa.classify(1, json.dumps({"ok": False, "error": "unknown target", "deliveryStatus": "failed"})), (fa.NOT_DELIVERED, "result_not_ok"))
        for stdout in ("", "Error: boom", "{not json", json.dumps({"something": "else"})):
            self.assertEqual(fa.classify(1, stdout)[0], fa.AMBIGUOUS, stdout)

    def test_send_uses_json_and_a_timeout_is_ambiguous_and_a_missing_cli_is_not_delivered(self):
        import subprocess
        relay = Relay(done(0, "{}"))
        self.assertEqual(fa.send("123", "hi", run=relay, channel="telegram"), (fa.DELIVERED, "exit0", 0))
        self.assertIn("--json", relay.runs[0])
        self.assertEqual(fa.send("123", "hi", run=Relay(subprocess.TimeoutExpired("openclaw", 90)))[:2], (fa.AMBIGUOUS, "timeout"))
        self.assertEqual(fa.send("123", "hi", run=Relay(FileNotFoundError()))[:2], (fa.NOT_DELIVERED, "not_started"))

    def test_the_flood_case_delivered_but_exit_1_is_sent_once_however_often_the_relay_polls(self):
        relay = Relay(DELIVERED_EXIT_1)
        self.poll(relay, times=50)                                                               # ~17 minutes of 20 s polls, the Oct 18 burst length
        self.assertEqual(len(relay.runs), 2)                                                     # a1 once, a2 once: never again
        self.assertEqual(fa.last_id("telegram"), "a2")
        self.assertEqual([d["outcome"] for d in self.diagnostics()], [fa.DELIVERED, fa.DELIVERED])

    def test_an_ambiguous_outcome_is_never_resent(self):
        relay = Relay(done(1, "", "Error: gateway closed"))
        self.poll(relay, times=20)
        self.assertEqual(len(relay.runs), 2)
        self.assertEqual([(d["id"], d["outcome"], d["class"]) for d in self.diagnostics()], [("a1", fa.AMBIGUOUS, "no_result"), ("a2", fa.AMBIGUOUS, "no_result")])

    def test_an_explicit_failure_is_retried_a_bounded_number_of_times_in_order_then_given_up(self):
        not_ok = done(1, json.dumps({"ok": False, "error": "rate limited"}))
        relay = Relay(not_ok)
        self.poll(relay, times=10)
        a1 = [r for r in relay.runs]
        self.assertEqual(len(relay.runs), 2 * fa.MAX_ATTEMPTS)                                   # a1 three times, then a2 three times: bounded, never forever
        self.assertEqual(fa.last_id("telegram"), "a2")
        outcomes = [(d["id"], d["outcome"]) for d in self.diagnostics()]
        self.assertEqual(outcomes, [("a1", fa.NOT_DELIVERED)] * 3 + [("a1", fa.GAVE_UP)] + [("a2", fa.NOT_DELIVERED)] * 3 + [("a2", fa.GAVE_UP)])
        self.assertTrue(a1)

    def test_a_transient_failure_then_success_delivers_once_and_keeps_order(self):
        relay = Relay(done(1, json.dumps({"ok": False, "error": "busy"})), done(0, "{}"), done(0, "{}"))
        self.poll(relay)                                                                         # a1 fails: a2 is NOT sent ahead of it
        self.assertEqual((len(relay.runs), fa.last_id("telegram")), (1, None))
        self.poll(relay, times=5)
        self.assertEqual((len(relay.runs), fa.last_id("telegram")), (3, "a2"))

    def test_a_restart_never_replays_a_delivered_or_an_interrupted_send(self):
        relay = Relay(DELIVERED_EXIT_1)
        self.poll(relay)
        self.assertEqual(len(relay.runs), 2)
        fresh = importlib.util.module_from_spec(spec)                                            # a restarted relay: new process state, same files
        spec.loader.exec_module(fresh)
        with mock.patch.object(fresh, "STATE", self.dir), mock.patch.object(fresh, "LAST", os.path.join(self.dir, "last-id")):
            again = Relay(DELIVERED_EXIT_1)
            urls = Stream(stream())
            fresh.follow("topic", "123456789", urls, again.sender(), channel="telegram")
            self.assertTrue(urls.urls[0].endswith("since=a2"))
            self.assertEqual(again.runs, [])
        self.setUp()                                                                             # killed DURING a send: the attempt was written first
        crash = Relay(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.poll(crash)
        self.assertEqual(fa.attempts("telegram"), {"id": "a1", "count": 1, "pending": True})
        after = Relay(done(0, "{}"))
        self.poll(after)
        self.assertEqual([r for r in after.runs].__len__(), 1)                                   # only a2: a1 may have gone out, so it is not replayed
        self.assertEqual([(d["id"], d["outcome"], d["class"]) for d in self.diagnostics()][:1], [("a1", fa.AMBIGUOUS, "interrupted")])
        self.assertEqual(fa.last_id("telegram"), "a2")

    def test_channels_keep_separate_positions_and_attempts(self):
        self.poll(Relay(done(0, "{}")), channel="whatsapp")
        bad = Relay(done(1, json.dumps({"ok": False, "error": "x"})))
        self.poll(bad, channel="telegram")
        self.assertEqual((fa.last_id("whatsapp"), fa.last_id("telegram")), ("a2", None))
        self.assertEqual((fa.attempts("whatsapp"), fa.attempts("telegram")["count"]), ({}, 1))

    def test_repeated_polls_with_nothing_new_send_nothing_and_the_diagnostics_stay_bounded_and_private(self):
        relay = Relay(done(0, "{}"))
        self.poll(relay)
        self.poll(relay, times=30)
        self.assertEqual(len(relay.runs), 2)
        for i in range(fa.DIAGNOSTICS_KEEP + 50):
            fa.diagnose("telegram", f"x{i}", fa.DELIVERED, "exit0", 0)
        lines = self.diagnostics()
        self.assertEqual(len(lines), fa.DIAGNOSTICS_KEEP)
        self.assertEqual(lines[-1]["id"], f"x{fa.DIAGNOSTICS_KEEP + 49}")
        self.assertEqual(os.stat(os.path.join(self.dir, fa.DIAGNOSTICS)).st_mode & 0o777, 0o600)
        self.assertNotIn("domain jobs", open(os.path.join(self.dir, fa.DIAGNOSTICS)).read())      # never the alert text


if __name__ == "__main__":
    unittest.main()
