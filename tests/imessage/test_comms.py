import contextlib
import datetime as dt
import io
import json
import unittest
from unittest import mock

from lifeos.imessage import alerts_endpoint as ep, comms_card as card
from lifeos.platform import report_region
from tests.imessage.test_alerts import RUNNER, OTHER, SECRET_HANDLE, SECRET_TEXT, message, v2, whois
from tests.kit.comms import Report

KEY = bytes(range(32))
NOW = dt.datetime(2026, 10, 14, 15, 0, 30, tzinfo=dt.timezone.utc)
NAME = "Alice Example"


class Wrapper:
    """The wrapper's `run` for exactly the two forms comms uses."""

    def __init__(self, unread=12, msgs=None, counts_health="CURRENT", recent_health="CURRENT"):
        self.unread, self.msgs, self.ch, self.rh, self.calls = unread, msgs, counts_health, recent_health, []

    def run(self, argv, now=None):
        self.calls.append(argv)
        if argv == ["counts"]:
            unread = {"all_time": {"messages": 900, "conversations": 9}, "last_7_days": {"messages": self.unread, "conversations": 3},
                      "last_24_hours": {"messages": 1, "conversations": 1}}
            counts = {"unread": unread, "unread_messages_by_service_all_time": {"iMessage": 900}, "unread_conversations_all_time": {"group": 1, "direct": 8}}
            return {"health": self.ch, "observed_at": "2026-10-14T15:00:00Z", "counts": counts if self.ch == "CURRENT" else None}
        if argv == ["recent", "--hours", "72"]:
            return {"health": self.rh, "observed_at": "2026-10-14T15:00:00Z", "messages": self.msgs if self.rh == "CURRENT" else None}
        raise AssertionError(argv)


def newest_first():
    return [message(1, name="Older Person", minutes=30), message(2, name=NAME, minutes=2), {**message(3, minutes=1), "from_me": True, "sender": None}]


class Endpoint(unittest.TestCase):
    def call(self, payload, wrapper):
        out = io.BytesIO()
        with mock.patch.object(ep, "load_wrapper", lambda path=None: wrapper):
            code = ep.main(io.BytesIO(payload), out, whois, "unused", "/nonexistent/event-key", uid=lambda: 0)
        return code, out.getvalue()

    def test_count_and_scrambled_newest_incoming_sender_only(self):
        code, raw = self.call(v2(RUNNER) + b"comms " + KEY.hex().encode() + b"\n", Wrapper(msgs=newest_first()))
        self.assertEqual(code, 0)
        p = json.loads(raw)
        sealed = p.pop("latest")
        self.assertEqual(set(sealed), {"nonce", "box", "tag"})
        self.assertTrue(all(set(v) <= set("0123456789abcdef") for v in sealed.values()))       # the scrambled name is hex only
        for leak in (NAME, "Alice", "Older", SECRET_HANDLE, SECRET_TEXT, "m2", "c3"):
            self.assertNotIn(leak, json.dumps(p))
        self.assertEqual(card.validate(raw, KEY, NOW), (12, "PRESENT", NAME))          # the runner's key opens it; the newest incoming sender, not mine
        with self.assertRaises(card.CommsError):
            card.validate(raw, bytes(32), NOW)                                          # any other key cannot

    def test_no_incoming_and_failed_sources_are_never_a_guess(self):
        _, raw = self.call(v2(RUNNER) + b"comms " + KEY.hex().encode() + b"\n", Wrapper(msgs=[]))
        self.assertEqual(card.validate(raw, KEY, NOW), (12, "NONE", None))
        _, raw = self.call(v2(RUNNER) + b"comms " + KEY.hex().encode() + b"\n", Wrapper(counts_health="DISCONNECTED", recent_health="STALE"))
        p = json.loads(raw)
        self.assertEqual((p["health"], p["unread_7d"], p["latest_state"], p["latest"]), ("DEGRADED", None, "UNAVAILABLE", None))

    def test_a_missing_name_is_unknown_contact_never_the_handle(self):
        _, raw = self.call(v2(RUNNER) + b"comms " + KEY.hex().encode() + b"\n", Wrapper(msgs=[message(1, name=None)]))
        self.assertEqual(card.validate(raw, KEY, NOW)[2], "Unknown contact")

    def test_refusals(self):
        w = Wrapper(msgs=newest_first())
        for payload in (v2(OTHER) + b"comms " + KEY.hex().encode() + b"\n", b"comms " + KEY.hex().encode() + b"\n",
                        v2(RUNNER) + b"comms " + KEY.hex().upper().encode() + b"\n", v2(RUNNER) + b"comms " + KEY.hex()[:-2].encode() + b"\n",
                        v2(RUNNER) + b"comms\n", v2(RUNNER) + b"comms " + KEY.hex().encode() + b" x\n"):
            self.assertEqual(self.call(payload, w), (1, b""), payload[-12:])
        self.assertEqual(w.calls, [])

    def test_alerts_requests_are_unchanged(self):
        self.assertEqual(ep.lookback(io.BytesIO(b"alerts 3\n")), 3)
        self.assertIsNone(ep.lookback(io.BytesIO(b"comms " + KEY.hex().encode() + b"\n")))


class Contract(unittest.TestCase):
    def payload(self, **change):
        p = {"schema": card.SCHEMA, "observed_at": "2026-10-14T15:00:00Z", "health": "CURRENT", "unread_7d": 5, "latest_state": "PRESENT",
             "latest": ep.scramble(KEY, NAME)}
        p.update(change)
        return json.dumps(p).encode()

    def test_tampered_stale_and_off_contract_answers_are_refused(self):
        sealed = ep.scramble(KEY, NAME)
        flipped = {**sealed, "box": ("%02x" % (int(sealed["box"][:2], 16) ^ 1)) + sealed["box"][2:]}
        for raw in (b"", self.payload(latest=flipped), self.payload(observed_at="2026-10-14T14:00:00Z"), self.payload(unread_7d=-1),
                    self.payload(unread_7d=True), self.payload(latest_state="NONE"), self.payload(schema="x"), b"[]", b"x" * 5000):
            with self.assertRaises(card.CommsError, msg=raw[:40]):
                card.validate(raw, KEY, NOW)

    def test_render(self):
        self.assertEqual(card.render(45, "PRESENT", NAME, NOW), ("Messages (45)", "Latest: Alice Example", "Communications Attention (Updated 10:00 AM CT)"))
        self.assertEqual(card.render(150, "NONE", None, NOW)[:2], ("Messages (99+)", "Latest: none in 3 days"))
        self.assertEqual(card.render(None, "UNAVAILABLE", None, NOW)[:2], ("Messages (—)", "Latest: unavailable"))


class Card(unittest.TestCase):
    ENV = {"IMESSAGE_ALERTS_HOST": "100.64.0.9", "ATTENTION_CARD_BLOCK_ID": "attn", "NOTION_JIRA_TOKEN": "unused"}

    def run_card(self, report, live=True, answer=None, env=None):
        answer = answer or (lambda host, key: json.dumps({"schema": card.SCHEMA, "observed_at": "2026-10-14T15:00:00Z", "health": "CURRENT", "unread_7d": 45,
                                                          "latest_state": "PRESENT", "latest": ep.scramble(key, NAME)}).encode())
        out = io.StringIO()
        with mock.patch.object(report_region, "protected", lambda client, ids, fail: {"Attention": "same"}), contextlib.redirect_stdout(out):
            counts = card.run(0, live, env or self.ENV, NOW, answer, report, keygen=lambda n: KEY)
        return counts, out.getvalue()

    def test_live_writes_only_the_messages_row_its_latest_line_and_the_time(self):
        report = Report()
        others = {b: report.plain(b) for b in ("l1", "l2", "l3", "r1", "r2", "r3", "attn", "bills")}
        counts, log = self.run_card(report)
        self.assertEqual(counts, {"unread_7d": 45, "latest": "present", "written": 3})
        self.assertEqual([p[0] for p in report.patches], ["l0", "r0", "head"])
        self.assertEqual(report.plain("l0"), ":messages: Messages (45)")                 # Jim's icon and the space are sent back as read
        self.assertEqual(report.blocks["l0"]["paragraph"]["rich_text"][0]["mention"]["custom_emoji"]["id"], "e-msg")
        self.assertEqual(report.blocks["l0"]["paragraph"]["rich_text"][2]["text"]["link"]["url"], "https://example.com/launch/messages/")
        self.assertEqual(report.plain("r0"), "Latest: Alice Example")
        self.assertEqual(report.plain("head"), "Communications Attention (Updated 10:00 AM CT)")
        self.assertEqual({b: report.plain(b) for b in others}, others)
        self.assertNotIn("Alice", log)

    def test_dry_run_writes_nothing(self):
        report = Report()
        self.assertEqual(self.run_card(report, live=False)[0]["written"], 0)
        self.assertEqual(report.patches, [])

    def test_an_unreachable_or_tampered_source_is_shown_as_unavailable(self):
        for answer in (lambda host, key: (_ for _ in ()).throw(OSError()), lambda host, key: b"{}"):
            report = Report()
            counts, log = self.run_card(report, answer=answer)
            self.assertEqual(counts["latest"], "unavailable")
            self.assertEqual((report.plain("l0"), report.plain("r0")), (":messages: Messages (—)", "Latest: unavailable"))
            self.assertIn("source refused", log)

    def test_a_missing_or_ambiguous_callout_is_not_owned(self):
        report = Report()
        report.add("comms2", "callout", "col", [])
        report.add("head2", "heading_3", "comms2", [{"type": "text", "plain_text": "Communications Attention", "text": {"content": "x", "link": None}}])
        with self.assertRaises(card.CommsError):
            self.run_card(report)
        self.assertEqual(report.patches, [])
        with self.assertRaises(card.CommsError):
            self.run_card(Report(), env={**self.ENV, "ATTENTION_CARD_BLOCK_ID": ""})

    def test_a_changed_protected_region_fails_after_the_write(self):
        states = iter([{"Attention": "a"}, {"Attention": "b"}])
        with mock.patch.object(report_region, "protected", lambda client, ids, fail: next(states)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(card.CommsError):
                card.run(0, True, self.ENV, NOW, lambda host, key: b"", Report(), keygen=lambda n: KEY)


if __name__ == "__main__":
    unittest.main()
