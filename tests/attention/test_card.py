"""The Attention card updates its text while preserving Jim's interactive view."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.attention import card
from lifeos.platform import report_region
from tests.attention.test_attention import FakeNotion, page
from tests.kit.notion import MailRegions

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 5, 13, 30, tzinfo=TZ)                  # Monday: the week ends 2026-10-11
ENV = {"ATTENTION_CARD_BLOCK_ID": "attention-callout", "JIRA_CARD_BLOCK_ID": "jira-callout", "CALENDAR_CARD_BLOCK_ID": "calendar-callout",
       "BILLS_CARD_BLOCK_ID": "bills-callout", "AMAZON_CARD_BLOCK_ID": "amazon-callout", "MAIL_ALERTS_CARD_BLOCK_ID": "mail-callout"}


def line(blocks):
    return [report_region.plain(b) for b in blocks]


class Render(unittest.TestCase):
    def test_empty_state_says_no_active_exceptions(self):
        text = line(card.render(0, NOW))[0]
        self.assertEqual(text, "Updated 1:30 PM CT · No active exceptions.")
        self.assertNotIn("Suggested next decisions", text)                                     # Jim, Oct 26: generated advice above the table is not attention

    def test_counts_singular_and_plural(self):
        self.assertEqual(line(card.render(1, NOW))[0], "Updated 1:30 PM CT · 1 active exception")
        self.assertEqual(line(card.render(3, NOW))[0], "Updated 1:30 PM CT · 3 active exceptions")

    def test_the_callout_text_is_one_status_line_even_when_decisions_are_passed(self):
        block = card.render(0, NOW, [("Triage overdue bills", "4 on the Bills card", "https://example.invalid/bills")], "5 active hiring opportunities")[0]
        self.assertEqual(report_region.plain(block), "Updated 1:30 PM CT · No active exceptions.")
        self.assertEqual([part for part in block["paragraph"]["rich_text"] if (part.get("text", {}).get("link") or {}).get("url")], [])

    def test_degraded_never_looks_like_an_empty_queue(self):
        text = line(card.render(0, NOW, degraded=True))[0]
        self.assertTrue(text.startswith("DEGRADED · Attention sync failed at 1:30 PM CT"))
        self.assertNotIn("No active exceptions", text)

    def test_only_active_open_non_mail_rows_count(self):
        rows = [dict(id=str(i), item=f"i{i}", category=c, done=d, active=a, medium="", url="", week="2026-10-11")
                for i, (c, d, a) in enumerate([("Account", False, True), ("Account", True, True), ("Account", False, False), ("Physical Mail", False, True)])]
        self.assertEqual(len(card.active(rows)), 1)

    def test_decisions_keep_links_and_suppress_counts_from_degraded_sources(self):
        areas = {"Calendar": {"status": "Calendar (Updated 1:25 PM CT)"}, "Jira": {"status": "JIRA Execution (Updated 1:25 PM CT)", "lines": ["Sprint · 3 Overdue/Blocked"]},
                 "Bills": {"status": "Updated 1:25 PM CT · 4 overdue", "links": {"Updated 1:25 PM CT · 4 overdue": "https://example.invalid/bills"}},
                 "Mail Alerts": {"status": "Updated 1:25 PM CT · 2 need review"}, "Amazon": {"status": "Updated 1:25 PM CT"},
                 "Attention": {"status": "Updated 1:25 PM CT · 2 active exceptions"}, "Hiring Pipeline": {"status": "Updated 1:25 PM CT · 5 active"}}
        decisions, hiring = card._decisions(areas, NOW)
        self.assertEqual([item[0] for item in decisions], ["Triage overdue bills", "Clear overdue or blocked Jira items", "Review Attention exceptions"])
        self.assertEqual(decisions[0][2], "https://example.invalid/bills")
        self.assertEqual(hiring, "5 active hiring opportunities")
        areas["Bills"] = {"status": "DEGRADED · Bills failed at 1:25 PM CT · showing the last accepted state"}
        decisions, _ = card._decisions(areas, NOW)
        self.assertTrue(decisions[0][0].startswith("Check Bills"))
        self.assertNotIn("Triage overdue bills", [item[0] for item in decisions])

    def test_a_card_that_cannot_be_read_however_it_fails_never_stops_the_status_line(self):
        from unittest import mock
        with mock.patch.object(card, "_text_of", side_effect=KeyError("odd block")):
            areas = card._areas(None, ENV, NOW)
        self.assertTrue(all(area in ({"unreadable": True}, {"unset": True}) for area in areas.values()))
        self.assertEqual(areas["Bills"], {"unreadable": True})
        decisions, _ = card._decisions({**areas, "Attention": {"status": "Updated 1:30 PM CT · 2 active exceptions"}}, NOW)
        self.assertTrue(decisions[0][0].startswith("Check "))


def own(regions):
    return report_region.plain({"type": "callout", "callout": regions.metas["attention-callout"].get("callout") or {}})


class Run(unittest.TestCase):
    def go(self, pages=(), live=True, env=None, regions=None):
        regions = regions or MailRegions()
        counts = card.run(0, live, environ={**ENV, **(env or {})}, reader=FakeNotion(pages), client=regions, now=NOW)
        return regions, counts

    def test_live_write_sets_the_callouts_own_text_and_touches_nothing_else(self):
        regions = MailRegions()
        before = {k: regions.full_tree(k) for k in ("calendar-callout", "jira-callout", "dcc-callout", "bills-callout", "amazon-callout")}
        children_before = [dict(k) for k in regions.children["attention-callout"]]
        regions, counts = self.go(regions=regions)
        self.assertEqual(own(regions), "Updated 1:30 PM CT · No active exceptions.")
        self.assertEqual(regions.children["attention-callout"], children_before)                # the linked view is untouched and no child block is added
        self.assertEqual({k: regions.full_tree(k) for k in before}, before)
        self.assertEqual([m for m, _ in regions.log if m in ("APPEND", "DELETE")], [])
        self.assertEqual(counts["active"], 0)

    def test_running_again_replaces_the_text_it_never_adds_a_second_line(self):
        regions, _ = self.go()
        regions, _ = self.go(pages=[page("a", "Card added")], regions=regions)
        self.assertEqual(own(regions), "Updated 1:30 PM CT · 1 active exception")
        self.assertEqual([k["id"] for k in regions.children["attention-callout"]], ["attention-view"])

    def test_counts_the_current_weeks_active_rows(self):
        _, counts = self.go([page("a", "Card added"), page("b", "Handled", done=True), page("c", "Mail", category="Physical Mail")])
        self.assertEqual(counts["active"], 1)

    def test_visible_child_text_lines_from_earlier_runs_are_removed(self):
        regions = MailRegions()
        regions.children["attention-callout"].insert(0, regions._text_block("old-line", "paragraph", "Updated 9:00 AM CT · stale"))
        regions, _ = self.go(regions=regions)
        self.assertEqual([k["id"] for k in regions.children["attention-callout"]], ["attention-view"])
        self.assertTrue(own(regions).startswith("Updated 1:30 PM CT · No active exceptions."))

    def test_dry_run_and_missing_config_write_nothing(self):
        regions, _ = self.go(live=False)
        self.assertEqual([m for m, _ in regions.log if m in ("APPEND", "DELETE", "PATCH")], [])
        self.assertEqual(card.run(0, True, environ={}, reader=FakeNotion(), client=MailRegions())["status"], "not_configured")

    def test_failed_sync_flag_writes_the_degraded_line(self):
        regions, counts = self.go(env={"ATTENTION_SYNC_OUTCOME": "failure"})
        self.assertTrue(own(regions).startswith("DEGRADED"))
        self.assertEqual(counts["status"], "degraded")

    def test_a_callout_with_a_heading_or_no_view_is_not_ours(self):
        for kids in ([{"id": "h", "type": "heading_3", "has_children": False, "heading_3": {"rich_text": [{"plain_text": "Bills: This Week"}]}}, {"id": "v", "type": "child_database", "has_children": False}],
                     [{"id": "t", "type": "paragraph", "has_children": False, "paragraph": {"rich_text": []}}]):
            regions = MailRegions()
            regions.children["attention-callout"] = kids
            with self.assertRaises(Exception) as ctx:
                card.run(0, True, environ=ENV, reader=FakeNotion(), client=regions, now=NOW)
            self.assertIn("CARD_NOT_OWNED", str(ctx.exception))
            self.assertEqual([m for m, _ in regions.log if m in ("APPEND", "DELETE", "PATCH")], [])

    def test_a_schema_mismatch_changes_nothing(self):
        regions = MailRegions()
        with self.assertRaises(Exception):
            card.run(0, True, environ=ENV, reader=FakeNotion(schema_ok=False), client=regions, now=NOW)
        self.assertEqual([m for m, _ in regions.log if m in ("APPEND", "DELETE", "PATCH")], [])


if __name__ == "__main__":
    unittest.main()
