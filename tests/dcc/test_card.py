"""D175: the Daily Command Center is written by V7 from what the other cards say on the page."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.attention import card as attention_card
from lifeos.dcc import card
from lifeos.platform import report_region, router
from lifeos.platform.notion_client import NotionError
from tests.kit.notion import MailRegions

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 12, 22, 20, tzinfo=TZ)
ENV = {"DCC_CARD_BLOCK_ID": "dcc-callout", "JIRA_CARD_BLOCK_ID": "jira-callout", "CALENDAR_CARD_BLOCK_ID": "calendar-callout", "BILLS_CARD_BLOCK_ID": "bills-callout",
       "AMAZON_CARD_BLOCK_ID": "amazon-callout", "ATTENTION_CARD_BLOCK_ID": "attention-callout", "MAIL_ALERTS_CARD_BLOCK_ID": "mail-callout"}
GTV = "GTV Action · JFM-1 — Example task · To Do"
SPRINT = "JFM Sprint 15 · 6 Overdue/Blocked · 1 Today · 7 This Week · 2 Done"


def text_block(notion, block_id, kind, text):
    return notion._text_block(block_id, kind, text)


def page(calendar="Calendar (Updated 10:06 PM CT)", jira="JIRA Execution (Updated 10:05 PM CT)", bills="Updated 10:07 PM CT · 0 due through one week from now · 26 overdue · Bill Tracker",
         mail="Updated 10:12 PM CT · 13 waiting · 0 need review", amazon="Updated 10:08 PM CT · 0 in transit · 2 delivered (last 7 days) · 0 need review",
         attention="Updated 10:05 PM CT · 23 active exceptions", **kwargs):
    """The Daily Report as the cards leave it (invented content), built on the shared fake."""
    notion = MailRegions(**kwargs)
    notion.children["calendar-callout"][0] = text_block(notion, "heading-calendar-callout", "heading_3", calendar)
    notion.children["jira-callout"] = [text_block(notion, "heading-jira-callout", "heading_4", jira), notion._paragraph(GTV), notion._paragraph(SPRINT)]
    notion.children["bills-callout"][0] = text_block(notion, "heading-bills-callout", "heading_3", "Bills: This Week")
    notion.children["bills-callout"][1] = text_block(notion, "bills-status-old", "paragraph", bills)
    notion.children["mail-callout"][0] = text_block(notion, "heading-mail-callout", "heading_3", "Mail Alerts")
    notion.children["mail-callout"][1] = text_block(notion, "mail-status-old", "paragraph", mail)
    notion.children["amazon-callout"][0] = text_block(notion, "heading-amazon-callout", "heading_3", "Amazon Orders")
    notion.children["amazon-callout"][1] = text_block(notion, "amazon-status-old", "paragraph", amazon)
    notion.metas["attention-callout"]["callout"] = {"rich_text": [{"plain_text": attention, "text": {"content": attention}}]}        # Jim's headless callout keeps its status in its own text (D131)
    return notion


def go(notion, live=True, env=None, now=NOW):
    return card.run(0, live, environ={**ENV, **(env or {})}, client=notion, now=now)


def lines_of(notion):
    return [report_region.plain(b) for b in notion.children["dcc-callout"]]


class ClassifyTests(unittest.TestCase):
    def test_stamps_are_fresh_within_ninety_minutes_stale_after_and_yesterdays_when_later_than_now(self):
        self.assertEqual(card.classify({"status": "Updated 10:06 PM CT · x"}, NOW), ("fresh", "10:06 PM"))
        self.assertEqual(card.classify({"status": "Updated 8:49 PM CT · x"}, NOW)[0], "stale")         # 91 minutes
        self.assertEqual(card.classify({"status": "Updated 8:50 PM CT · x"}, NOW)[0], "fresh")          # exactly 90
        self.assertEqual(card.classify({"status": "Updated 11:30 PM CT · x"}, NOW)[0], "stale")        # later than now means yesterday
        self.assertEqual(card.classify({"status": "Updated 12:05 AM CT · x"}, datetime(2026, 10, 12, 0, 30, tzinfo=TZ))[0], "fresh")
        self.assertEqual(card.classify({"status": "Calendar (Updated 10:06 PM CT)"}, NOW)[0], "fresh")

    def test_degraded_stale_missing_and_unreadable_are_never_fresh(self):
        self.assertEqual(card.classify({"status": "DEGRADED · Attention sync failed at 10:05 PM CT · showing the last accepted exceptions"}, NOW), ("degraded", "10:05 PM"))
        self.assertEqual(card.classify({"status": "STALE · last accepted Oct 11 9:44 AM CT"}, NOW)[0], "stale")
        self.assertEqual(card.classify({"status": "no stamp here"}, NOW), ("unknown", None))
        self.assertEqual(card.classify({"unreadable": True}, NOW)[0], "unreadable")
        self.assertEqual(card.classify({"unset": True}, NOW)[0], "unset")


class ComposeTests(unittest.TestCase):
    def areas(self, **over):
        base = {"Calendar": {"status": "Calendar (Updated 10:06 PM CT)", "lines": []}, "Jira": {"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": [GTV, SPRINT]},
                "Bills": {"status": "Updated 10:07 PM CT · 0 due through one week from now · 26 overdue · Bill Tracker", "lines": []},
                "Mail Alerts": {"status": "Updated 10:12 PM CT · 13 waiting · 0 need review", "lines": []},
                "Amazon": {"status": "Updated 10:08 PM CT · 0 in transit", "lines": []}, "Attention": {"status": "Updated 10:05 PM CT · 23 active exceptions", "lines": []},
                "Hiring Pipeline": {"status": "Updated 10:13 PM CT · 5 active", "lines": []}}
        base.update(over)
        return base

    def test_everything_fresh_says_what_the_cards_say_and_asks_for_the_obvious_actions(self):
        lines = dict(card.compose(self.areas(), NOW))
        first = card.compose(self.areas(), NOW)[0]
        self.assertEqual(first, (None, "Updated 10:20 PM CT · 7 of 7 areas current"))
        self.assertEqual(lines["Current focus"], GTV + "; " + SPRINT)
        self.assertEqual(lines["Attention needed"], "26 overdue bills; 23 active Attention exceptions; 13 mail items waiting; 5 interview opportunities active")
        self.assertIn("Calendar 10:06 PM", lines["Evidence freshness"])
        self.assertEqual(lines["Next useful action"], "Triage 26 overdue bills; Clear 6 overdue or blocked Jira items; Review 23 Attention exceptions")
        self.assertNotIn("unverified", str(lines).lower())

    def test_a_degraded_or_stale_area_is_named_and_comes_first_in_the_next_action(self):
        areas = self.areas(Attention={"status": "DEGRADED · Attention sync failed at 10:05 PM CT · showing the last accepted exceptions", "lines": []},
                           Bills={"status": "STALE · last accepted Oct 11 9:44 AM CT · 4 overdue", "lines": []})
        out = dict(card.compose(areas, NOW))
        self.assertEqual(card.compose(areas, NOW)[0][1], "Updated 10:20 PM CT · 5 of 7 areas current · 2 need a look")
        self.assertTrue(out["Next useful action"].startswith("Check Bills, Attention (not current)"))
        self.assertIn("Attention degraded (last 10:05 PM)", out["Evidence freshness"])

    def test_nothing_flagged_says_so(self):
        areas = self.areas(Bills={"status": "Updated 10:07 PM CT · 0 due · 0 overdue", "lines": []}, Attention={"status": "Updated 10:05 PM CT · No active exceptions.", "lines": []},
                           Jira={"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": ["JFM Sprint 15 · 0 Overdue/Blocked · 1 Today"]})
        areas["Mail Alerts"] = {"status": "Updated 10:12 PM CT · 0 waiting · 0 need review", "lines": []}
        areas["Hiring Pipeline"] = {"status": "Updated 10:13 PM CT · 0 active", "lines": []}
        out = dict(card.compose(areas, NOW))
        self.assertEqual((out["Attention needed"], out["Next useful action"]), ("Nothing flagged by the cards.", "Nothing flagged; continue the day's planned work."))

    def test_the_counts_it_reads_match_what_the_attention_card_really_writes(self):
        text = report_region.plain(attention_card.render(23, NOW)[0])
        out = dict(card.compose(self.areas(Attention={"status": text, "lines": []}), NOW))
        self.assertIn("23 active Attention exceptions", out["Attention needed"])

    def test_render_makes_bold_labelled_paragraphs_and_an_unlabelled_first_line(self):
        blocks = card.render(card.compose(self.areas(), NOW))
        self.assertEqual([b["type"] for b in blocks], ["paragraph"] * 5)
        self.assertEqual(len(blocks[0]["paragraph"]["rich_text"]), 1)
        self.assertEqual(blocks[1]["paragraph"]["rich_text"][0], {"type": "text", "text": {"content": "Current focus: "}, "annotations": {"bold": True}})


class RunTests(unittest.TestCase):
    def test_a_live_run_writes_only_the_command_center_text_and_leaves_every_other_region_untouched(self):
        notion = page()
        before = {k: notion.full_tree(k) for k in ("calendar-callout", "jira-callout", "bills-callout", "amazon-callout", "attention-callout", "mail-callout")}
        counts = go(notion)
        kids = notion.children["dcc-callout"]
        self.assertEqual(report_region.plain(kids[0]), router.DCC_REGION)
        self.assertEqual(kids[0]["id"], "heading-dcc-callout")                                  # the heading keeps its identity
        self.assertEqual(report_region.plain(kids[1]), "Updated 10:20 PM CT · 6 of 7 areas current · 1 need a look")
        self.assertNotIn("Protected DCC content", "\n".join(lines_of(notion)))
        self.assertEqual({k: notion.full_tree(k) for k in before}, before)
        self.assertEqual((counts["fresh"], counts["not_current"], counts["blocks_written"]), (6, 1, 5))
        self.assertEqual(counts["states"]["Hiring Pipeline"], "unset")                           # the log names each area's state, never its text
        self.assertEqual({v for k, v in counts["states"].items() if k != "Hiring Pipeline"}, {"fresh"})          # Hiring Pipeline has no block id here, so it is unset
        others = {k["id"] for key in before for k in notion.children.get(key, [])}
        self.assertFalse({p.split("/")[2] for m, p in notion.log if m == "DELETE"} & others)                    # only the old Command Center text was removed

    def test_the_text_says_what_the_cards_said(self):
        notion = page()
        go(notion)
        text = "\n".join(lines_of(notion))
        self.assertIn("Current focus: " + GTV, text)
        self.assertIn("26 overdue bills", text)
        self.assertIn("Hiring Pipeline unset", text)

    def test_a_dry_run_and_a_missing_secret_write_nothing(self):
        notion = page()
        counts = go(notion, live=False)
        self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE", "PATCH")], [])
        self.assertEqual(counts["status"], "fresh")
        self.assertEqual(card.run(0, True, environ={}, client=page())["status"], "not_configured")

    def test_one_unreadable_card_does_not_stop_the_others(self):
        notion = page()
        broken = notion.call

        def call(method, path, body=None):
            if path.startswith("/blocks/bills-callout/children"):
                raise NotionError("NOTION_HTTP_500")
            return broken(method, path, body)
        notion.call = call
        # the protected-region proof also reads Bills, so the write is refused rather than made around an unreadable region
        with self.assertRaises(NotionError):
            go(notion)
        self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE")], [])
        areas = card.read_areas(notion, ENV)
        self.assertEqual(areas["Bills"], {"unreadable": True})
        self.assertIn("status", areas["Jira"])

    def test_nothing_readable_writes_nothing_and_says_why(self):
        notion = page()
        notion.call = lambda *a, **k: (_ for _ in ()).throw(NotionError("NOTION_HTTP_500"))
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertEqual(str(error.exception), "DCC_NO_EVIDENCE")

    def test_a_protected_region_changing_mid_write_fails_the_run(self):
        notion = page(change_on="APPEND", change_region="jira-callout")
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertIn("PROTECTED_REGION_CHANGED", str(error.exception))

    def test_a_wrong_target_is_refused_before_any_write(self):
        notion = page()
        with self.assertRaises(NotionError) as error:
            go(notion, env={"DCC_CARD_BLOCK_ID": "jira-callout"})
        self.assertIn("NOT_OWNED", str(error.exception))
        self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE")], [])

    def test_the_region_has_one_owner_and_the_card_is_a_stage(self):
        self.assertEqual(router.OWNERS[router.DCC_REGION], card.MODULE)
        from lifeos import run as entry
        self.assertEqual(entry.STAGES["dcc-card"].target, ("lifeos.dcc.card", "run"))


if __name__ == "__main__":
    unittest.main()
