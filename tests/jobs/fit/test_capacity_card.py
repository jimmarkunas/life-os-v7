"""LIFE-77 D228: the AI Capacity card. Fakes only: Oracle's limits reply and the Notion client. No model, no network."""
from datetime import datetime
import json
import unittest

from lifeos.jobs.fit import capacity_card as card
from lifeos.jobs.fit import edge

NOW = datetime(2026, 10, 20, 0, 30, tzinfo=card.TZ)
READING = {"read_at": "2026-10-20T05:30:00+00:00",
           "pools": [{"provider": "openai", "stop": 50, "windows": [{"label": "5h", "used": 40, "reset_at": "2026-10-20T07:12:19+00:00"},
                                                                    {"label": "Week", "used": 18, "reset_at": "2026-10-24T21:26:54+00:00"}]},
                     {"provider": "openrouter", "windows": [{"label": "Monthly key budget", "used": 80, "reset_at": None}]}],
           "unavailable": ["groq"], "safety_cap": {"runs_today": 20, "limit": 5000}}


class Render(unittest.TestCase):
    def test_the_approved_layout_tightest_first_remaining_large_used_as_a_bar_resets_in_central(self):
        text, table = card.render(READING, NOW)
        self.assertEqual(text, "AI Capacity · Updated 12:30 AM CT · 1 near limit · 0 exhausted · our Cloud Fit guard 20/5000 runs today (not a provider limit)")
        self.assertEqual(table[0], card.HEADER)
        self.assertEqual(table[1], ["OpenRouter · Monthly key budget ◀ tightest", "20%", "▰▰▰▰▰▰▰▰▱▱ 80%", "Monthly key budget", "—", "NEAR LIMIT"])
        self.assertEqual(table[2], ["Codex allowance · 5-hour", "60%", "▰▰▰▰▱▱▱▱▱▱ 40% · Cloud Fit stops at 50%", "5 hours", "Tue Oct 20, 2:12 AM CDT", "AVAILABLE"])
        self.assertEqual(table[3][0:2], ["Codex allowance · weekly", "82%"])
        self.assertEqual(table[4], ["Groq", "—", "▱▱▱▱▱▱▱▱▱▱ not exposed", "—", "—", "UNAVAILABLE"])           # unknown is never an empty or a full bar
        self.assertEqual(table[5:], [[""] * card.COLS] * 2)
        self.assertEqual((len(table), {len(r) for r in table}), (card.ROWS, {card.COLS}))

    def test_exhausted_and_the_reserve_are_named(self):
        reading = {**READING, "pools": [{"provider": "openai", "stop": 50, "windows": [{"label": "5h", "used": 55, "reset_at": None}, {"label": "Week", "used": 100, "reset_at": None}]}],
                   "unavailable": [], "safety_cap": None}
        text, table = card.render(reading, NOW)
        self.assertEqual([table[1][5], table[2][5]], ["EXHAUSTED", "RESERVE REACHED"])
        self.assertEqual(text, "AI Capacity · Updated 12:30 AM CT · 1 near limit · 1 exhausted")

    def test_more_pools_than_rows_are_counted_not_dropped_silently(self):
        reading = {**READING, "unavailable": ["groq", "google", "ollama", "a", "b"]}
        text, table = card.render(reading, NOW)
        self.assertEqual(len(table), card.ROWS)
        self.assertTrue(text.endswith("· 2 more not shown"))


ON = {"V7_AI_CAPACITY_CARD": "on", "ATTENTION_CARD_BLOCK_ID": "att"}


def _rt(text):
    return [{"plain_text": text, "text": {"content": text}}] if text else []


class FakeNotion:
    """The Daily Report column as the card sees it: the Attention callout, the Oracle Cloud callout (its heading first) and, once installed, the card
    (one callout holding one 6 x 7 table whose first row is the header)."""
    def __init__(self, installed=True, rows=card.ROWS, cols=card.COLS, oracle=True, cards=1):
        self.cols, self.patches, self.inserted = cols, [], []
        self.order = ["att"] + (["ora"] if oracle else []) + [f"cap{i}" for i in range(cards if installed else 0)]
        self.text = {f"cap{i}": "" for i in range(cards)}
        self.rows = {f"cap{i}": {f"cap{i}-r{j}": (list(card.HEADER) if j == 0 else [""] * cols) for j in range(rows)} for i in range(cards)}

    def call(self, method, path, body=None):
        parts = path.split("?")[0].split("/")
        if method == "PATCH" and path == "/blocks/col/children":
            self.inserted.append(body)
            assert body["after"] == "ora"
            self.order.insert(self.order.index("ora") + 1, "cap0")
            return {"results": [{"type": "callout", "id": "cap0"}]}
        if method == "PATCH":
            self.patches.append(path)
            bid = parts[2]
            if "table_row" in body:
                cap = bid.split("-")[0]
                self.rows[cap][bid] = ["".join(x["text"]["content"] for x in cell) for cell in body["table_row"]["cells"]]
            else:
                self.text[bid] = body["callout"]["rich_text"][0]["text"]["content"]
            return {}
        bid = parts[2]
        if len(parts) == 3:
            if bid == "att":
                return {"type": "callout", "id": "att", "parent": {"type": "block_id", "block_id": "col"}}
            return {"type": "callout", "id": bid, "callout": {"rich_text": _rt(self.text.get(bid, ""))}}
        if bid == "col":
            return {"results": [{"type": "callout", "id": b} for b in self.order], "has_more": False}
        if bid == "att":
            return {"results": [{"type": "paragraph", "id": "p"}], "has_more": False}
        if bid == "ora":
            return {"results": [{"type": "heading_3", "id": "h", "heading_3": {"rich_text": _rt("Oracle Cloud (Updated 6:05 AM CT)")}}], "has_more": False}
        if bid.startswith("cap") and not bid.endswith("-t"):
            return {"results": [{"type": "table", "id": bid + "-t", "table": {"table_width": self.cols}}], "has_more": False}
        if bid.endswith("-t"):
            cap = bid[:-2]
            return {"results": [{"type": "table_row", "id": rid, "table_row": {"cells": [_rt(c) for c in cells]}} for rid, cells in self.rows[cap].items()], "has_more": False}
        raise AssertionError(path)


class Run(unittest.TestCase):
    def test_off_unless_jim_switched_it_on(self):
        for environ in ({}, {"ATTENTION_CARD_BLOCK_ID": "att"}, {**ON, "V7_AI_CAPACITY_CARD": "off"}, {"V7_AI_CAPACITY_CARD": "on"}):
            out = card.run(0, True, environ=environ, client=object(), read=lambda: (_ for _ in ()).throw(AssertionError("no read")))
            self.assertEqual(out["status"], "not_configured", environ)

    def test_the_first_live_run_places_the_card_right_after_the_oracle_cloud_card_and_fills_it(self):
        notion = FakeNotion(installed=False)
        self.assertEqual(card.run(0, False, environ=ON, client=notion, read=lambda: READING, now=NOW)["status"], "would_install")   # a dry run inserts nothing
        self.assertEqual(notion.inserted, [])
        out = card.run(0, True, environ=ON, client=notion, read=lambda: READING, now=NOW)
        self.assertEqual((out["status"], out["installed"]), ("fresh", True))
        self.assertEqual(notion.order, ["att", "ora", "cap0"])
        made = notion.inserted[0]["children"][0]
        self.assertEqual((made["type"], made["callout"]["children"][0]["table"]["table_width"], len(made["callout"]["children"][0]["table"]["children"])), ("callout", 6, 7))
        self.assertEqual(notion.rows["cap0"]["cap0-r1"][5], "NEAR LIMIT")
        again = card.run(0, True, environ=ON, client=notion, read=lambda: READING, now=NOW)
        self.assertEqual((again["installed"], again["rows_written"], again["text_written"], len(notion.inserted)), (False, 0, False, 1))     # never placed twice

    def test_without_the_oracle_cloud_card_nothing_is_placed_anywhere_else(self):
        with self.assertRaises(card.CardError) as ctx:
            card.run(0, True, environ=ON, client=FakeNotion(installed=False, oracle=False), read=lambda: READING, now=NOW)
        self.assertEqual(str(ctx.exception), "CAPACITY_ORACLE_CARD_NOT_FOUND")

    def test_two_cards_are_refused(self):
        with self.assertRaises(card.CardError) as ctx:
            card.run(0, True, environ=ON, client=FakeNotion(cards=2), read=lambda: READING, now=NOW)
        self.assertEqual(str(ctx.exception), "CAPACITY_CARD_AMBIGUOUS")

    def test_a_fresh_reading_is_written_in_place_and_read_back(self):
        notion = FakeNotion()
        out = card.run(0, True, environ=ON, client=notion, read=lambda: READING, now=NOW)
        self.assertEqual((out["status"], out["installed"], out["rows_written"], out["text_written"]), ("fresh", False, 4, True))   # the header is already right
        again = card.run(0, True, environ=ON, client=notion, read=lambda: READING, now=NOW)
        self.assertEqual((again["rows_written"], again["text_written"]), (0, False))

    def test_a_failed_read_says_degraded_keeps_the_table_and_fails_the_stage_by_name(self):
        notion = FakeNotion()
        card.run(0, True, environ=ON, client=notion, read=lambda: READING, now=NOW)
        before = {k: dict(v) for k, v in notion.rows.items()}

        def broken():
            raise edge.EdgeError("FIT_EDGE_NO_REPLY")
        out = card.run(0, True, environ=ON, client=notion, read=broken, now=NOW)
        self.assertEqual((out["status"], out["code"], out["failed"]), ("degraded", "FIT_EDGE_NO_REPLY", 1))
        self.assertEqual(notion.text["cap0"], "AI Capacity · DEGRADED · Oracle limits read failed at 12:30 AM CT (FIT_EDGE_NO_REPLY) · showing the last reading")
        self.assertEqual(notion.rows, before)

    def test_a_card_that_lost_its_shape_is_refused_and_nothing_is_written(self):
        notion = FakeNotion(rows=6)
        with self.assertRaises(card.CardError):
            card.run(0, True, environ=ON, client=notion, read=lambda: READING, now=NOW)
        self.assertEqual(notion.patches, [])

    def test_a_dry_run_writes_nothing(self):
        notion = FakeNotion()
        out = card.run(0, False, environ=ON, client=notion, read=lambda: READING, now=NOW)
        self.assertEqual((out["rows_written"], out["rows_changed"], notion.patches), (0, 4, []))


class Limits(unittest.TestCase):
    def reply(self, **override):
        def send(host, data, wait=None):
            body = json.loads(data.split(b"\n", 1)[1])
            self.assertLessEqual(set(body), {"limits", "nonce", "backlog"})                                   # never a request, never a profile
            out = {"schema": "lifeos.fit.v1", "status": "LIMITS", "nonce": body["nonce"], "read_at": "2026-10-20T05:30:00+00:00",
                   "pools": READING["pools"], "unavailable": ["groq"], "safety_cap": {"runs_today": 20, "limit": 5000}}
            out.update(override)
            return json.dumps(out).encode()
        return send

    def test_a_good_reply_is_kept_to_labels_and_numbers(self):
        got = edge.limits("oracle.example", send=self.reply(extra="dropped"))
        self.assertEqual((got["pools"], got["unavailable"], got["safety_cap"]), (READING["pools"], ["groq"], {"runs_today": 20, "limit": 5000}))
        self.assertNotIn("extra", got)

    def test_anything_off_contract_is_a_fixed_code(self):
        for override, code in (({"status": "OK"}, "FIT_EDGE_OFF_CONTRACT"), ({"nonce": "0" * 32}, "FIT_EDGE_NONCE_MISMATCH"),
                               ({"pools": [{"provider": "Open AI", "windows": []}]}, "FIT_EDGE_LIMITS_OFF_CONTRACT"),
                               ({"pools": [{"provider": "openai", "windows": [{"label": "5h", "used": 140, "reset_at": None}]}]}, "FIT_EDGE_LIMITS_OFF_CONTRACT"),
                               ({"unavailable": "groq"}, "FIT_EDGE_LIMITS_OFF_CONTRACT")):
            with self.assertRaises(edge.EdgeError) as ctx:
                edge.limits("oracle.example", send=self.reply(**override))
            self.assertEqual(str(ctx.exception), code, override)
        with self.assertRaises(edge.EdgeError) as ctx:
            edge.limits("", send=self.reply())
        self.assertEqual(str(ctx.exception), "FIT_EDGE_NO_HOST")


def with_estimate(proposed, five, week, used_5h=0, used_week=1):
    pool = {"provider": "openai", "stop": 50, "windows": [{"label": "5h", "used": used_5h, "reset_at": None}, {"label": "Week", "used": used_week, "reset_at": None}],
            "estimate": {"proposed": proposed, "reported_at": "2026-10-20T05:40:00+00:00", "windows": [{"label": "5h", **five}, {"label": "Week", **week}]}}
    return {**READING, "pools": [pool], "unavailable": []}


class ProposedUsage(unittest.TestCase):
    """LIFE-77: the preview's proposed-usage line, from Oracle's estimate for the backlog V7 reported."""

    def test_a_backlog_that_fits_under_the_stop(self):
        reading = with_estimate(300, {"basis_calls": 44, "per_call_below": 0.0227, "proposed_percent_at_most": 6.82, "calls_left_at_least": 2200},
                                {"basis_calls": 44, "per_call_percent": 0.02, "proposed_percent": 6, "calls_left": 2450})
        text, _ = card.render(reading, NOW)
        self.assertEqual(text.split("\n")[1], "Proposed usage · Cloud Fit backlog (300 jobs): at most 6.82% of the 5-hour window and about 6% of the weekly window"
                                              " (ESTIMATED from 44 metered calls) · fits under the 50% stop")

    def test_a_backlog_that_would_reach_the_stop_says_how_much_fits(self):
        reading = with_estimate(2692, {"basis_calls": 44, "per_call_below": 0.0227, "proposed_percent_at_most": 61.18, "calls_left_at_least": 2200},
                                {"basis_calls": 44, "per_call_percent": 0.02, "proposed_percent": 53.84, "calls_left": 2450})
        line = card.render(reading, NOW)[0].split("\n")[1]
        self.assertTrue(line.startswith("Proposed usage · Cloud Fit backlog (2,692 jobs): at most 61.18% of the 5-hour window"))
        self.assertTrue(line.endswith("· about 2,200 calls fit before the 50% stop; the rest wait for the reset"))

    def test_nothing_measured_or_nothing_reported_means_no_line(self):
        self.assertEqual(len(card.render(with_estimate(10, {"basis_calls": 0}, {"basis_calls": 0}), NOW)[0].split("\n")), 1)
        self.assertEqual(len(card.render(READING, NOW)[0].split("\n")), 1)

    def test_oracles_estimate_keeps_numbers_only_and_anything_else_is_off_contract(self):
        est = {"proposed": 2692, "reported_at": "2026-10-20T05:40:00+00:00", "windows": [{"label": "5h", "basis_calls": 44, "per_call_below": 0.0227, "note": "dropped"}]}
        pools = [{**READING["pools"][0], "estimate": est}]
        got = edge.limits("oracle.example", send=Limits.reply(Limits(), pools=pools))
        self.assertEqual(got["pools"][0]["estimate"]["windows"], [{"label": "5h", "basis_calls": 44, "per_call_below": 0.0227}])
        for bad in ({**est, "proposed": -1}, {**est, "windows": [{"label": "5h", "basis_calls": "44"}]}, {**est, "windows": [{"label": "5h", "basis_calls": 1, "calls_left": -3}]}):
            with self.assertRaises(edge.EdgeError) as ctx:
                edge.limits("oracle.example", send=Limits.reply(Limits(), pools=[{**READING["pools"][0], "estimate": bad}]))
            self.assertEqual(str(ctx.exception), "FIT_EDGE_LIMITS_OFF_CONTRACT", bad)

    def test_the_backlog_count_goes_to_oracle_only_when_given(self):
        sent = []

        def send(host, data, wait=None):
            sent.append(json.loads(data.split(b"\n", 1)[1]))
            return Limits.reply(Limits())(host, data, wait)
        edge.limits("oracle.example", send=send)
        edge.limits("oracle.example", send=send, backlog=2692)
        self.assertEqual([set(b) for b in sent], [{"limits", "nonce"}, {"limits", "nonce", "backlog"}])
        self.assertEqual(sent[1]["backlog"], 2692)
