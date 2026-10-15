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


def tg_block(key, name="Bob Example"):
    return {"health": "CURRENT", "auth": "READY", "error": None, "observed_at": "2026-10-14T15:00:00Z", "chats_main": 40, "unread_messages": 30,
            "unread_messages_unmuted": 7, "unread_chats": 6, "unread_chats_unmuted": 3, "latest_state": "PRESENT", "latest": ep.scramble(key, name)}


def newest_first():
    return [message(1, name="Older Person", minutes=30), message(2, name=NAME, minutes=2), {**message(3, minutes=1), "from_me": True, "sender": None}]


class Endpoint(unittest.TestCase):
    def call(self, payload, wrapper):
        out = io.BytesIO()
        with mock.patch.object(ep, "load_wrapper", lambda path=None: wrapper):
            code = ep.main(io.BytesIO(payload), out, whois, "unused", "/nonexistent/event-key", uid=lambda: 0)
        return code, out.getvalue()

    def test_count_and_scrambled_newest_incoming_sender_only(self):
        code, raw = self.call(v2(RUNNER) + b"comms3 " + KEY.hex().encode() + b"\n", Wrapper(msgs=newest_first()))
        self.assertEqual(code, 0)
        p = json.loads(raw)
        sealed = p.pop("latest")
        p["telegram"] = {k: v for k, v in p["telegram"].items() if k != "latest"}
        p["whatsapp"] = {k: v for k, v in p["whatsapp"].items() if k != "latest"}
        self.assertEqual(set(sealed), {"nonce", "box", "tag"})
        self.assertTrue(all(set(v) <= set("0123456789abcdef") for v in sealed.values()))       # the scrambled name is hex only
        for leak in (NAME, "Alice", "Older", SECRET_HANDLE, SECRET_TEXT, "m2", "c3"):
            self.assertNotIn(leak, json.dumps(p))
        self.assertEqual(card.validate(raw, KEY, NOW)[0]["messages"], (12, "PRESENT", NAME))   # the runner's key opens it; the newest incoming sender, not mine
        self.assertEqual((p["telegram"]["health"], p["whatsapp"]["health"]), ("UNAVAILABLE", "UNAVAILABLE"))   # no adapters here: unavailable, never zero
        with self.assertRaises(card.CommsError):
            card.validate(raw, bytes(32), NOW)                                          # any other key cannot

    def test_no_incoming_and_failed_sources_are_never_a_guess(self):
        _, raw = self.call(v2(RUNNER) + b"comms3 " + KEY.hex().encode() + b"\n", Wrapper(msgs=[]))
        self.assertEqual(card.validate(raw, KEY, NOW)[0]["messages"], (12, "NONE", None))
        _, raw = self.call(v2(RUNNER) + b"comms " + KEY.hex().encode() + b"\n", Wrapper(counts_health="DISCONNECTED", recent_health="STALE"))
        p = json.loads(raw)
        self.assertEqual((p["health"], p["unread_7d"], p["latest_state"], p["latest"]), ("DEGRADED", None, "UNAVAILABLE", None))

    def test_a_missing_name_is_unknown_contact_never_the_handle(self):
        _, raw = self.call(v2(RUNNER) + b"comms3 " + KEY.hex().encode() + b"\n", Wrapper(msgs=[message(1, name=None)]))
        self.assertEqual(card.validate(raw, KEY, NOW)[0]["messages"][2], "Unknown contact")

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


class TelegramBlock(unittest.TestCase):
    def adapter(self, reply, code=0):
        return lambda argv, **kw: mock.Mock(returncode=code, stdout=json.dumps(reply) if isinstance(reply, dict) else reply)

    def test_counts_pass_through_and_only_the_name_is_scrambled(self):
        reply = {"channel": "telegram", "health": "CURRENT", "auth": "READY", "error": None, "observed_at": "2026-10-14T15:00:00Z", "chats_main": 40,
                 "unread_messages": 30, "unread_messages_unmuted": 7, "unread_chats": 6, "unread_chats_unmuted": 3, "latest_state": "PRESENT",
                 "latest_name": "Bob Example", "latest_at": "2026-10-14T14:00:00Z", "scope": "x"}
        block = ep.telegram(KEY, self.adapter(reply))
        self.assertEqual(set(block), card.TG_KEYS)
        self.assertNotIn("Bob", json.dumps(block))
        self.assertEqual(card.unscramble(KEY, block["latest"]), "Bob Example")
        self.assertEqual(block["unread_messages_unmuted"], 7)

    def test_a_missing_failed_or_degraded_adapter_is_unavailable_never_zero(self):
        degraded = {"channel": "telegram", "health": "DEGRADED", "auth": "READY", "error": "NOT_CONNECTED", "unread_messages": 3, "latest_state": "PRESENT",
                    "latest_name": "Bob Example"}
        for run in (self.adapter("", 1), self.adapter("not json"), self.adapter({"channel": "other"}), self.adapter(degraded),
                    lambda argv, **kw: (_ for _ in ()).throw(OSError())):
            block = ep.telegram(KEY, run)
            self.assertTrue(all(block[k] is None for k in card.TG_COUNTS))
            self.assertEqual((block["latest_state"], block["latest"]), ("UNAVAILABLE", None))


class WhatsAppBlock(unittest.TestCase):
    def serve_once(self, reply):
        import os, socket, tempfile, threading
        path = os.path.join(tempfile.mkdtemp(), "a.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)

        def answer():
            conn, _ = srv.accept()
            conn.sendall(reply if isinstance(reply, bytes) else json.dumps(reply).encode())
            conn.close()
            srv.close()
        threading.Thread(target=answer, daemon=True).start()
        return path

    def snapshot(self, **change):
        s = {"channel": "whatsapp", "health": "CURRENT", "observed_at": "2026-10-14T15:00:00Z", "chats_known": 80, "unread_messages": 12,
             "unread_messages_unmuted": 9, "unread_chats": 5, "unread_chats_unmuted": 4, "latest_state": "PRESENT", "latest_name": "Carol Example"}
        s.update(change)
        return s

    def test_counts_pass_through_and_only_the_name_is_scrambled(self):
        block = ep.whatsapp(KEY, self.serve_once(self.snapshot()))
        self.assertEqual(set(block), card.TG_KEYS)
        self.assertNotIn("Carol", json.dumps(block))
        self.assertEqual((block["unread_messages_unmuted"], card.unscramble(KEY, block["latest"])), (9, "Carol Example"))

    def test_not_linked_disconnected_missing_or_malformed_is_unavailable_never_zero(self):
        for path in (self.serve_once(self.snapshot(health="NOT_LINKED", unread_messages=0)), self.serve_once(self.snapshot(health="DISCONNECTED")),
                     self.serve_once(b"not json"), self.serve_once(self.snapshot(channel="telegram")), "/nonexistent/attention.sock"):
            block = ep.whatsapp(KEY, path)
            self.assertEqual(block["health"], "UNAVAILABLE")
            self.assertTrue(all(block[k] is None for k in card.TG_COUNTS))
            self.assertIsNone(block["latest"])


class Contract(unittest.TestCase):
    def payload(self, **change):
        p = {"schema": card.SCHEMA, "observed_at": "2026-10-14T15:00:00Z", "health": "CURRENT", "unread_7d": 5, "latest_state": "PRESENT",
             "latest": ep.scramble(KEY, NAME), "telegram": tg_block(KEY), "whatsapp": tg_block(KEY, "Carol Example")}
        p.update(change)
        return json.dumps(p).encode()

    def test_tampered_stale_and_off_contract_answers_are_refused(self):
        sealed = ep.scramble(KEY, NAME)
        flipped = {**sealed, "box": ("%02x" % (int(sealed["box"][:2], 16) ^ 1)) + sealed["box"][2:]}
        for raw in (b"", self.payload(latest=flipped), self.payload(observed_at="2026-10-14T14:00:00Z"), self.payload(unread_7d=-1),
                    self.payload(unread_7d=True), self.payload(latest_state="NONE"), self.payload(schema="x"), b"[]", b"x" * 5000):
            with self.assertRaises(card.CommsError, msg=raw[:40]):
                card.validate(raw, KEY, NOW)

    def test_tampered_or_inconsistent_telegram_blocks_are_refused(self):
        bad = (dict(tg_block(KEY), health="DEGRADED"),                                    # a non-current block may not carry counts or a name
               dict(tg_block(KEY), unread_messages=-1), dict(tg_block(KEY), latest_state="NONE"), {k: v for k, v in tg_block(KEY).items() if k != "auth"},
               dict(tg_block(KEY), latest={**ep.scramble(KEY, NAME), "tag": "00" * 16}))
        for block in bad:
            with self.assertRaises(card.CommsError):
                card.validate(self.payload(telegram=block), KEY, NOW)

    def test_render(self):
        lines, heading = card.render({"messages": (45, "PRESENT", NAME), "telegram": (150, "NONE", None)}, NOW)
        self.assertEqual(lines, {"messages": ("Messages (45)", "Latest: Alice Example"), "telegram": ("Telegram (99+)", "Latest: none")})
        self.assertEqual(heading, "Communications Attention (Updated 10:00 AM CT)")
        self.assertEqual(card.render(card.UNAVAILABLE, NOW)[0]["messages"], ("Messages (—)", "Latest: unavailable"))


class Card(unittest.TestCase):
    ENV = {"IMESSAGE_ALERTS_HOST": "100.64.0.9", "ATTENTION_CARD_BLOCK_ID": "attn", "NOTION_JIRA_TOKEN": "unused"}

    def run_card(self, report, live=True, answer=None, env=None):
        answer = answer or (lambda host, key: json.dumps({"schema": card.SCHEMA, "observed_at": "2026-10-14T15:00:00Z", "health": "CURRENT", "unread_7d": 45,
                                                          "latest_state": "PRESENT", "latest": ep.scramble(key, NAME), "telegram": tg_block(key),
                                                          "whatsapp": tg_block(key, "Carol Example")}).encode())
        out = io.StringIO()
        with mock.patch.object(report_region, "protected", lambda client, ids, fail: {"Attention": "same"}), contextlib.redirect_stdout(out):
            counts = card.run(0, live, env or self.ENV, NOW, answer, report, keygen=lambda n: KEY)
        return counts, out.getvalue()

    def test_live_writes_only_the_messages_and_telegram_rows_their_latest_lines_and_the_time(self):
        report = Report()
        others = {b: report.plain(b) for b in ("l3", "r3", "attn", "bills")}
        counts, log = self.run_card(report)
        self.assertEqual((counts["unread_7d"], counts["latest"], counts["telegram_latest"], counts["telegram_unread_messages_unmuted"], counts["written"]),
                         (45, "present", "present", 7, 7))
        self.assertEqual([p[0] for p in report.patches], ["l0", "r0", "l1", "r1", "l2", "r2", "head"])
        self.assertEqual((report.plain("l2"), report.plain("r2")), (":whatsapp: WhatsApp (7)", "Latest: Carol Example"))
        self.assertEqual((report.plain("l1"), report.plain("r1")), (":telegram: Telegram (7)", "Latest: Bob Example"))
        self.assertEqual(report.plain("l0"), ":messages: Messages (45)")                 # Jim's icon and the space are sent back as read
        self.assertEqual(report.blocks["l0"]["paragraph"]["rich_text"][0]["mention"]["custom_emoji"]["id"], "e-msg")
        self.assertEqual(report.blocks["l0"]["paragraph"]["rich_text"][2]["text"]["link"]["url"], "https://example.com/launch/messages/")
        self.assertEqual(report.plain("r0"), "Latest: Alice Example")
        self.assertEqual(report.plain("head"), "Communications Attention (Updated 10:00 AM CT)")
        self.assertEqual({b: report.plain(b) for b in others}, others)
        self.assertNotIn("Alice", log)
        self.assertNotIn("Bob", log)
        self.assertNotIn("Carol", log)

    def test_a_restarted_whatsapp_source_keeps_the_name_already_in_notion(self):
        report = Report()
        report.blocks["r2"]["paragraph"]["rich_text"] = [{"type": "text", "plain_text": "Latest: Carol Example", "text": {"content": "Latest: Carol Example", "link": None}}]
        wa = dict(tg_block(KEY), latest_state="UNAVAILABLE", latest=None)
        answer = lambda host, key: json.dumps({"schema": card.SCHEMA, "observed_at": "2026-10-14T15:00:00Z", "health": "CURRENT", "unread_7d": 45,
                                               "latest_state": "PRESENT", "latest": ep.scramble(key, NAME), "telegram": tg_block(key),
                                               "whatsapp": dict(wa, latest=None)}).encode()
        counts, _ = self.run_card(report, answer=answer)
        self.assertEqual((report.plain("l2"), report.plain("r2")), (":whatsapp: WhatsApp (7)", "Latest: Carol Example"))
        self.assertNotIn("r2", [p[0] for p in report.patches])
        self.assertEqual(counts["written"], 7)

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
            self.assertEqual((report.plain("l1"), report.plain("r1")), (":telegram: Telegram (—)", "Latest: unavailable"))
            self.assertEqual((report.plain("l2"), report.plain("r2")), (":whatsapp: WhatsApp (—)", "Latest: unavailable"))
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
