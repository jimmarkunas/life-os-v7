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


if __name__ == "__main__":
    unittest.main()
