"""D128: the Attention card writes one text line above Jim's interactive view and nothing else."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.attention import card
from lifeos.platform import report_region
from tests.attention.test_attention import FakeNotion, page
from tests.kit.notion import AttentionRegions

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 5, 13, 30, tzinfo=TZ)                  # Monday: the week ends 2026-10-11
ENV = {"ATTENTION_CARD_BLOCK_ID": "attention-callout", "JIRA_CARD_BLOCK_ID": "jira-callout", "CALENDAR_CARD_BLOCK_ID": "calendar-callout",
       "BILLS_CARD_BLOCK_ID": "bills-callout", "AMAZON_CARD_BLOCK_ID": "amazon-callout"}


def line(blocks):
    return [report_region.plain(b) for b in blocks]


class Render(unittest.TestCase):
    def test_empty_state_says_no_active_exceptions(self):
        self.assertEqual(line(card.render(0, NOW)), ["Updated 1:30 PM CT · No active exceptions."])

    def test_counts_singular_and_plural(self):
        self.assertEqual(line(card.render(1, NOW)), ["Updated 1:30 PM CT · 1 active exception"])
        self.assertEqual(line(card.render(3, NOW)), ["Updated 1:30 PM CT · 3 active exceptions"])

    def test_degraded_never_looks_like_an_empty_queue(self):
        text = line(card.render(0, NOW, degraded=True))[0]
        self.assertTrue(text.startswith("DEGRADED · Attention sync failed at 1:30 PM CT"))
        self.assertNotIn("No active exceptions", text)

    def test_only_active_open_non_mail_rows_count(self):
        rows = [dict(id=str(i), item=f"i{i}", category=c, done=d, active=a, medium="", url="", week="2026-10-11")
                for i, (c, d, a) in enumerate([("Account", False, True), ("Account", True, True), ("Account", False, False), ("Physical Mail", False, True)])]
        self.assertEqual(len(card.active(rows)), 1)


class Run(unittest.TestCase):
    def go(self, pages=(), live=True, env=None, regions=None):
        regions = regions or AttentionRegions()
        counts = card.run(0, live, environ={**ENV, **(env or {})}, reader=FakeNotion(pages), client=regions, now=NOW)
        return regions, counts

    def test_live_write_replaces_only_the_text_keeps_the_view_and_every_other_region(self):
        regions = AttentionRegions()
        before = {k: regions.full_tree(k) for k in ("calendar-callout", "jira-callout", "dcc-callout", "bills-callout", "amazon-callout")}
        regions, counts = self.go(regions=regions)
        kids = regions.children["attention-callout"]
        self.assertEqual(report_region.plain(kids[0]), "Attention")
        self.assertEqual(report_region.plain(kids[1]), "Updated 1:30 PM CT · No active exceptions.")
        self.assertEqual(kids[-1]["id"], "attention-view")                                      # the linked view is never removed
        self.assertNotIn("Old attention text", "\n".join(line(kids)))
        self.assertEqual({k: regions.full_tree(k) for k in before}, before)
        self.assertEqual(counts["active"], 0)

    def test_counts_the_current_weeks_active_rows(self):
        _, counts = self.go([page("a", "Card added"), page("b", "Handled", done=True), page("c", "Mail", category="Physical Mail")])
        self.assertEqual(counts["active"], 1)

    def test_dry_run_and_missing_config_write_nothing(self):
        regions, _ = self.go(live=False)
        self.assertEqual([m for m, _ in regions.log if m in ("APPEND", "DELETE", "PATCH")], [])
        self.assertEqual(card.run(0, True, environ={}, reader=FakeNotion(), client=AttentionRegions())["status"], "not_configured")

    def test_failed_sync_flag_writes_the_degraded_line(self):
        regions, counts = self.go(env={"ATTENTION_SYNC_OUTCOME": "failure"})
        self.assertTrue(report_region.plain(regions.children["attention-callout"][1]).startswith("DEGRADED"))
        self.assertEqual(counts["status"], "degraded")

    def test_a_schema_mismatch_changes_nothing(self):
        regions = AttentionRegions()
        with self.assertRaises(Exception):
            card.run(0, True, environ=ENV, reader=FakeNotion(schema_ok=False), client=regions, now=NOW)
        self.assertEqual([m for m, _ in regions.log if m in ("APPEND", "DELETE", "PATCH")], [])


if __name__ == "__main__":
    unittest.main()
