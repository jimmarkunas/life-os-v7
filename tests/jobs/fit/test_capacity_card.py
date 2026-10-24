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
    def test_each_line_has_a_bold_label_and_the_plain_text_is_exactly_the_text(self):
        text, _ = card.render(READING, NOW)
        segments = card.rich(text)
        self.assertEqual("".join(x["text"]["content"] for x in segments), text)                                  # the read-back compares plain text, so nothing may be added or lost
        bold = [x["text"]["content"] for x in segments if x.get("annotations", {}).get("bold")]
        self.assertEqual(bold, ["AI Capacity", "Codex allowance", "Cloud Fit guard"])

    def test_the_approved_layout_tightest_first_remaining_large_used_as_a_bar_resets_in_central(self):
        text, table = card.render(READING, NOW)
        self.assertEqual(text.split("\n")[0], "AI Capacity · Updated 12:30 AM CT · 1 near limit · 0 exhausted")
        self.assertIn("Cloud Fit guard · 20 of 5,000 runs today (our own limit, not a provider's)", text.split("\n"))
        self.assertTrue(text.split("\n")[1].startswith("Codex allowance · 5-hour "))                               # the Codex numbers come first, right under the headline
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
        self.assertEqual(text.split("\n")[0], "AI Capacity · Updated 12:30 AM CT · 1 near limit · 1 exhausted")
        self.assertEqual(text.split("\n")[1], "Codex allowance · 5-hour 55% used · weekly 100% used · Cloud Fit stops at 50%")

    def test_more_pools_than_rows_are_counted_not_dropped_silently(self):
        reading = {**READING, "unavailable": ["groq", "google", "ollama", "a", "b"]}
        text, table = card.render(reading, NOW)
        self.assertEqual(len(table), card.ROWS)
        self.assertIn("· 2 more not shown", text.split("\n")[0])


ON = {"V7_AI_CAPACITY_CARD": "on", "ATTENTION_CARD_BLOCK_ID": "att"}


def _rt(text):
    return [{"plain_text": text, "text": {"content": text}}] if text else []


class FakeNotion:
    """The Daily Report as the card sees it: the page's top level (the half-width columns, a divider, a trailing paragraph and, once installed, the card), and inside the left
    column the Attention callout and, until Jim's move is done, the card's old copy (one callout holding one 6 x 7 table whose first row is the header)."""
    def __init__(self, new=0, old=0, rows=card.ROWS, cols=card.COLS, divider=True):
        self.cols, self.patches, self.inserted, self.trashed = cols, [], [], []
        self.top = ["cl"] + (["div"] if divider else []) + ["par"] + [f"capN{i}" for i in range(new)]
        self.column = ["att"] + [f"capO{i}" for i in range(old)]
        ids = [f"capN{i}" for i in range(new)] + [f"capO{i}" for i in range(old)]
        self.text = {i: "" for i in ids}
        self.rows = {i: {f"{i}-r{j}": (list(card.HEADER) if j == 0 else [""] * cols) for j in range(rows)} for i in ids}

    def call(self, method, path, body=None):
        parts = path.split("?")[0].split("/")
        if method == "PATCH" and path == "/blocks/page/children":
            self.inserted.append(body)
            assert body["after"] == "div"                                  # directly under the last top-level divider, nowhere else
            self.top.insert(self.top.index("div") + 1, "capN0")
            self.text["capN0"] = ""
            self.rows["capN0"] = {f"capN0-r{j}": ([""] * card.COLS) for j in range(card.ROWS)}
            return {"results": [{"type": "callout", "id": "capN0"}]}
        if method == "PATCH":
            bid = parts[2]
            if body == {"in_trash": True}:
                self.trashed.append(bid)
                return {}
            self.patches.append(path)
            if "table_row" in body:
                cap = bid.split("-")[0]
                self.rows[cap][bid] = ["".join(x["text"]["content"] for x in cell) for cell in body["table_row"]["cells"]]
            else:
                self.text[bid] = "".join(x["text"]["content"] for x in body["callout"]["rich_text"])             # Notion joins the segments
            return {}
        bid = parts[2]
        if len(parts) == 3:
            if bid == "att":
                return {"type": "callout", "id": "att", "parent": {"type": "block_id", "block_id": "col"}}
            if bid == "col":
                return {"type": "column", "id": "col", "parent": {"type": "block_id", "block_id": "cl"}}
            if bid == "cl":
                return {"type": "column_list", "id": "cl", "parent": {"type": "page_id", "page_id": "page"}}
            return {"type": "callout", "id": bid, "callout": {"rich_text": _rt(self.text.get(bid, ""))}, "in_trash": bid in self.trashed}
        kinds = {"cl": "column_list", "div": "divider", "par": "paragraph"}
        if bid == "page":
            return {"results": [{"type": kinds.get(b, "callout"), "id": b} for b in self.top if b not in self.trashed], "has_more": False}
        if bid == "col":
            return {"results": [{"type": "callout", "id": b} for b in self.column if b not in self.trashed], "has_more": False}
        if bid == "att":
            return {"results": [{"type": "paragraph", "id": "p"}], "has_more": False}
        if bid in ("par", "div", "cl"):
            return {"results": [], "has_more": False}                      # not a capacity card: no children to own it by
        if bid.startswith("cap") and not bid.endswith("-t"):
            return {"results": [{"type": "table", "id": bid + "-t", "table": {"table_width": self.cols}}], "has_more": False}
        if bid.endswith("-t"):
            cap = bid[:-2]
            return {"results": [{"type": "table_row", "id": rid, "table_row": {"cells": [_rt(c) for c in cells]}} for rid, cells in self.rows[cap].items()], "has_more": False}
        raise AssertionError(path)


class Run(unittest.TestCase):
    def go(self, notion, live=True, read=lambda: READING):
        return card.run(0, live, environ=ON, client=notion, read=read, now=NOW)

    def test_off_unless_jim_switched_it_on(self):
        for environ in ({}, {"ATTENTION_CARD_BLOCK_ID": "att"}, {**ON, "V7_AI_CAPACITY_CARD": "off"}, {"V7_AI_CAPACITY_CARD": "on"}):
            out = card.run(0, True, environ=environ, client=object(), read=lambda: (_ for _ in ()).throw(AssertionError("no read")))
            self.assertEqual(out["status"], "not_configured", environ)

    def test_a_first_live_run_places_the_card_full_width_under_the_last_divider_and_fills_it(self):
        notion = FakeNotion()
        self.assertEqual(self.go(notion, live=False)["status"], "would_install")                    # a dry run inserts nothing
        self.assertEqual(notion.inserted, [])
        out = self.go(notion)
        self.assertEqual((out["status"], out["installed"], out["old_card"]), ("fresh", True, 0))
        self.assertEqual(notion.top, ["cl", "div", "capN0", "par"])                                 # at the page's top level, right under the divider
        made = notion.inserted[0]["children"][0]
        self.assertEqual((made["type"], made["callout"]["children"][0]["table"]["table_width"], len(made["callout"]["children"][0]["table"]["children"])), ("callout", 6, 7))
        self.assertEqual(notion.rows["capN0"]["capN0-r1"][5], "NEAR LIMIT")
        again = self.go(notion)
        self.assertEqual((again["installed"], again["rows_written"], again["text_written"], len(notion.inserted)), (False, 0, False, 1))     # never placed twice

    def test_a_card_still_in_the_old_column_is_moved_the_new_one_first_and_the_old_one_only_after_it_is_read_back(self):
        notion = FakeNotion(old=1)
        out = self.go(notion)
        self.assertEqual((out["status"], out["installed"], out["old_card"], out["old_card_removed"]), ("fresh", True, 1, 1))
        self.assertEqual((notion.trashed, notion.top[2]), (["capO0"], "capN0"))
        self.assertEqual(notion.rows["capN0"]["capN0-r1"][5], "NEAR LIMIT")                         # the new card carries the reading
        again = self.go(notion)
        self.assertEqual((again["installed"], again["old_card"], again["old_card_removed"], len(notion.inserted), notion.trashed), (False, 0, 0, 1, ["capO0"]))   # nothing moved twice

    def test_the_old_card_stays_when_the_new_one_has_no_fresh_reading_a_dry_run_or_a_failed_removal(self):
        notion = FakeNotion(new=1, old=1)

        def broken():
            raise edge.EdgeError("FIT_EDGE_NO_REPLY")
        self.assertEqual(self.go(notion, read=broken)["status"], "degraded")
        self.assertEqual(notion.trashed, [])                                                        # degraded: the old card is the last good reading
        patched = len(notion.patches)
        dry = self.go(notion, live=False)
        self.assertEqual((dry["old_card"], dry["rows_written"], notion.trashed, len(notion.patches)), (1, 0, [], patched))     # a dry run neither writes nor removes
        stuck = FakeNotion(new=1, old=1)
        stuck.call = (lambda orig: lambda method, path, body=None: ({} if body == {"in_trash": True} else orig(method, path, body)))(stuck.call)     # Notion accepts the trash but the block is not trashed
        with self.assertRaises(card.CardError) as ctx:
            self.go(stuck)
        self.assertEqual(str(ctx.exception), "CAPACITY_RETIRE_FAILED")

    def test_only_this_cards_own_copy_is_ever_trashed(self):
        notion = FakeNotion(new=1, old=1)
        with self.assertRaises(card.CardError) as ctx:
            card.retire(notion, "att")                                                              # a callout that is not this card (the Attention callout)
        self.assertEqual((str(ctx.exception), notion.trashed), ("CAPACITY_RETIRE_NOT_OWNED", []))
        with self.assertRaises(card.CardError):
            card.retire(notion, "par")                                                              # not even a callout
        self.assertEqual(notion.trashed, [])

    def test_without_a_top_level_divider_nothing_is_placed_anywhere_else(self):
        notion = FakeNotion(divider=False)
        with self.assertRaises(card.CardError) as ctx:
            self.go(notion)
        self.assertEqual((str(ctx.exception), notion.inserted), ("CAPACITY_PLACE_NOT_FOUND", []))

    def test_two_cards_in_either_place_are_refused(self):
        for notion in (FakeNotion(new=2), FakeNotion(old=2)):
            with self.assertRaises(card.CardError) as ctx:
                self.go(notion)
            self.assertEqual(str(ctx.exception), "CAPACITY_CARD_AMBIGUOUS")

    def test_a_fresh_reading_is_written_in_place_and_read_back(self):
        notion = FakeNotion(new=1)
        out = self.go(notion)
        self.assertEqual((out["status"], out["installed"], out["rows_written"], out["text_written"]), ("fresh", False, 4, True))   # the header is already right
        again = self.go(notion)
        self.assertEqual((again["rows_written"], again["text_written"]), (0, False))

    def test_a_failed_read_says_degraded_keeps_the_table_and_fails_the_stage_by_name(self):
        notion = FakeNotion(new=1)
        self.go(notion)
        before = {k: dict(v) for k, v in notion.rows.items()}

        def broken():
            raise edge.EdgeError("FIT_EDGE_NO_REPLY")
        out = self.go(notion, read=broken)
        self.assertEqual((out["status"], out["code"], out["failed"]), ("degraded", "FIT_EDGE_NO_REPLY", 1))
        self.assertEqual(notion.text["capN0"], "AI Capacity · DEGRADED · Oracle limits read failed at 12:30 AM CT (FIT_EDGE_NO_REPLY) · showing the last reading")
        self.assertEqual(notion.rows, before)

    def test_a_card_that_lost_its_shape_is_refused_and_nothing_is_written(self):
        notion = FakeNotion(new=1, rows=6)
        with self.assertRaises(card.CardError):
            self.go(notion)
        self.assertEqual(notion.patches, [])

    def test_a_dry_run_writes_nothing(self):
        notion = FakeNotion(new=1)
        out = self.go(notion, live=False)
        self.assertEqual((out["rows_written"], out["rows_changed"], notion.patches, notion.trashed), (0, 4, [], []))


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
        self.assertEqual(next(l for l in text.split("\n") if l.startswith("Proposed usage")), "Proposed usage · Cloud Fit backlog (300 jobs): at most 6.82% of the 5-hour window and about 6% of the weekly window"
                                              " (ESTIMATED from 44 metered calls) · fits under the 50% stop")

    def test_an_empty_backlog_shows_no_proposed_usage_line(self):
        reading = with_estimate(0, {"basis_calls": 1864, "proposed_percent": 0}, {"basis_calls": 1864, "proposed_percent": 0})
        text, _ = card.render(reading, NOW)
        self.assertNotIn("Proposed usage", text)

    def test_a_backlog_that_would_reach_the_stop_says_how_much_fits(self):
        reading = with_estimate(2692, {"basis_calls": 44, "per_call_below": 0.0227, "proposed_percent_at_most": 61.18, "calls_left_at_least": 2200},
                                {"basis_calls": 44, "per_call_percent": 0.02, "proposed_percent": 53.84, "calls_left": 2450})
        line = next(l for l in card.render(reading, NOW)[0].split("\n") if l.startswith("Proposed usage"))
        self.assertTrue(line.startswith("Proposed usage · Cloud Fit backlog (2,692 jobs): at most 61.18% of the 5-hour window"))
        self.assertTrue(line.endswith("· about 2,200 calls fit before the 50% stop; the rest wait for the reset"))

    def test_nothing_measured_or_nothing_reported_means_no_line(self):
        for reading in (with_estimate(10, {"basis_calls": 0}, {"basis_calls": 0}), READING):
            self.assertFalse([l for l in card.render(reading, NOW)[0].split("\n") if l.startswith("Proposed usage")])

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


class SignIns(unittest.TestCase):
    """D239 (Jim, Oct 20: "a permanent solution to the sign in"): the card names each OpenAI sign-in Oracle uses, OK with its expiry in Central time or what is wrong,
    from Oracle's state words only; the table keeps its fixed shape."""

    def test_each_sign_in_is_a_line_under_the_card_and_the_table_is_unchanged(self):
        ok = {**READING, "signins": {"meter": {"state": "OK", "expires": "2026-10-30T04:19:08+00:00"}, "fit": {"state": "EXPIRED", "expires": "1970-01-01T00:00:00+00:00"}}}
        text, table = card.render(ok, NOW)
        self.assertEqual(text.splitlines()[-1], "Sign-ins · Cloud Fit sign-in: EXPIRED: sign in again on Oracle · Usage meter sign-in: OK until Thu Oct 29, 11:19 PM CDT")
        self.assertEqual(table, card.render(READING, NOW)[1])
        self.assertEqual(card.signin_line({**READING, "signins": {"fit": {"state": "COOLDOWN", "expires": None}}}), "Sign-ins · Cloud Fit sign-in: FAILING: OpenAI refused its last refresh")
        self.assertIsNone(card.signin_line(READING))                                                        # an Oracle that does not report them: no line, nothing guessed

    def test_oracles_sign_in_words_are_kept_and_anything_else_is_off_contract(self):
        got = edge.limits("oracle.example", send=Limits.reply(self, signins={"fit": {"state": "OK", "expires": "2026-10-27T10:00:00+00:00", "profile": "dropped"}}))
        self.assertEqual(got["signins"], {"fit": {"state": "OK", "expires": "2026-10-27T10:00:00+00:00"}})
        self.assertNotIn("signins", edge.limits("oracle.example", send=Limits.reply(self)))
        for bad in ({"fit": {"state": "FINE"}}, {"account": {"state": "OK"}}, {"fit": "OK"}, {"fit": {"state": "OK", "expires": 5}}, ["fit"]):
            with self.assertRaises(edge.EdgeError) as ctx:
                edge.limits("oracle.example", send=Limits.reply(self, signins=bad))
            self.assertEqual(str(ctx.exception), "FIT_EDGE_LIMITS_OFF_CONTRACT", bad)
