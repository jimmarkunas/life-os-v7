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


class FakeNotion:
    """One callout holding one 6 x 7 table, the shape the card is installed with."""
    def __init__(self, rows=card.ROWS, cols=card.COLS, kind="callout"):
        self.kind, self.text, self.cols = kind, "", cols
        self.rows = {f"r{i}": [""] * cols for i in range(rows)}
        self.patches = []

    def call(self, method, path, body=None):
        if method == "PATCH":
            self.patches.append(path)
            rid = path.split("/")[2]
            if "table_row" in body:
                self.rows[rid] = ["".join(x["text"]["content"] for x in cell) for cell in body["table_row"]["cells"]]
            else:
                self.text = body["callout"]["rich_text"][0]["text"]["content"]
            return {}
        if path == "/blocks/cap":
            return {"type": self.kind, "id": "cap", "callout": {"rich_text": [{"plain_text": self.text, "text": {"content": self.text}}] if self.text else []}}
        if path.startswith("/blocks/cap/children"):
            return {"results": [{"type": "table", "id": "tbl", "table": {"table_width": self.cols}}], "has_more": False}
        if path.startswith("/blocks/tbl/children"):
            return {"results": [{"type": "table_row", "id": rid, "table_row": {"cells": [[{"plain_text": c, "text": {"content": c}}] if c else [] for c in cells]}}
                                for rid, cells in self.rows.items()], "has_more": False}
        raise AssertionError(path)


class Run(unittest.TestCase):
    def test_not_configured_reads_and_writes_nothing(self):
        out = card.run(0, True, environ={}, client=object(), read=lambda: (_ for _ in ()).throw(AssertionError("no read")))
        self.assertEqual(out["status"], "not_configured")

    def test_a_fresh_reading_is_written_in_place_and_read_back(self):
        notion = FakeNotion()
        out = card.run(0, True, environ={"AI_CAPACITY_BLOCK_ID": "cap"}, client=notion, read=lambda: READING, now=NOW)
        self.assertEqual((out["status"], out["rows_written"], out["text_written"]), ("fresh", 5, True))      # header + 4 rows changed; 2 blank rows already blank
        self.assertEqual(notion.rows["r1"][5], "NEAR LIMIT")
        again = card.run(0, True, environ={"AI_CAPACITY_BLOCK_ID": "cap"}, client=notion, read=lambda: READING, now=NOW)
        self.assertEqual((again["rows_written"], again["text_written"]), (0, False))                            # nothing changed: nothing written

    def test_a_failed_read_says_degraded_keeps_the_table_and_fails_the_stage_by_name(self):
        notion = FakeNotion()
        card.run(0, True, environ={"AI_CAPACITY_BLOCK_ID": "cap"}, client=notion, read=lambda: READING, now=NOW)
        before = dict(notion.rows)

        def broken():
            raise edge.EdgeError("FIT_EDGE_NO_REPLY")
        out = card.run(0, True, environ={"AI_CAPACITY_BLOCK_ID": "cap"}, client=notion, read=broken, now=NOW)
        self.assertEqual((out["status"], out["code"], out["failed"]), ("degraded", "FIT_EDGE_NO_REPLY", 1))
        self.assertEqual(notion.text, "AI Capacity · DEGRADED · Oracle limits read failed at 12:30 AM CT (FIT_EDGE_NO_REPLY) · showing the last reading")
        self.assertEqual(notion.rows, before)

    def test_a_block_that_is_not_the_installed_shape_is_refused_and_nothing_is_written(self):
        for notion in (FakeNotion(kind="paragraph"), FakeNotion(cols=5), FakeNotion(rows=6)):
            with self.assertRaises(card.CardError):
                card.run(0, True, environ={"AI_CAPACITY_BLOCK_ID": "cap"}, client=notion, read=lambda: READING, now=NOW)
            self.assertEqual(notion.patches, [])

    def test_a_dry_run_writes_nothing(self):
        notion = FakeNotion()
        out = card.run(0, False, environ={"AI_CAPACITY_BLOCK_ID": "cap"}, client=notion, read=lambda: READING, now=NOW)
        self.assertEqual((out["rows_written"], out["rows_changed"], notion.patches), (0, 5, []))


class Limits(unittest.TestCase):
    def reply(self, **override):
        def send(host, data, wait=None):
            body = json.loads(data.split(b"\n", 1)[1])
            self.assertEqual(set(body), {"limits", "nonce"})                                                  # never a request, never a profile
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
