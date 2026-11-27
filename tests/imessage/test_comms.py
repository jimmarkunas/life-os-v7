import contextlib
from copy import deepcopy
import copy
import datetime as dt
import io
import json
import unittest
from unittest import mock

from lifeos.imessage import alerts_endpoint as ep, comms_card as card
from tests.imessage.test_alerts import RUNNER, OTHER, SECRET_HANDLE, SECRET_TEXT, message, v2, whois
from tests.kit.comms import LAUNCH, Report, text

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
            unread = {
                "all_time": {"messages": 900, "conversations": 9},
                "last_7_days": {"messages": self.unread, "conversations": 3},
                "last_24_hours": {"messages": 1, "conversations": 1},
            }
            counts = {
                "unread": unread,
                "unread_messages_by_service_all_time": {"iMessage": 900},
                "unread_conversations_all_time": {"group": 1, "direct": 8},
            }
            return {
                "health": self.ch,
                "observed_at": "2026-10-14T15:00:00Z",
                "counts": counts if self.ch == "CURRENT" else None,
            }
        if argv == ["recent", "--hours", "72"]:
            return {
                "health": self.rh,
                "observed_at": "2026-10-14T15:00:00Z",
                "messages": self.msgs if self.rh == "CURRENT" else None,
            }
        raise AssertionError(argv)


def tg_block(key, name="Bob Example"):
    return {
        "health": "CURRENT",
        "auth": "READY",
        "error": None,
        "observed_at": "2026-10-14T15:00:00Z",
        "chats_main": 40,
        "unread_messages": 30,
        "unread_messages_unmuted": 7,
        "unread_chats": 6,
        "unread_chats_unmuted": 3,
        "latest_state": "PRESENT",
        "latest": ep.scramble(key, name),
    }


def newest_first():
    return [
        message(1, name="Older Person", minutes=30),
        message(2, name=NAME, minutes=2),
        {**message(3, minutes=1), "from_me": True, "sender": None},
    ]


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
        self.assertTrue(
            all(set(v) <= set("0123456789abcdef") for v in sealed.values())
        )  # the scrambled name is hex only
        for leak in (NAME, "Alice", "Older", SECRET_HANDLE, SECRET_TEXT, "m2", "c3"):
            self.assertNotIn(leak, json.dumps(p))
        self.assertEqual(
            card.validate(raw, KEY, NOW)[0]["messages"], (12, "PRESENT", NAME)
        )  # the runner's key opens it
        self.assertEqual(
            (p["telegram"]["health"], p["whatsapp"]["health"]), ("UNAVAILABLE", "UNAVAILABLE")
        )  # no adapters here: unavailable, never zero
        with self.assertRaises(card.CommsError):
            card.validate(raw, bytes(32), NOW)  # any other key cannot

    def test_no_incoming_and_failed_sources_are_never_a_guess(self):
        _, raw = self.call(v2(RUNNER) + b"comms3 " + KEY.hex().encode() + b"\n", Wrapper(msgs=[]))
        self.assertEqual(card.validate(raw, KEY, NOW)[0]["messages"], (12, "NONE", None))
        _, raw = self.call(
            v2(RUNNER) + b"comms " + KEY.hex().encode() + b"\n",
            Wrapper(counts_health="DISCONNECTED", recent_health="STALE"),
        )
        p = json.loads(raw)
        self.assertEqual(
            (p["health"], p["unread_7d"], p["latest_state"], p["latest"]), ("DEGRADED", None, "UNAVAILABLE", None)
        )

    def test_a_missing_name_is_unknown_contact_never_the_handle(self):
        _, raw = self.call(v2(RUNNER) + b"comms3 " + KEY.hex().encode() + b"\n", Wrapper(msgs=[message(1, name=None)]))
        self.assertEqual(card.validate(raw, KEY, NOW)[0]["messages"][2], "Unknown contact")

    def test_refusals(self):
        w = Wrapper(msgs=newest_first())
        for payload in (
            v2(OTHER) + b"comms " + KEY.hex().encode() + b"\n",
            b"comms " + KEY.hex().encode() + b"\n",
            v2(RUNNER) + b"comms " + KEY.hex().upper().encode() + b"\n",
            v2(RUNNER) + b"comms " + KEY.hex()[:-2].encode() + b"\n",
            v2(RUNNER) + b"comms\n",
            v2(RUNNER) + b"comms " + KEY.hex().encode() + b" x\n",
        ):
            self.assertEqual(self.call(payload, w), (1, b""), payload[-12:])
        self.assertEqual(w.calls, [])

    def test_alerts_requests_are_unchanged(self):
        self.assertEqual(ep.lookback(io.BytesIO(b"alerts 3\n")), 3)
        self.assertIsNone(ep.lookback(io.BytesIO(b"comms " + KEY.hex().encode() + b"\n")))


class TelegramBlock(unittest.TestCase):
    def adapter(self, reply, code=0):
        return lambda argv, **kw: mock.Mock(
            returncode=code, stdout=json.dumps(reply) if isinstance(reply, dict) else reply
        )

    def test_counts_pass_through_and_only_the_name_is_scrambled(self):
        reply = {
            "channel": "telegram",
            "health": "CURRENT",
            "auth": "READY",
            "error": None,
            "observed_at": "2026-10-14T15:00:00Z",
            "chats_main": 40,
            "unread_messages": 30,
            "unread_messages_unmuted": 7,
            "unread_chats": 6,
            "unread_chats_unmuted": 3,
            "latest_state": "PRESENT",
            "latest_name": "Bob Example",
            "latest_at": "2026-10-14T14:00:00Z",
            "scope": "x",
        }
        block = ep.telegram(KEY, self.adapter(reply))
        self.assertEqual(set(block), card.TG_KEYS)
        self.assertNotIn("Bob", json.dumps(block))
        self.assertEqual(card.unscramble(KEY, block["latest"]), "Bob Example")
        self.assertEqual(block["unread_messages_unmuted"], 7)

    def test_a_missing_failed_or_degraded_adapter_is_unavailable_never_zero(self):
        degraded = {
            "channel": "telegram",
            "health": "DEGRADED",
            "auth": "READY",
            "error": "NOT_CONNECTED",
            "unread_messages": 3,
            "latest_state": "PRESENT",
            "latest_name": "Bob Example",
        }
        for run in (
            self.adapter("", 1),
            self.adapter("not json"),
            self.adapter({"channel": "other"}),
            self.adapter(degraded),
            lambda argv, **kw: (_ for _ in ()).throw(OSError()),
        ):
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
        s = {
            "channel": "whatsapp",
            "health": "CURRENT",
            "observed_at": "2026-10-14T15:00:00Z",
            "chats_known": 80,
            "unread_messages": 12,
            "unread_messages_unmuted": 9,
            "unread_chats": 5,
            "unread_chats_unmuted": 4,
            "latest_state": "PRESENT",
            "latest_name": "Carol Example",
        }
        s.update(change)
        return s

    def test_counts_pass_through_and_only_the_name_is_scrambled(self):
        block = ep.whatsapp(KEY, self.serve_once(self.snapshot()))
        self.assertEqual(set(block), card.TG_KEYS)
        self.assertNotIn("Carol", json.dumps(block))
        self.assertEqual(
            (block["unread_messages_unmuted"], card.unscramble(KEY, block["latest"])), (9, "Carol Example")
        )

    def test_not_linked_disconnected_missing_or_malformed_is_unavailable_never_zero(self):
        for path in (
            self.serve_once(self.snapshot(health="NOT_LINKED", unread_messages=0)),
            self.serve_once(self.snapshot(health="DISCONNECTED")),
            self.serve_once(b"not json"),
            self.serve_once(self.snapshot(channel="telegram")),
            "/nonexistent/attention.sock",
        ):
            block = ep.whatsapp(KEY, path)
            self.assertEqual(block["health"], "UNAVAILABLE")
            self.assertTrue(all(block[k] is None for k in card.TG_COUNTS))
            self.assertIsNone(block["latest"])


class Contract(unittest.TestCase):
    def payload(self, **change):
        p = {
            "schema": card.SCHEMA,
            "observed_at": "2026-10-14T15:00:00Z",
            "health": "CURRENT",
            "unread_7d": 5,
            "latest_state": "PRESENT",
            "latest": ep.scramble(KEY, NAME),
            "telegram": tg_block(KEY),
            "whatsapp": tg_block(KEY, "Carol Example"),
        }
        p.update(change)
        return json.dumps(p).encode()

    def test_tampered_stale_and_off_contract_answers_are_refused(self):
        sealed = ep.scramble(KEY, NAME)
        flipped = {**sealed, "box": ("%02x" % (int(sealed["box"][:2], 16) ^ 1)) + sealed["box"][2:]}
        for raw in (
            b"",
            self.payload(latest=flipped),
            self.payload(observed_at="2026-10-14T14:00:00Z"),
            self.payload(unread_7d=-1),
            self.payload(unread_7d=True),
            self.payload(latest_state="NONE"),
            self.payload(schema="x"),
            b"[]",
            b"x" * 5000,
        ):
            with self.assertRaises(card.CommsError, msg=raw[:40]):
                card.validate(raw, KEY, NOW)

    def test_tampered_or_inconsistent_telegram_blocks_are_refused(self):
        bad = (
            dict(tg_block(KEY), health="DEGRADED"),  # a non-current block may not carry counts or a name
            dict(tg_block(KEY), unread_messages=-1),
            dict(tg_block(KEY), latest_state="NONE"),
            {k: v for k, v in tg_block(KEY).items() if k != "auth"},
            dict(tg_block(KEY), latest={**ep.scramble(KEY, NAME), "tag": "00" * 16}),
        )
        for block in bad:
            with self.assertRaises(card.CommsError):
                card.validate(self.payload(telegram=block), KEY, NOW)

    def test_render(self):
        lines, heading = card.render({"messages": (45, "PRESENT", NAME), "telegram": (150, "NONE", None)}, NOW)
        self.assertEqual(
            lines,
            {"messages": ("Messages (45)", "Latest: Alice Example"), "telegram": ("Telegram (99+)", "Latest: none")},
        )
        self.assertEqual(heading, "Communications Attention (10/14 · 10:00 AM CT)")
        self.assertEqual(card.render(card.UNAVAILABLE, NOW)[0]["messages"], ("Messages (—)", "Latest: unavailable"))


class Card(unittest.TestCase):
    ENV = {
        "IMESSAGE_ALERTS_HOST": "100.64.0.9",
        "JIRA_CARD_BLOCK_ID": "jira",
        "CALENDAR_CARD_BLOCK_ID": "attn",
        "BILLS_CARD_BLOCK_ID": "bills",
        "AMAZON_CARD_BLOCK_ID": "amz",
        "NOTION_JIRA_TOKEN": "unused",
    }

    def run_card(self, report, live=True, answer=None, env=None, tasks=None):
        answer = answer or (
            lambda host, key: json.dumps(
                {
                    "schema": card.SCHEMA,
                    "observed_at": "2026-10-14T15:00:00Z",
                    "health": "CURRENT",
                    "unread_7d": 45,
                    "latest_state": "PRESENT",
                    "latest": ep.scramble(key, NAME),
                    "telegram": tg_block(key),
                    "whatsapp": tg_block(key, "Carol Example"),
                }
            ).encode()
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            counts = card.run(0, live, env or self.ENV, NOW, answer, report, keygen=lambda n: KEY, tasks=tasks)
        return counts, out.getvalue()

    def test_live_writes_only_the_messages_and_telegram_rows_their_latest_lines_and_the_time(self):
        report = Report()
        others = {b: report.plain(b) for b in ("l3", "r3", "attn", "bills")}
        counts, log = self.run_card(report)
        self.assertEqual(
            (
                counts["unread_7d"],
                counts["latest"],
                counts["telegram_latest"],
                counts["telegram_unread_messages_unmuted"],
                counts["written"],
            ),
            (45, "present", "present", 7, 7),
        )
        self.assertEqual([p[0] for p in report.patches], ["l0", "r0", "l1", "r1", "l2", "r2", "head"])
        self.assertEqual((report.plain("l2"), report.plain("r2")), (":whatsapp: WhatsApp (7)", "Latest: Carol Example"))
        self.assertEqual((report.plain("l1"), report.plain("r1")), (":telegram: Telegram (7)", "Latest: Bob Example"))
        self.assertEqual(
            report.plain("l0"), ":messages: Messages (45)"
        )  # Jim's icon and the space are sent back as read
        self.assertEqual(report.blocks["l0"]["paragraph"]["rich_text"][0]["mention"]["custom_emoji"]["id"], "e-msg")
        self.assertEqual(
            report.blocks["l0"]["paragraph"]["rich_text"][2]["text"]["link"]["url"],
            "https://example.com/launch/messages/",
        )
        self.assertEqual(report.plain("r0"), "Latest: Alice Example")
        self.assertEqual(report.plain("head"), "Communications Attention (10/14 · 10:00 AM CT)")
        self.assertEqual({b: report.plain(b) for b in others}, others)
        self.assertNotIn("Alice", log)
        self.assertNotIn("Bob", log)
        self.assertNotIn("Carol", log)

    def test_a_restarted_whatsapp_source_keeps_the_name_already_in_notion(self):
        report = Report()
        report.blocks["r2"]["paragraph"]["rich_text"] = [
            {
                "type": "text",
                "plain_text": "Latest: Carol Example",
                "text": {"content": "Latest: Carol Example", "link": None},
            }
        ]
        wa = dict(tg_block(KEY), latest_state="UNAVAILABLE", latest=None)
        answer = lambda host, key: json.dumps(
            {
                "schema": card.SCHEMA,
                "observed_at": "2026-10-14T15:00:00Z",
                "health": "CURRENT",
                "unread_7d": 45,
                "latest_state": "PRESENT",
                "latest": ep.scramble(key, NAME),
                "telegram": tg_block(key),
                "whatsapp": dict(wa, latest=None),
            }
        ).encode()
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
            self.assertEqual(
                (report.plain("l0"), report.plain("r0")), (":messages: Messages (—)", "Latest: unavailable")
            )
            self.assertEqual(
                (report.plain("l1"), report.plain("r1")), (":telegram: Telegram (—)", "Latest: unavailable")
            )
            self.assertEqual(
                (report.plain("l2"), report.plain("r2")), (":whatsapp: WhatsApp (—)", "Latest: unavailable")
            )
            self.assertIn("source refused", log)

    def test_a_missing_or_ambiguous_callout_is_not_owned(self):
        report = Report()
        report.add("comms2", "callout", "col", [])
        report.add(
            "head2",
            "heading_3",
            "comms2",
            [{"type": "text", "plain_text": "Communications Attention", "text": {"content": "x", "link": None}}],
        )
        with self.assertRaises(card.CommsError):
            self.run_card(report)
        self.assertEqual(report.patches, [])
        with self.assertRaises(card.CommsError):
            self.run_card(Report(), env={**self.ENV, "CALENDAR_CARD_BLOCK_ID": ""})

    def add_row(self, report, name, links=False, own_links=False):
        """Another two-column row in the callout, as Jim added below a divider (Reminders | Todoist)"""
        report.add(name, "column_list", "comms")
        report.add(name + "L", "column", name)
        report.add(name + "R", "column", name)
        report.add(
            name + "l",
            "paragraph",
            name + "L",
            [text("Reminders (-)", LAUNCH % "reminders" if own_links else LAUNCH % "messages" if links else None)],
        )
        report.add(
            name + "r", "paragraph", name + "R", [text("Todoist (-)", LAUNCH % "todoist" if own_links else None)]
        )

    def test_another_row_of_jims_own_is_left_alone_and_the_channel_rows_are_still_written(self):
        report = Report()
        report.add("div", "divider", "comms")
        self.add_row(report, "extra")
        counts, _ = self.run_card(report)
        self.assertEqual([p[0] for p in report.patches], ["l0", "r0", "l1", "r1", "l2", "r2", "head"])
        self.assertEqual((report.plain("extral"), report.plain("extrar")), ("Reminders (-)", "Todoist (-)"))
        self.assertEqual(counts["written"], 7)

    def test_the_reminders_label_and_the_todoist_link_are_left_exactly_as_they_are_and_only_the_todoist_count_text_changes(
        self,
    ):
        report = Report()
        report.add("div", "divider", "comms")
        self.add_row(report, "extra", own_links=True)
        reminders = copy.deepcopy(report.blocks["extral"])
        link = report.blocks["extrar"]["paragraph"]["rich_text"][0]["text"]["link"]
        counts, _ = self.run_card(report)  # no Todoist credential: never zero, never a guess
        self.assertEqual(
            [p[0] for p in report.patches], ["l0", "r0", "l1", "r1", "l2", "r2", "head", "extrar"]
        )  # the channel rows are still written, then only the Todoist label
        self.assertEqual(report.blocks["extral"], reminders)
        self.assertEqual(report.blocks["extrar"]["paragraph"]["rich_text"][0]["text"]["link"], link)
        self.assertEqual((report.plain("extrar"), counts["todoist"]), ("Todoist (—)", "unavailable"))
        self.assertEqual(counts["written"], 8)

    TODOIST_ENV = {"FOLLOWUP_TODOIST_PERSONAL_TOKEN": "unused"}

    def todoist_row(self, report):
        report.add("div", "divider", "comms")
        self.add_row(report, "extra", own_links=True)

    def test_the_todoist_launcher_reads_its_live_count_and_keeps_its_link_and_label(self):
        """step 4d: `Todoist (-)` becomes `Todoist (13)`"""
        report = Report()
        self.todoist_row(report)
        reminders = copy.deepcopy(report.blocks["extral"])
        counts, log = self.run_card(report, env={**self.ENV, **self.TODOIST_ENV}, tasks=lambda limit: [{}] * 13)
        self.assertEqual((report.plain("extrar"), report.plain("extral")), ("Todoist (13)", "Reminders (-)"))
        self.assertEqual(
            report.blocks["extrar"]["paragraph"]["rich_text"][0]["text"]["link"]["url"], LAUNCH % "todoist"
        )
        self.assertEqual(report.blocks["extral"], reminders)
        self.assertEqual([p[0] for p in report.patches], ["l0", "r0", "l1", "r1", "l2", "r2", "head", "extrar"])
        self.assertEqual((counts["todoist"], counts["written"]), (13, 8))

    def test_more_than_ninety_nine_open_tasks_read_99_plus_and_an_unchanged_count_writes_nothing_more(self):
        report = Report()
        self.todoist_row(report)
        self.run_card(report, env={**self.ENV, **self.TODOIST_ENV}, tasks=lambda limit: [{}] * 150)
        self.assertEqual(report.plain("extrar"), "Todoist (99+)")
        mark = len(report.patches)
        counts, _ = self.run_card(report, env={**self.ENV, **self.TODOIST_ENV}, tasks=lambda limit: [{}] * 150)
        self.assertNotIn("extrar", [p[0] for p in report.patches[mark:]])
        self.assertEqual(counts["written"], 7)

    def broken(self, limit):
        raise RuntimeError("TODOIST_HTTP_401")

    def test_before_any_verified_count_a_failed_read_shows_unavailable_never_zero_and_leaks_nothing(self):
        for env, tasks in ((self.ENV, None), ({**self.ENV, **self.TODOIST_ENV}, self.broken)):
            report = Report()
            self.todoist_row(report)
            counts, log = self.run_card(report, env=env, tasks=tasks)
            self.assertEqual(report.plain("extrar"), "Todoist (—)")
            self.assertEqual(counts["todoist"], "unavailable")
            self.assertNotIn("unused", log)
        self.assertIn("TODOIST_UNREADABLE", log)
        self.assertNotIn("401", log)

    def test_a_failed_read_keeps_the_last_verified_count_marked_stale_and_the_next_good_read_clears_it(self):
        env = {**self.ENV, **self.TODOIST_ENV}
        report = Report()
        self.todoist_row(report)
        self.run_card(report, env=env, tasks=lambda limit: [{}] * 14)
        self.assertEqual(report.plain("extrar"), "Todoist (14)")
        counts, _ = self.run_card(report, env=env, tasks=self.broken)
        self.assertEqual((report.plain("extrar"), counts["todoist"]), ("Todoist (14 · STALE)", "stale"))
        mark = len(report.patches)
        counts, _ = self.run_card(report, env=env, tasks=self.broken)  # still failing: the same text is not rewritten
        self.assertEqual(report.plain("extrar"), "Todoist (14 · STALE)")
        self.assertNotIn("extrar", [p[0] for p in report.patches[mark:]])
        counts, _ = self.run_card(report, env=env, tasks=lambda limit: [{}] * 15)
        self.assertEqual((report.plain("extrar"), counts["todoist"]), ("Todoist (15)", 15))

    def test_a_stale_overflow_keeps_99_plus_and_an_old_dash_or_hyphen_placeholder_is_never_a_count(self):
        env = {**self.ENV, **self.TODOIST_ENV}
        report = Report()
        self.todoist_row(report)
        self.run_card(report, env=env, tasks=lambda limit: [{}] * 150)
        self.run_card(report, env=env, tasks=self.broken)
        self.assertEqual(report.plain("extrar"), "Todoist (99+ · STALE)")
        for label in ("Todoist (-)", "Todoist (—)", "Todoist", "Todoist (x · STALE)"):
            self.assertIsNone(card.kept_count(label))

    def test_only_top_level_tasks_count_subtasks_and_header_items_never_do(self):
        rows = [
            {"id": "1", "content": "Parent", "parent_id": None},
            {"id": "2", "content": "Child", "parent_id": "1"},
            {"id": "3", "content": "* Header line", "parent_id": None},
            {"id": "4", "content": "Undated task"},
            {"id": "5", "content": "Recurring", "due": {"is_recurring": True}},
        ]
        report = Report()
        self.todoist_row(report)
        counts, _ = self.run_card(report, env={**self.ENV, **self.TODOIST_ENV}, tasks=lambda limit: rows)
        self.assertEqual((report.plain("extrar"), counts["todoist"]), ("Todoist (3)", 3))

    def test_a_listing_that_filled_the_client_cap_is_truncated_so_it_is_stale_not_a_count(self):
        report = Report()
        self.todoist_row(report)
        counts, _ = self.run_card(report, env={**self.ENV, **self.TODOIST_ENV}, tasks=lambda limit: [{}] * limit)
        self.assertEqual((report.plain("extrar"), counts["todoist"]), ("Todoist (—)", "unavailable"))

    DASH_ENV = {"DASHBOARD_TODOIST_BLOCK_ID": "dash"}

    def dash_run(self, report, tasks, env=None):
        return self.run_card(report, env={**self.ENV, **self.TODOIST_ENV, **self.DASH_ENV, **(env or {})}, tasks=tasks)

    def test_the_dashboard_label_shows_the_same_count_keeps_its_icon_and_space_and_gets_the_launcher_link(self):
        report = Report()
        self.todoist_row(report)
        report.add_dashboard()
        icon = copy.deepcopy(report.blocks["dash"]["paragraph"]["rich_text"][0])
        counts, _ = self.dash_run(report, lambda limit: [{}] * 13)
        self.assertEqual((report.plain("dash"), report.plain("extrar")), (":todoist: Todoist (13)", "Todoist (13)"))
        self.assertEqual(report.blocks["dash"]["paragraph"]["rich_text"][0], icon)
        self.assertEqual(report.blocks["dash"]["paragraph"]["rich_text"][1]["plain_text"], " Todoist (13)")
        self.assertEqual(report.blocks["dash"]["paragraph"]["rich_text"][1]["text"]["link"]["url"], LAUNCH % "todoist")
        self.assertEqual((counts["dashboard"], counts["todoist"], counts["written"]), ("written", 13, 9))
        mark = len(report.patches)
        counts, _ = self.dash_run(report, lambda limit: [{}] * 13)  # an unchanged count and link write nothing more
        self.assertNotIn("dash", [p[0] for p in report.patches[mark:]])

    def test_both_labels_go_stale_together_and_recover_together(self):
        report = Report()
        self.todoist_row(report)
        report.add_dashboard()
        self.dash_run(report, lambda limit: [{}] * 14)
        counts, _ = self.dash_run(report, self.broken)
        self.assertEqual(
            (report.plain("dash"), report.plain("extrar")),
            (":todoist: Todoist (14 · STALE)", "Todoist (14 · STALE)"),
        )
        self.assertEqual(counts["dashboard"], "written")
        self.dash_run(report, lambda limit: [{}] * 15)
        self.assertEqual((report.plain("dash"), report.plain("extrar")), (":todoist: Todoist (15)", "Todoist (15)"))

    def test_an_unset_dashboard_target_is_not_configured_and_nothing_on_it_is_read_or_written(self):
        report = Report()
        self.todoist_row(report)
        report.add_dashboard()
        before = copy.deepcopy(report.blocks["dash"])
        counts, _ = self.run_card(report, env={**self.ENV, **self.TODOIST_ENV}, tasks=lambda limit: [{}] * 3)
        self.assertEqual((counts["dashboard"], report.blocks["dash"]), ("not_configured", before))

    def test_a_dashboard_target_of_the_wrong_shape_or_link_writes_nothing_there_and_the_run_is_degraded(self):
        shapes = {
            "heading": lambda r: r.add_dashboard(kind="heading_3"),
            "two labels": lambda r: r.add_dashboard(extra=(text("Todoist (2)"),)),
            "foreign link": lambda r: r.add_dashboard(link="https://example.com/elsewhere/"),
            "no label": lambda r: r.add_dashboard(label=" something else"),
        }
        for name, build in shapes.items():
            report = Report()
            self.todoist_row(report)
            build(report)
            before = copy.deepcopy(report.blocks["dash"])
            with self.assertRaises(card.CommsError, msg=name) as error:
                self.dash_run(report, lambda limit: [{}] * 3)
            self.assertEqual(str(error.exception), "COMMS_TODOIST_DIVERGED", name)
            self.assertEqual(report.blocks["dash"], before, name)
            self.assertEqual(report.plain("extrar"), "Todoist (3)", name)  # the Daily Report label is still written

    def test_a_failed_dashboard_write_is_degraded_never_fresh(self):
        class Failing(Report):
            def call_once(self, method, path, body=None, **kw):
                if path.strip("/").endswith("dash"):
                    raise card.NotionError("NOTION_HTTP_500")
                return super().call_once(method, path, body, **kw)

        report = Failing()
        self.todoist_row(report)
        report.add_dashboard()
        with self.assertRaises(card.CommsError) as error:
            self.dash_run(report, lambda limit: [{}] * 3)
        self.assertEqual(str(error.exception), "COMMS_TODOIST_DIVERGED")

    def test_the_callout_is_found_in_another_column_than_the_calendar_card(self):
        report = Report()
        report.add("col2", "column", "root")
        report.blocks["col2"]["parent"] = {"type": "page_id", "page_id": "root"}
        report.kids["col"].remove("comms")
        report.kids["col2"] = ["comms"]
        report.blocks["comms"]["parent"] = {"type": "block_id", "block_id": "col2"}
        counts, _ = self.run_card(report)
        self.assertEqual(counts["written"], 7)

    def test_two_todoist_launchers_are_ambiguous_and_nothing_is_written(self):
        report = Report()
        self.todoist_row(report)
        report.add("more", "paragraph", "extraR", [text("Todoist (-)", LAUNCH % "todoist")])
        with self.assertRaises(card.CommsError):
            self.run_card(report, env={**self.ENV, **self.TODOIST_ENV}, tasks=lambda limit: [])
        self.assertEqual(report.patches, [])

    def test_two_rows_with_launch_links_are_ambiguous_and_nothing_is_written(self):
        report = Report()
        self.add_row(report, "extra", links=True)
        with self.assertRaises(card.CommsError) as error:
            self.run_card(report)
        self.assertEqual(str(error.exception), "CARD_NOT_OWNED")
        self.assertEqual(report.patches, [])

    def test_another_cards_write_during_ours_no_longer_fails_the_run(self):
        class Busy(Report):
            def call_once(self, method, path, body=None):
                result = super().call_once(method, path, body)
                self.blocks["bills"]["callout"] = {"rich_text": [text("Bills (another card wrote this)")]}
                return result

        counts, _ = self.run_card(Busy())
        self.assertGreater(counts["written"], 0)

    def test_a_write_into_another_cards_block_is_refused_before_it_is_sent_and_nothing_changes(self):
        """mutation proof."""
        report = Report()
        before = deepcopy(report.blocks["attn"])

        def buggy_writer(client, heading, blocks, lines, heading_text):
            client.call_once(
                "PATCH",
                "/blocks/attn",
                {"callout": {"rich_text": [{"type": "text", "text": {"content": "overwritten"}}]}},
            )

        with mock.patch.object(card, "write", buggy_writer):
            with self.assertRaises(card.CommsError) as error:
                self.run_card(report)
        self.assertEqual(str(error.exception), "WRITE_OUT_OF_SCOPE")
        self.assertEqual(report.blocks["attn"], before)
        self.assertEqual(report.patches, [])


if __name__ == "__main__":
    unittest.main()
