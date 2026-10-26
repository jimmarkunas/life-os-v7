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


def result(**fields):                                                                      # the shape of OpenClaw 2026.9.9 buildMessageCliJson
    return json.dumps({"action": "send", "channel": "telegram", "dryRun": False, "handledBy": "plugin", "payload": {}, **fields}, indent=2)


DELIVERED_EXIT_1 = done(1, result(messageId="131"))                                         # the flood signature: delivered, then exit 1
OK = done(0, result(messageId="m1"))
REFUSED = done(1, result(ok=False, error="Message send suppressed: policy.", deliveryStatus="suppressed"))      # proven never sent


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

    def test_only_verified_evidence_is_delivered_and_only_a_proven_refusal_is_retried(self):
        """Career TL review 5576359134: the exit code proves nothing either way; a generic failure is never retried."""
        cases = [
            ((1, result(messageId="131")), (fa.DELIVERED, "message_id")),                       # exit 1 with an authoritative message id
            ((0, result(messageId="m1")), (fa.DELIVERED, "message_id")),
            ((1, "noise\n" + result(ok=False, error="partial", deliveryStatus="partial_failed", sentBeforeError=True)), (fa.DELIVERED, "sent_before_error")),
            ((1, result(ok=False, error="x", deliveryStatus="failed", sentBeforeError=True)), (fa.DELIVERED, "sent_before_error")),
            ((0, result(payload={"deliveryStatus": "sent"})), (fa.DELIVERED, "status_sent")),
            ((1, REFUSED.stdout), (fa.NOT_DELIVERED, "suppressed_before_send")),                # confirmed pre-send refusal: the only retry
            ((0, result(ok=False, error="send failed", deliveryStatus="failed")), (fa.AMBIGUOUS, "failed_unproven")),   # exit 0 contradicting an explicit failure
            ((1, result(ok=False, error="closed after send", deliveryStatus="failed")), (fa.AMBIGUOUS, "failed_unproven")),  # post-send failure, no id
            ((1, result(ok=False, error="generic")), (fa.AMBIGUOUS, "failed_unproven")),
            ((0, result()), (fa.AMBIGUOUS, "no_delivery_evidence")),                            # exit 0 alone is not delivery evidence
            ((0, result(payload={"status": "delivery_queued", "delivered": False})), (fa.AMBIGUOUS, "gateway_queued")),
            ((0, result(dryRun=True, messageId="x")), (fa.AMBIGUOUS, "not_a_send_result")),
            ((0, ""), (fa.AMBIGUOUS, "no_result")), ((1, "Error: gateway rejected"), (fa.AMBIGUOUS, "no_result")), ((1, "{not json"), (fa.AMBIGUOUS, "no_result")),
        ]
        for (code, stdout), want in cases:
            self.assertEqual(fa.classify(code, stdout), want, (code, stdout[:60]))

    def test_send_uses_json_and_a_timeout_is_ambiguous_and_a_missing_cli_is_not_delivered(self):
        import subprocess
        relay = Relay(OK)
        self.assertEqual(fa.send("123", "hi", run=relay, channel="telegram"), (fa.DELIVERED, "message_id", 0))
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
        relay = Relay(done(1, "", "Error: gateway closed"))                                    # no result at all
        self.poll(relay, times=20)
        self.assertEqual(len(relay.runs), 2)
        self.assertEqual([(d["id"], d["outcome"], d["class"]) for d in self.diagnostics()], [("a1", fa.AMBIGUOUS, "no_result"), ("a2", fa.AMBIGUOUS, "no_result")])

    def test_an_explicit_failure_is_retried_a_bounded_number_of_times_in_order_then_given_up(self):
        relay = Relay(REFUSED)
        self.poll(relay, times=10)
        a1 = [r for r in relay.runs]
        self.assertEqual(len(relay.runs), 2 * fa.MAX_ATTEMPTS)                                   # a1 three times, then a2 three times: bounded, never forever
        self.assertEqual(fa.last_id("telegram"), "a2")
        outcomes = [(d["id"], d["outcome"]) for d in self.diagnostics()]
        self.assertEqual(outcomes, [("a1", fa.NOT_DELIVERED)] * 3 + [("a1", fa.GAVE_UP)] + [("a2", fa.NOT_DELIVERED)] * 3 + [("a2", fa.GAVE_UP)])
        self.assertTrue(a1)

    def test_a_transient_failure_then_success_delivers_once_and_keeps_order(self):
        relay = Relay(REFUSED, OK, OK)
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
        after = Relay(OK)
        self.poll(after)
        self.assertEqual([r for r in after.runs].__len__(), 1)                                   # only a2: a1 may have gone out, so it is not replayed
        self.assertEqual([(d["id"], d["outcome"], d["class"]) for d in self.diagnostics()][:1], [("a1", fa.AMBIGUOUS, "interrupted")])
        self.assertEqual(fa.last_id("telegram"), "a2")

    def test_channels_keep_separate_positions_and_attempts(self):
        self.poll(Relay(OK), channel="whatsapp")
        bad = Relay(REFUSED)
        self.poll(bad, channel="telegram")
        self.assertEqual((fa.last_id("whatsapp"), fa.last_id("telegram")), ("a2", None))
        self.assertEqual((fa.attempts("whatsapp"), fa.attempts("telegram")["count"]), ({}, 1))

    def test_repeated_polls_with_nothing_new_send_nothing_and_the_diagnostics_stay_bounded_and_private(self):
        relay = Relay(OK)
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



class CorruptStateFailsClosedTests(unittest.TestCase):
    """Career TL review 5576359134: saved state that EXISTS but is unreadable or invalid holds that channel (no poll, no send, no 10-minute fallback, one HELD
    line); an ABSENT file is a fresh start; the other channel keeps working."""

    setUp, tearDown, poll, diagnostics = ReplaySafetyTests.setUp, ReplaySafetyTests.tearDown, ReplaySafetyTests.poll, ReplaySafetyTests.diagnostics

    def held(self, channel="telegram"):
        opener, relay = Stream(stream(*self.cache)), Relay(OK)
        for _ in range(5):                                                                       # repeated polls: still nothing, and one HELD line per cause
            fa.follow("topic", "123456789", opener, relay.sender(channel), channel=channel)
        return opener.urls, relay.runs

    def write(self, name, data):
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(os.path.join(self.dir, name), mode) as f:
            f.write(data)

    def test_invalid_or_unreadable_attempts_hold_the_channel_with_zero_sends(self):
        bad = ["{not json", "[]", json.dumps({"id": "a1", "count": 1}), json.dumps({"id": "a1", "count": "1", "pending": True}),
               json.dumps({"id": "a1", "count": 0, "pending": False}), json.dumps({"id": "a1", "count": 99, "pending": False}),
               json.dumps({"id": "a1", "count": 1, "pending": "yes"}), json.dumps({"id": "a/1", "count": 1, "pending": True}),
               json.dumps({"id": "a1", "count": 1, "pending": True, "extra": 1}), b"\xff\xfe"]
        for data in bad:
            self.setUp()
            fa.remember("a0", "telegram")
            self.write("attempts-telegram", data)
            urls, runs = self.held()
            self.assertEqual((urls, runs), ([], []), data)                                      # no poll, no send attempt
            self.assertEqual(fa.last_id("telegram"), "a0", data)
            self.assertEqual([(d["outcome"], d["class"]) for d in self.diagnostics()], [(fa.HELD, "invalid_attempts")], data)
        self.setUp()
        os.mkdir(os.path.join(self.dir, "attempts-telegram"))                                   # unreadable (a directory where the file should be)
        self.assertEqual(self.held(), ([], []))

    def test_invalid_or_unreadable_cursor_holds_the_channel_and_never_falls_back_to_ten_minutes(self):
        for data in ("", "a/1", "x" * 40, "a1 a2", b"\xff\xfe"):
            self.setUp()
            self.write("last-id-telegram", data)
            urls, runs = self.held()
            self.assertEqual((urls, runs), ([], []), data)
            self.assertEqual([(d["outcome"], d["class"]) for d in self.diagnostics()], [(fa.HELD, "invalid_cursor")], data)
        self.setUp()
        os.mkdir(os.path.join(self.dir, "last-id-telegram"))
        self.assertEqual(self.held(), ([], []))

    def test_a_held_channel_does_not_stop_the_other_and_absent_state_is_a_fresh_start(self):
        self.write("attempts-telegram", "{not json")
        self.assertEqual(self.held("telegram"), ([], []))
        whatsapp = Relay(OK)
        self.poll(whatsapp, channel="whatsapp")                                                  # absent cursor and attempts: a first start
        self.assertEqual((len(whatsapp.runs), fa.last_id("whatsapp")), (2, "a2"))
        os.unlink(os.path.join(self.dir, "attempts-telegram"))                                  # a person repaired it (removed the bad file, kept the cursor)
        fa.remember("a1", "telegram")
        repaired = Relay(OK)
        self.poll(repaired, channel="telegram")
        self.assertEqual((len(repaired.runs), fa.last_id("telegram")), (1, "a2"))

    def test_valid_saved_state_still_works(self):
        fa.remember("a0", "telegram")
        fa._write_attempts("telegram", {"id": "a1", "count": 1, "pending": False})               # a proven refusal last time: retried now
        relay = Relay(OK)
        self.poll(relay)
        self.assertEqual((len(relay.runs), fa.last_id("telegram"), fa.attempts("telegram")), (2, "a2", {}))


class HourlyCapTests(unittest.TestCase):
    """LIFE-73: at most RELAY_CAP copies per channel per clock hour, then ONE paused notice and the rest dropped (ntfy keeps the originals)."""

    HOUR = 1_800_000_000 // 3600 * 3600                                    # a clock hour start

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patches = [mock.patch.object(fa, "STATE", self.dir), mock.patch.object(fa, "LAST", os.path.join(self.dir, "last-id"))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.sent = []

    def sender(self, to, body):
        self.sent.append(body)
        return (fa.DELIVERED, "message_id", 0)

    def flood(self, n, start=0, now=None):
        events = [{"event": "message", "id": f"m{start + i:04d}", "title": f"A{start + i}"} for i in range(n)]
        fa.follow("topic", "+15550100001", Stream(stream(*events)), self.sender, now=lambda: now or self.HOUR + 60)

    def test_up_to_the_cap_every_copy_is_forwarded(self):
        self.flood(fa.RELAY_CAP)
        self.assertEqual(len(self.sent), fa.RELAY_CAP)
        self.assertNotIn(fa.PAUSE_TEXT, self.sent)

    def test_a_flood_sends_the_cap_then_one_paused_notice_and_drops_the_rest(self):
        self.flood(40)
        self.assertEqual(len(self.sent), fa.RELAY_CAP + 1)
        self.assertEqual(self.sent.count(fa.PAUSE_TEXT), 1)
        self.assertEqual(self.sent[-1], fa.PAUSE_TEXT)
        self.assertEqual(fa.last_id(), "m0039")                                # the position moved past every dropped copy: nothing replays

    def test_the_diagnostics_record_the_cap_without_alert_text(self):
        self.flood(10)
        lines = [json.loads(x) for x in open(os.path.join(self.dir, fa.DIAGNOSTICS)).read().splitlines()]
        self.assertEqual([x["outcome"] for x in lines].count(fa.CAPPED), 10 - fa.RELAY_CAP)
        self.assertEqual([x["outcome"] for x in lines].count(fa.PAUSED), 1)
        self.assertNotIn("A9", json.dumps(lines))

    def test_the_next_clock_hour_starts_at_zero(self):
        self.flood(20)
        self.sent.clear()
        self.flood(3, start=100, now=self.HOUR + 3600 + 5)
        self.assertEqual(len(self.sent), 3)
        self.assertNotIn(fa.PAUSE_TEXT, self.sent)

    def test_each_channel_has_its_own_count(self):
        sent = {"whatsapp": [], "telegram": []}
        for channel in sent:
            events = [{"event": "message", "id": f"m{i:04d}", "title": f"A{i}"} for i in range(fa.RELAY_CAP)]
            fa.follow("topic", "x", Stream(stream(*events)), lambda to, body, c=channel: sent[c].append(body) or True, channel=channel, now=lambda: self.HOUR + 1)
        self.assertEqual([len(sent["whatsapp"]), len(sent["telegram"])], [fa.RELAY_CAP, fa.RELAY_CAP])

    def test_an_unreadable_rate_file_holds_the_channel_instead_of_lifting_the_cap(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, "rate-whatsapp"), "w") as f:
            f.write("not json")
        self.flood(3)
        self.assertEqual(self.sent, [])
        self.assertEqual(json.loads(open(os.path.join(self.dir, fa.DIAGNOSTICS)).read().splitlines()[-1])["class"], "invalid_rate")

    def test_the_count_is_written_before_the_send(self):
        seen = []

        def sender(to, body):
            seen.append(json.load(open(os.path.join(self.dir, "rate-whatsapp")))["count"])
            return (fa.DELIVERED, "message_id", 0)
        fa.follow("topic", "x", Stream(stream({"event": "message", "id": "m0001", "title": "A"})), sender, now=lambda: self.HOUR + 1)
        self.assertEqual(seen, [1])


if __name__ == "__main__":
    unittest.main()
