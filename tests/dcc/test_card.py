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


class Nested(MailRegions):
    """MailRegions that creates blocks the way the Notion API does: children nested up to two levels in one append, placed right after the `after` block."""

    def _make(self, block):
        self.serial += 1
        kind, new_id = block["type"], f"nested-{self.serial}"
        body = {k: v for k, v in block[kind].items() if k != "children"}
        if "rich_text" in body:
            body["rich_text"] = [{"plain_text": p["text"]["content"], "text": {"link": None, **p["text"]}, "annotations": {"bold": False, "color": "default", **p.get("annotations", {})}} for p in body["rich_text"]]
        self.children[new_id] = [self._make(child) for child in block[kind].get("children", [])]
        return {"id": new_id, "type": kind, "has_children": bool(self.children[new_id]), kind: body}

    def call_once(self, method, path, body=None):
        self.log.append(("APPEND", path))
        kids = self.children.setdefault(path.split("/")[2], [])
        made, at = [self._make(b) for b in body["children"]], len(kids)
        if body.get("after"):
            at = next(i for i, k in enumerate(kids) if k["id"] == body["after"]) + 1
        kids[at:at] = made
        self._poke("APPEND")
        return {"results": made + kids[at + len(made):]}


def text_block(notion, block_id, kind, text):
    return notion._text_block(block_id, kind, text)


def page(calendar="Calendar (Updated 10:06 PM CT)", jira="JIRA Execution (Updated 10:05 PM CT)", bills="Updated 10:07 PM CT · 0 due through one week from now · 26 bills due · 26 overdue · Bill Tracker",
         mail="Updated 10:12 PM CT · 13 waiting · 0 need review", amazon="Updated 10:08 PM CT · 0 in transit · 2 delivered (last 7 days) · 0 need review",
         attention="Updated 10:05 PM CT · 23 active exceptions", **kwargs):
    """The Daily Report as the cards leave it (invented content), built on the shared fake."""
    notion = Nested(**kwargs)
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
        link = {GTV: "https://example.invalid/browse/JFM-1"}
        base = {"Calendar": {"status": "Calendar (Updated 10:06 PM CT)", "lines": []}, "Jira": {"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": [GTV, SPRINT], "links": link},
                "Bills": {"status": "Updated 10:07 PM CT · 0 due through one week from now · 26 bills due · 26 overdue · Bill Tracker", "lines": [],
                          "links": {"Updated 10:07 PM CT · 0 due through one week from now · 26 bills due · 26 overdue · Bill Tracker": "https://example.invalid/tracker"}},
                "Mail Alerts": {"status": "Updated 10:12 PM CT · 13 waiting · 0 need review", "lines": []},
                "Amazon": {"status": "Updated 10:08 PM CT · 0 in transit", "lines": []}, "Attention": {"status": "Updated 10:05 PM CT · 23 active exceptions", "lines": []},
                "Hiring Pipeline": {"status": "Updated 10:13 PM CT · 5 active", "lines": []}}
        base.update(over)
        return base

    def test_everything_fresh_binds_each_number_to_its_own_card_and_links_the_real_records(self):
        m = card.compose(self.areas(), NOW)
        self.assertEqual(m["health"], "Source health and freshness · 7 of 7 current")
        self.assertIn("Calendar 10:06 PM", m["detail"][0])
        self.assertTrue(m["detail"][1].startswith("✅ 7 of 7 source cards current"))
        self.assertNotIn("focus", m)
        self.assertNotIn("sub", m)
        self.assertNotIn("items", m)
        self.assertNotIn("hiring", m)
        self.assertEqual([(v, n) for v, n, _ in m["kpis"]], [("26", "Bills due"), ("6", "Jira Overdue / Blocked"), ("23", "Attention items"), ("13", "Mail waiting")])
        self.assertEqual(m["kpis"][1][2], "Jira · 10:05 PM CT")                                          # the combined count, labelled as such, with its source stamp

    def test_a_degraded_or_stale_card_is_named_in_the_closed_summary_and_never_shows_a_number_or_a_zero(self):
        areas = self.areas(Attention={"status": "DEGRADED · Attention sync failed at 10:05 PM CT · showing the last accepted exceptions", "lines": []},
                           Bills={"status": "STALE · last accepted Oct 11 9:44 AM CT · 4 bills due · 4 overdue", "lines": []})
        m = card.compose(areas, NOW)
        self.assertEqual(m["health"], "Source health and freshness · 5 of 7 current · Bills STALE · Attention DEGRADED")
        self.assertTrue(m["detail"][1].startswith("⚠️ 5 of 7"))
        self.assertEqual([(v, t) for v, n, t in m["kpis"] if n in ("Bills due", "Attention items")], [("—", "STALE · Bills (last 9:44 AM)"), ("—", "DEGRADED · Attention (last 10:05 PM)")])
        self.assertIn("Bills STALE", m["health"])
        self.assertIn("Attention DEGRADED", m["health"])

    def test_a_missing_gtv_action_or_an_unreadable_jira_card_says_so_instead_of_choosing_one(self):
        m = card.compose(self.areas(Jira={"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": [SPRINT]}), NOW)
        self.assertNotIn("focus", m)
        self.assertNotIn("sub", m)
        m = card.compose(self.areas(Jira={"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": ["GTV Action · JFM-2 — No status yet"]}), NOW)
        self.assertNotIn("focus", m)
        self.assertNotIn("sub", m)
        m = card.compose(self.areas(Jira={"unreadable": True}), NOW)
        self.assertNotIn("focus", m)
        self.assertNotIn("sub", m)
        self.assertEqual(m["kpis"][1][2], "UNREADABLE · Jira")

    def test_nothing_flagged_says_so_and_a_fresh_zero_is_a_zero(self):
        areas = self.areas(Bills={"status": "Updated 10:07 PM CT · 0 due · 0 bills due · 0 overdue", "lines": []}, Attention={"status": "Updated 10:05 PM CT · No active exceptions.", "lines": []},
                           Jira={"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": ["JFM Sprint 15 · 0 Overdue/Blocked · 1 Today"]})
        areas["Mail Alerts"] = {"status": "Updated 10:12 PM CT · 0 waiting · 0 need review", "lines": []}
        m = card.compose(areas, NOW)
        self.assertEqual([v for v, _, _ in m["kpis"]], ["0", "0", "0", "0"])                   # a verified zero is a zero, Jira included

    def test_the_jira_card_shows_its_combined_overdue_blocked_count_and_never_a_number_it_cannot_verify(self):
        kpi = lambda areas: card.compose(areas, NOW)["kpis"][1]
        self.assertEqual(kpi(self.areas()), ("6", "Jira Overdue / Blocked", "Jira · 10:05 PM CT"))
        self.assertEqual(kpi(self.areas(Jira={"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": [GTV, "JFM Sprint 15 · 0 Overdue/Blocked · 1 Today"]}))[0], "0")
        stale = {"status": "JIRA Execution (Updated 8:00 PM CT)", "lines": [GTV, SPRINT]}
        self.assertEqual(kpi(self.areas(Jira=stale)), ("—", "Jira Overdue / Blocked", "STALE · Jira (last 8:00 PM)"))
        degraded = {"status": "DEGRADED · Jira sync failed at 10:05 PM CT · showing the last accepted state", "lines": [GTV, SPRINT]}
        self.assertEqual(kpi(self.areas(Jira=degraded))[::2], ("—", "DEGRADED · Jira (last 10:05 PM)"))
        self.assertEqual(kpi(self.areas(Jira={"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": [GTV]})), ("—", "Jira Overdue / Blocked", "Jira · 10:05 PM CT · count not reported"))
        self.assertEqual(kpi(self.areas(Jira={"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": [GTV, "JFM Sprint 15 · many Overdue/Blocked"]}))[0], "—")

    def test_the_counts_it_reads_match_what_the_attention_card_really_writes(self):
        text = report_region.plain(attention_card.render(23, NOW)[0])
        self.assertEqual(card.compose(self.areas(Attention={"status": text, "lines": []}), NOW)["kpis"][2][0], "23")

    def test_render_places_source_health_under_heading_above_four_kpis(self):
        health, kpi_row = card.render(card.compose(self.areas(), NOW))
        self.assertEqual(health["type"], "toggle")
        self.assertEqual([c["column"]["width_ratio"] for c in kpi_row["column_list"]["children"]], [0.25] * 4)
        self.assertEqual([c["column"]["width_ratio"] for c in kpi_row["column_list"]["children"]], [0.25] * 4)
        kpis = [c["column"]["children"][0]["callout"] for c in kpi_row["column_list"]["children"]]
        self.assertEqual([[b["type"] for b in k["children"]] for k in kpis], [["heading_3", "paragraph", "paragraph"]] * 4)
        self.assertEqual([k["icon"]["external"]["url"].rsplit("/", 1)[1] for k in kpis], ["credit-card_gray.svg", "arrow-right_gray.svg", "arrow-up_gray.svg", "inbox_gray.svg"])


class RunTests(unittest.TestCase):
    def shape(self, notion):
        """The Command Center's blocks as the page shows them: type and text, nested, in order."""
        def walk(block_id):
            return [(b["type"], report_region.plain(b), walk(b["id"])) for b in notion.children.get(block_id, [])]
        return walk("dcc-callout")

    def test_the_first_live_run_installs_the_layout_removes_the_old_lines_and_keeps_every_other_region(self):
        notion = page()
        before = {k: notion.full_tree(k) for k in ("calendar-callout", "jira-callout", "bills-callout", "amazon-callout", "attention-callout", "mail-callout")}
        counts = go(notion)
        kids = notion.children["dcc-callout"]
        self.assertEqual([b["type"] for b in kids][1:], ["toggle", "column_list"])
        self.assertEqual((report_region.plain(kids[0]), kids[0]["id"]), (router.DCC_REGION, "heading-dcc-callout"))        # the heading keeps its identity
        self.assertNotIn("Protected DCC content", str(self.shape(notion)))                                                  # the old flat line is gone
        health, kpi_row = (b["id"] for b in kids[1:])
        self.assertEqual(len(notion.children[health]), 2)
        self.assertEqual([len(notion.children[c["id"]]) for c in notion.children[kpi_row]], [1, 1, 1, 1])
        self.assertEqual({k: notion.full_tree(k) for k in before}, before)
        self.assertEqual((counts["fresh"], counts["not_current"], counts["removed"]), (6, 1, 1))
        self.assertEqual({v for k, v in counts["states"].items() if k != "Hiring Pipeline"}, {"fresh"})
        others = {k["id"] for key in before for k in notion.children.get(key, [])}
        self.assertFalse({p.split("/")[2] for m, p in notion.log if m == "DELETE"} & others)

    def test_the_second_run_changes_nothing_and_a_later_run_rewrites_only_text_in_the_same_blocks(self):
        notion = page()
        go(notion)
        ids = {k: [b["id"] for b in v] for k, v in notion.children.items() if k in ("dcc-callout",) or k.startswith("agenda-")}
        first, mark, made = self.shape(notion), len(notion.log), sum(1 for m, _ in notion.log if m == "APPEND")
        counts = go(notion)
        self.assertEqual((counts["blocks_written"], counts["removed"]), (0, 0))
        self.assertEqual([m for m, _ in notion.log[mark:] if m in ("APPEND", "DELETE", "PATCH")], [])
        self.assertEqual(self.shape(notion), first)
        notion.children["bills-callout"][1] = text_block(notion, "bills-status-old", "paragraph", "Updated 10:07 PM CT · 0 due through one week from now · 27 bills due · 27 overdue · Bill Tracker")
        counts = go(notion)
        self.assertEqual(counts["blocks_written"], 1)                                                                       # only the changed KPI number remains in the DCC
        self.assertEqual({k: [b["id"] for b in notion.children[k]] for k in ids}, ids)                                      # no block was added, removed or replaced
        self.assertEqual(sum(1 for m, _ in notion.log if m == "APPEND"), made)                                              # only the first run created blocks

    def test_the_command_center_text_says_what_the_cards_said(self):
        notion = page()
        go(notion)
        text = str(self.shape(notion))
        self.assertNotIn("Primary focus", text)
        self.assertNotIn("Suggested next decisions", text)
        self.assertIn("Hiring Pipeline unset", text)
        self.assertIn("Source health and freshness · 6 of 7 current · Hiring Pipeline UNSET", text)
        self.assertNotIn("Current focus", text)

    def test_an_install_that_does_not_read_back_complete_keeps_the_old_lines_and_fails(self):
        notion = page()
        real = notion.call_once

        def call_once(method, path, body=None):
            parent = next((b for blocks in notion.children.values() for b in blocks if b["id"] == path.split("/")[2]), {})
            if parent.get("type") == "callout":
                result = real(method, path, body)
                toggle = next(b for b in notion.children["dcc-callout"] if b["type"] == "toggle")
                notion.children[toggle["id"]] = []
                return result
            if parent.get("type") == "toggle":                                      # Notion acknowledges the append but the toggle's lines never appear
                return {"results": [{"id": "ghost", "type": "paragraph"}]}
            return real(method, path, body)
        notion.call_once = call_once
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertIn("CARD_VERIFY_FAILED", str(error.exception))
        self.assertIn("Protected DCC content", "\n".join(lines_of(notion)))             # the old lines are removed only after the replacement is verified
        self.assertEqual([p for m, p in notion.log if m == "DELETE"], [])

    def test_the_exact_legacy_decision_widget_is_removed_and_protected_regions_survive(self):
        notion = page()
        go(notion)
        stale = card._b("callout", children=[card._b("paragraph", card._t("Suggested next decisions", bold=True)), card._b("paragraph", card._t("legacy"))])
        notion.children["dcc-callout"].append(notion._make(stale))
        text = "Updated 10:05 PM CT · 23 active exceptions\n\nSuggested next decisions\n1. Triage overdue bills"            # the Attention card has written the decisions
        notion.metas["attention-callout"]["callout"] = {"rich_text": [{"plain_text": text, "text": {"content": text}}]}
        before = notion.full_tree("attention-callout")
        go(notion)
        self.assertNotIn("Suggested next decisions", str(self.shape(notion)))
        self.assertEqual(notion.full_tree("attention-callout"), before)

    def test_the_old_decision_widget_is_never_deleted_before_the_attention_card_holds_the_decisions(self):
        notion = page()
        go(notion)
        stale = card._b("callout", children=[card._b("paragraph", card._t("Suggested next decisions", bold=True)), card._b("paragraph", card._t("legacy"))])
        notion.children["dcc-callout"].append(notion._make(stale))
        mark = len(notion.log)
        with self.assertRaises(NotionError) as error:                       # the Attention card has not written them (its step failed or has not run)
            go(notion)
        self.assertIn("DECISIONS_NOT_IN_ATTENTION", str(error.exception))
        self.assertEqual(self.writes_since(notion, mark), [])
        self.assertIn("legacy", str(self.shape(notion)))

    def writes_since(self, notion, mark):
        return [m for m, _ in notion.log[mark:] if m in ("APPEND", "DELETE", "PATCH")]

    def test_a_foreign_child_inside_a_valid_component_is_never_deleted_and_nothing_is_written(self):
        notion = page()
        go(notion)
        kpi_row = notion.children["dcc-callout"][2]["id"]
        kpi = notion.children[notion.children[notion.children[kpi_row][3]["id"]][0]["id"]]               # the Mail waiting card's blocks
        kpi.append(text_block(notion, "human-note", "paragraph", "my own note"))
        mark = len(notion.log)
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertIn("CARD_STRUCTURE_CHANGED", str(error.exception))
        self.assertEqual(self.writes_since(notion, mark), [])
        self.assertIn("my own note", str(self.shape(notion)))

    def test_a_later_branch_mismatch_stops_everything_even_when_an_earlier_branch_has_a_pending_change(self):
        notion = page()
        go(notion)
        kpi_row = notion.children["dcc-callout"][2]["id"]
        last = notion.children[notion.children[notion.children[kpi_row][3]["id"]][0]["id"]]
        last[0]["type"], last[0]["paragraph"] = "paragraph", {"rich_text": []}                         # the last card's number block was changed
        notion.children["bills-callout"][1] = text_block(notion, "bills-status-old", "paragraph", "Updated 10:07 PM CT · 0 due · 7 bills due · 7 overdue · Bill Tracker")      # an earlier card has new data
        mark = len(notion.log)
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertIn("CARD_STRUCTURE_CHANGED", str(error.exception))
        self.assertEqual(self.writes_since(notion, mark), [])

    def test_a_block_that_is_not_ours_or_a_changed_structure_stops_the_write(self):
        notion = page()
        notion.children["dcc-callout"].append({"id": "extra-view", "type": "child_database", "has_children": False, "child_database": {}})
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertIn("CARD_NOT_OWNED", str(error.exception))
        self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE", "PATCH")], [])
        notion = page()
        go(notion)
        kpi_row = notion.children["dcc-callout"][2]["id"]
        kpi = notion.children[notion.children[notion.children[kpi_row][0]["id"]][0]["id"]]
        first = kpi[0]
        first["type"], first["paragraph"] = "paragraph", {"rich_text": []}                                         # somebody changed the KPI's heading structure
        mark = len(notion.log)
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertIn("CARD_STRUCTURE_CHANGED", str(error.exception))
        self.assertEqual([m for m, _ in notion.log[mark:] if m in ("APPEND", "DELETE", "PATCH")], [])

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
