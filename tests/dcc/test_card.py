"""D175: the Daily Command Center is written by V7 from what the other cards say on the page."""
import unittest
import unittest.mock
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

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


def writes(notion, mark=0):
    return [m for m, _ in notion.log[mark:] if m in ("APPEND", "DELETE", "PATCH")]


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
        self.assertEqual(card.classify({"status": "DEGRADED · Amazon sync failed at 10:05 PM CT · showing the last accepted orders"}, NOW), ("degraded", "10:05 PM"))
        self.assertEqual(card.classify({"status": "STALE · last accepted Oct 11 9:44 AM CT"}, NOW)[0], "stale")
        self.assertEqual(card.classify({"status": "no stamp here"}, NOW), ("unknown", None))
        self.assertEqual(card.classify({"unreadable": True}, NOW)[0], "unreadable")
        self.assertEqual(card.classify({"unset": True}, NOW)[0], "unset")


class DatedStampTests(unittest.TestCase):
    """LIFE-660: the heading stamp carries the date (`Updated Oct 12 · 10:06 PM CT`); the Command Center reads it, still reads the old time-only form, and uses the real date."""

    def fresh(self, text, now):
        return card.classify({"status": text}, now)

    def test_the_dated_form_is_read_and_the_time_only_form_still_is(self):
        self.assertEqual(self.fresh("Updated Oct 12 · 10:06 PM CT · x", NOW), ("fresh", "10:06 PM"))
        self.assertEqual(self.fresh("Calendar (Updated Oct 12 · 10:06 PM CT)", NOW), ("fresh", "10:06 PM"))
        self.assertEqual(self.fresh("Updated 10:06 PM CT · x", NOW), ("fresh", "10:06 PM"))
        self.assertEqual(self.fresh("Updated Oct 12 · 8:50 PM CT", NOW)[0], "fresh")                         # exactly 90 minutes
        self.assertEqual(self.fresh("Updated Oct 12 · 8:49 PM CT", NOW)[0], "stale")                          # 91
        self.assertEqual(self.fresh("DEGRADED · Amazon sync failed at Oct 12 · 10:05 PM CT · x", NOW), ("degraded", "10:05 PM"))
        self.assertEqual(self.fresh("STALE · last accepted Oct 11 · 9:44 AM CT", NOW)[0], "stale")

    def test_a_stamp_from_yesterday_at_the_same_clock_time_is_stale_by_its_date(self):
        self.assertEqual(self.fresh("Updated Oct 11 · 10:06 PM CT", NOW)[0], "stale")                         # the time-only form would have read fresh
        self.assertEqual(self.fresh("Updated Oct 10 · 10:06 PM CT", NOW)[0], "stale")

    def test_a_stamp_across_midnight(self):
        after_midnight = datetime(2026, 10, 13, 0, 30, tzinfo=TZ)
        self.assertEqual(self.fresh("Updated Oct 12 · 11:30 PM CT", after_midnight)[0], "fresh")             # 60 minutes ago, the day before
        self.assertEqual(self.fresh("Updated Oct 12 · 10:50 PM CT", after_midnight)[0], "stale")             # 100 minutes
        self.assertEqual(self.fresh("Updated Oct 13 · 12:05 AM CT", after_midnight)[0], "fresh")

    def test_a_stamp_across_a_month_end_and_a_year_end(self):
        self.assertEqual(self.fresh("Updated Oct 31 · 11:30 PM CT", datetime(2026, 11, 1, 0, 40, tzinfo=TZ))[0], "fresh")
        self.assertEqual(self.fresh("Updated Oct 31 · 9:30 PM CT", datetime(2026, 11, 1, 0, 40, tzinfo=TZ))[0], "stale")
        self.assertEqual(self.fresh("Updated Feb 28 · 11:45 PM CT", datetime(2026, 3, 1, 0, 10, tzinfo=TZ))[0], "fresh")   # 2026 is not a leap year
        self.assertEqual(self.fresh("Updated Dec 31 · 11:45 PM CT", datetime(2027, 1, 1, 0, 10, tzinfo=TZ))[0], "fresh")
        self.assertEqual(self.fresh("Updated Dec 30 · 11:45 PM CT", datetime(2027, 1, 1, 0, 10, tzinfo=TZ))[0], "stale")

    def test_a_date_that_is_not_a_date_is_never_guessed_fresh(self):
        self.assertEqual(self.fresh("Updated Foo 12 · 10:06 PM CT", NOW), ("unknown", None))
        self.assertEqual(self.fresh("Updated Feb 30 · 10:06 PM CT", NOW), ("unknown", None))


class SlowerCardTests(unittest.TestCase):
    """LIFE-612: a card the due-time table runs less often than every tick is current for its longest gap between runs plus the usual 90 minutes."""
    TABLE = {"bills": {"slots_ct": ["00:00", "06:00", "12:00", "18:00"]}, "mail": {"every_hours": 6}, "amazon": {"every_hours": 3}, "jira": {"every_hours": 1}}

    def test_the_window_is_the_longest_gap_plus_ninety_minutes_and_an_hourly_card_keeps_ninety(self):
        self.assertEqual([card.fresh_minutes(label, self.TABLE) for label in ("Bills", "Mail Alerts", "Amazon", "Jira", "Hiring Pipeline", "Calendar")], [450, 450, 270, 90, 90, 90])
        self.assertEqual(card.fresh_minutes("Bills", {"bills": {"slots_ct": ["03:00", "15:00"]}}), 90 + 12 * 60)          # uneven slots: the longest gap, wrapping past midnight

    def test_a_bills_callout_written_by_the_last_slot_still_reads_fresh_when_the_next_one_is_not_yet_due(self):
        for hours_old, state in ((5.9, "fresh"), (7.4, "fresh"), (7.6, "stale")):
            stamped = NOW - timedelta(hours=hours_old)
            line = f"Updated {stamped.strftime('%-I:%M %p')} CT · 0 due · 26 bills due"
            self.assertEqual(card.classify({"status": line}, NOW, card.fresh_minutes("Bills", self.TABLE))[0], state, hours_old)

    def test_the_slot_fixture_gives_bills_the_four_slots_and_the_command_center_says_nothing_is_wrong_when_bills_was_skipped(self):
        self.assertEqual(card.fresh_minutes("Bills", self.TABLE), 450)               # LIFE-630: the real table is hourly now (90); the slot mechanism stays proven on this fixture
        self.assertEqual(card.fresh_minutes("Bills"), 90)
        areas = ComposeTests().areas(Bills={"status": "Updated 5:07 PM CT · 0 due through one week from now · 26 bills due · 26 overdue · Bill Tracker", "lines": [], "links": {}})
        with unittest.mock.patch.object(card.due, "load_table", lambda: self.TABLE):
            composed = card.compose(areas, NOW)                                      # NOW is 10:20 PM CT: the Bills callout is 5 hours old, as when the 6 PM slot is the next one
        self.assertEqual(composed["detail"][1].split(" ")[0], "✅")
        self.assertNotIn("STALE", composed["health"])
        self.assertEqual(composed["kpis"][0][:2], ("26", "Bills due"))                 # its number is still shown, bound to its own stamp


class ComposeTests(unittest.TestCase):
    def areas(self, **over):
        link = {GTV: "https://example.invalid/browse/JFM-1"}
        base = {"Calendar": {"status": "Calendar (Updated 10:06 PM CT)", "lines": []}, "Jira": {"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": [GTV, SPRINT], "links": link},
                "Bills": {"status": "Updated 10:07 PM CT · 0 due through one week from now · 26 bills due · 26 overdue · Bill Tracker", "lines": [],
                          "links": {"Updated 10:07 PM CT · 0 due through one week from now · 26 bills due · 26 overdue · Bill Tracker": "https://example.invalid/tracker"}},
                "Mail Alerts": {"status": "Updated 10:12 PM CT · 13 waiting · 0 need review", "lines": []},
                "Amazon": {"status": "Updated 10:08 PM CT · 0 in transit", "lines": []},
                "Hiring Pipeline": {"status": "Updated 10:13 PM CT · 5 active", "lines": []}}
        base.update(over)
        return base

    def test_everything_fresh_binds_each_number_to_its_own_card_and_links_the_real_records(self):
        m = card.compose(self.areas(), NOW)
        self.assertEqual(m["health"], "Source health and freshness · 6 of 6 current")
        self.assertIn("Calendar 10:06 PM", m["detail"][0])
        self.assertTrue(m["detail"][1].startswith("✅ 6 of 6 source cards current"))
        self.assertFalse({"focus", "sub", "items", "hiring"} & set(m))
        self.assertEqual([(v, n) for v, n, _ in m["kpis"]], [("26", "Bills due"), ("6", "Jira Overdue / Blocked"), ("13", "Mail waiting")])
        self.assertEqual(m["kpis"][1][2], "Jira · 10:05 PM CT")                                          # the combined count, labelled as such, with its source stamp

    def test_a_degraded_or_stale_card_is_named_in_the_closed_summary_and_never_shows_a_number_or_a_zero(self):
        areas = self.areas(Amazon={"status": "DEGRADED · Amazon sync failed at 10:05 PM CT · showing the last accepted orders", "lines": []},
                           Bills={"status": "STALE · last accepted Oct 11 9:44 AM CT · 4 bills due · 4 overdue", "lines": []})
        m = card.compose(areas, NOW)
        self.assertEqual(m["health"], "Source health and freshness · 4 of 6 current · Bills STALE · Amazon DEGRADED")
        self.assertTrue(m["detail"][1].startswith("⚠️ 4 of 6"))
        self.assertEqual([(v, t) for v, n, t in m["kpis"] if n == "Bills due"], [("—", "STALE · Bills (last 9:44 AM)")])

    def test_a_missing_gtv_action_or_an_unreadable_jira_card_says_so_instead_of_choosing_one(self):
        for jira in ({"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": [SPRINT]}, {"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": ["GTV Action · JFM-2 — No status yet"]}, {"unreadable": True}):
            m = card.compose(self.areas(Jira=jira), NOW)
            self.assertFalse({"focus", "sub"} & set(m))
        self.assertEqual(m["kpis"][1][2], "UNREADABLE · Jira")

    def test_nothing_flagged_says_so_and_a_fresh_zero_is_a_zero(self):
        areas = self.areas(Bills={"status": "Updated 10:07 PM CT · 0 due · 0 bills due · 0 overdue", "lines": []},
                           Jira={"status": "JIRA Execution (Updated 10:05 PM CT)", "lines": ["JFM Sprint 15 · 0 Overdue/Blocked · 1 Today"]})
        areas["Mail Alerts"] = {"status": "Updated 10:12 PM CT · 0 waiting · 0 need review", "lines": []}
        m = card.compose(areas, NOW)
        self.assertEqual([v for v, _, _ in m["kpis"]], ["0", "0", "0"])                   # a verified zero is a zero, Jira included

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

    def test_render_places_source_health_under_heading_above_three_kpis(self):
        health, kpi_row = card.render(card.compose(self.areas(), NOW))
        self.assertEqual(health["type"], "toggle")
        self.assertEqual([c["column"]["width_ratio"] for c in kpi_row["column_list"]["children"]], [0.3333] * 3)
        kpis = [c["column"]["children"][0]["callout"] for c in kpi_row["column_list"]["children"]]
        self.assertEqual([[b["type"] for b in k["children"]] for k in kpis], [["heading_3", "paragraph", "paragraph"]] * 3)
        self.assertEqual([k["icon"]["external"]["url"].rsplit("/", 1)[1] for k in kpis], ["credit-card_gray.svg", "arrow-right_gray.svg", "inbox_gray.svg"])


class RunTests(unittest.TestCase):
    def shape(self, notion):
        """The Command Center's blocks as the page shows them: type and text, nested, in order."""
        def walk(block_id):
            return [(b["type"], report_region.plain(b), walk(b["id"])) for b in notion.children.get(block_id, [])]
        return walk("dcc-callout")

    def test_a_card_with_its_stamp_in_the_heading_reads_the_same_as_one_with_it_in_the_first_line(self):          # LIFE-609
        old, new = page(), page()
        for key, title, stamp, body in (("bills", "Bills: This Week", "Updated 10:07 PM CT", "0 due through one week from now · 26 bills due · 26 overdue · Bill Tracker"),
                                        ("mail", "Mail Alerts", "Updated 10:12 PM CT", "13 waiting · 0 need review"),
                                        ("amazon", "Amazon Orders", "Updated 10:08 PM CT", "0 in transit · 2 delivered (last 7 days) · 0 need review")):
            new.children[f"{key}-callout"][0] = text_block(new, f"heading-{key}-callout", "heading_3", f"{title} ({stamp})")
            new.children[f"{key}-callout"][1] = text_block(new, f"{key}-status-old", "paragraph", body)
        before, after = card.read_areas(old, ENV), card.read_areas(new, ENV)
        for label in ("Bills", "Mail Alerts", "Amazon"):
            self.assertEqual(after[label]["status"], before[label]["status"], label)
            self.assertEqual(card.classify(after[label], NOW), card.classify(before[label], NOW))
            self.assertEqual(card.classify(after[label], NOW)[0], "fresh")
        degraded = page()
        degraded.children["mail-callout"][0] = text_block(degraded, "heading-mail-callout", "heading_3", "Mail Alerts (DEGRADED at 10:12 PM CT)")
        degraded.children["mail-callout"][1] = text_block(degraded, "mail-status-old", "paragraph", "Physical Mail sync failed · showing last accepted state (Oct 12, 9:12 PM CT) · 13 waiting · 0 need review")
        self.assertEqual(card.classify(card.read_areas(degraded, ENV)["Mail Alerts"], NOW), ("degraded", "10:12 PM"))

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
        self.assertEqual([len(notion.children[c["id"]]) for c in notion.children[kpi_row]], [1, 1, 1])
        self.assertEqual({k: notion.full_tree(k) for k in before}, before)
        self.assertEqual((counts["fresh"], counts["not_current"], counts["removed"]), (5, 1, 1))
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
        self.assertEqual(writes(notion, mark), [])
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
        self.assertFalse(any(t in text for t in ("Primary focus", "Suggested next decisions", "Current focus")))
        self.assertIn("Hiring Pipeline unset", text)
        self.assertIn("Source health and freshness · 5 of 6 current · Hiring Pipeline UNSET", text)

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

    def test_a_foreign_child_inside_a_valid_component_is_never_deleted_and_nothing_is_written(self):
        notion = page()
        go(notion)
        kpi_row = notion.children["dcc-callout"][2]["id"]
        kpi = notion.children[notion.children[notion.children[kpi_row][2]["id"]][0]["id"]]               # the Mail waiting card's blocks
        kpi.append(text_block(notion, "human-note", "paragraph", "my own note"))
        mark = len(notion.log)
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertIn("CARD_STRUCTURE_CHANGED", str(error.exception))
        self.assertEqual(writes(notion, mark), [])
        self.assertIn("my own note", str(self.shape(notion)))

    def test_a_later_branch_mismatch_stops_everything_even_when_an_earlier_branch_has_a_pending_change(self):
        notion = page()
        go(notion)
        kpi_row = notion.children["dcc-callout"][2]["id"]
        last = notion.children[notion.children[notion.children[kpi_row][2]["id"]][0]["id"]]
        last[0]["type"], last[0]["paragraph"] = "paragraph", {"rich_text": []}                         # the last card's number block was changed
        notion.children["bills-callout"][1] = text_block(notion, "bills-status-old", "paragraph", "Updated 10:07 PM CT · 0 due · 7 bills due · 7 overdue · Bill Tracker")      # an earlier card has new data
        mark = len(notion.log)
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertIn("CARD_STRUCTURE_CHANGED", str(error.exception))
        self.assertEqual(writes(notion, mark), [])

    def test_a_block_that_is_not_ours_or_a_changed_structure_stops_the_write(self):
        notion = page()
        notion.children["dcc-callout"].append({"id": "extra-view", "type": "child_database", "has_children": False, "child_database": {}})
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertIn("CARD_NOT_OWNED", str(error.exception))
        self.assertEqual(writes(notion), [])
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
        self.assertEqual(writes(notion, mark), [])

    def test_a_dry_run_and_a_missing_secret_write_nothing(self):
        notion = page()
        counts = go(notion, live=False)
        self.assertEqual(writes(notion), [])
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
        # LIFE-586: the write no longer reads the other regions to prove them unchanged, so an unreadable Bills region does not stop the card; the card shows it as unreadable
        go(notion)
        areas = card.read_areas(notion, ENV)
        self.assertEqual(areas["Bills"], {"unreadable": True})
        self.assertIn("status", areas["Jira"])

    def test_nothing_readable_writes_nothing_and_says_why(self):
        notion = page()
        notion.call = lambda *a, **k: (_ for _ in ()).throw(NotionError("NOTION_HTTP_500"))
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertEqual(str(error.exception), "DCC_NO_EVIDENCE")

    def test_another_cards_write_during_ours_no_longer_fails_the_run(self):
        """LIFE-586: Jira's callout changing while the DCC card writes is another card doing its own job, not a violation."""
        notion = page(change_on="APPEND", change_region="jira-callout")
        go(notion)

    def test_a_write_into_another_cards_block_is_refused_before_it_is_sent(self):
        """LIFE-586 mutation proof for the tree writer: a sync aimed at Jira's callout fails with the out-of-scope code and Jira's block stays as it was."""
        notion = page()
        foreign = notion.children["jira-callout"][0]["id"]
        before = notion.full_tree("jira-callout")

        def buggy_sync(client, *args, **kwargs):
            client.call("PATCH", f"/blocks/{foreign}", {"heading_2": {"rich_text": [{"type": "text", "text": {"content": "overwritten"}}]}})
            return 0

        with mock.patch.object(report_region, "_sync", buggy_sync):
            with self.assertRaises(NotionError) as error:
                go(notion)
        self.assertIn("WRITE_OUT_OF_SCOPE", str(error.exception))
        self.assertEqual(notion.full_tree("jira-callout"), before)

    def test_a_wrong_target_is_refused_before_any_write(self):
        notion = page()
        with self.assertRaises(NotionError) as error:
            go(notion, env={"DCC_CARD_BLOCK_ID": "jira-callout"})
        self.assertIn("NOT_OWNED", str(error.exception))
        self.assertEqual(writes(notion), [])

    def test_the_region_has_one_owner_and_the_card_is_a_stage(self):
        self.assertEqual(router.OWNERS[router.DCC_REGION], card.MODULE)
        from lifeos import run as entry
        self.assertEqual(entry.STAGES["dcc-card"].target, ("lifeos.dcc.card", "run"))


class SurplusColumnTests(unittest.TestCase):
    """LIFE-655: the live callout still holds the retired Attention KPI (4 columns), the card renders 3: the surplus column is removed first, then the normal sync runs."""

    def live(self, notion, rename=None):
        """The page as it stands after the LIFE-645 retirement: the first run's layout with the Attention KPI back as the third of four columns."""
        go(notion)
        row = notion.children["dcc-callout"][2]["id"]
        attention = {"object": "block", "type": "column", "column": {"width_ratio": 0.25, "children": [card._b("callout", icon="arrow-up", children=[
            card._b("heading_3", card._t("23")), card._b("paragraph", card._t(rename or "Attention items", bold=True)), card._b("paragraph", card._t("Attention · 10:05 PM CT", gray=True))])]}}
        made = notion._make(attention)
        notion.children[row].insert(2, made)
        for column in notion.children[row]:
            column["column"]["width_ratio"] = 0.25
        return row, made["id"]

    def names(self, notion, row):
        return [report_region.plain(notion.children[notion.children[c["id"]][0]["id"]][1]) for c in notion.children[row]]

    def test_four_live_columns_converge_to_three_and_nothing_else_on_the_page_is_touched(self):
        notion = page()
        row, attention = self.live(notion)
        others = {k: notion.full_tree(k) for k in ("calendar-callout", "jira-callout", "bills-callout", "amazon-callout", "attention-callout", "mail-callout")}
        mark = len(notion.log)
        counts = go(notion)
        self.assertEqual(self.names(notion, row), ["Bills due", "Jira Overdue / Blocked", "Mail waiting"])
        self.assertEqual([p for m, p in notion.log[mark:] if m == "DELETE"], [f"/blocks/{attention}"])               # one DELETE, aimed at the Attention column
        self.assertEqual(counts["columns_removed"], 1)
        self.assertEqual({c["column"]["width_ratio"] for c in notion.children[row]}, {round(1 / 3, 4)})
        mail = notion.children[notion.children[row][2]["id"]][0]
        self.assertTrue(mail["callout"]["icon"]["external"]["url"].endswith("inbox_gray.svg"))                        # the Mail KPI kept its own icon, it was not rewritten into the Attention column
        self.assertEqual({k: notion.full_tree(k) for k in others}, others)
        mark = len(notion.log)
        go(notion)
        self.assertEqual(writes(notion, mark), [])

    def test_three_live_columns_are_a_no_op(self):
        notion = page()
        go(notion)
        mark = len(notion.log)
        self.assertEqual(card.converge_columns(notion, "dcc-callout", ["Bills due", "Jira Overdue / Blocked", "Mail waiting"], {}, True), 0)
        self.assertEqual(writes(notion, mark), [])
        self.assertEqual(go(notion)["columns_removed"], 0)

    def test_a_surplus_column_outside_the_cards_own_callout_is_never_touched_and_the_guard_refuses_it(self):
        notion = page()
        go(notion)
        row = notion.children["dcc-callout"][2]["id"]
        foreign = notion._make({"object": "block", "type": "column", "column": {"width_ratio": 0.25, "children": []}})
        notion.children["jira-callout"].append({"id": "foreign-row", "type": "column_list", "has_children": True, "column_list": {}})
        notion.children["foreign-row"] = [foreign, notion._make({"object": "block", "type": "column", "column": {"width_ratio": 0.25, "children": []}})]
        before, mark = notion.full_tree("jira-callout"), len(notion.log)
        go(notion)
        self.assertEqual(notion.full_tree("jira-callout"), before)
        self.assertEqual(len(notion.children["foreign-row"]), 2)
        self.assertEqual([m for m, _ in notion.log[mark:] if m == "DELETE"], [])
        scoped = report_region.ScopedClient(notion, "dcc-callout", card._fail)
        scoped.call("GET", f"/blocks/{row}/children")
        with self.assertRaises(NotionError) as error:
            scoped.call("DELETE", f"/blocks/{foreign['id']}")
        self.assertIn("WRITE_OUT_OF_SCOPE", str(error.exception))
        self.assertEqual(len(notion.children["foreign-row"]), 2)

    def test_a_delete_that_does_not_read_back_fails_with_the_fixed_code(self):
        notion = page()
        row, attention = self.live(notion)
        real = notion.call

        def call(method, path, body=None):
            return {} if method == "DELETE" and attention in path else real(method, path, body)             # Notion acknowledges the delete but the column stays
        notion.call = call
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertEqual(str(error.exception), "DCC_COLUMNS_VERIFY_FAILED")

    def test_columns_that_are_not_the_rendered_ones_are_not_deleted_blind(self):
        notion = page()
        row, attention = self.live(notion, rename="Something Jim wrote")
        notion.children[row][3]["column"]["width_ratio"] = 0.25
        notion.children[notion.children[notion.children[row][0]["id"]][0]["id"]][1] = text_block(notion, "renamed", "paragraph", "Bills overdue")       # two unknown columns, three expected
        mark = len(notion.log)
        with self.assertRaises(NotionError) as error:
            go(notion)
        self.assertEqual(str(error.exception), "DCC_COLUMNS_UNRECOGNIZED")
        self.assertEqual(writes(notion, mark), [])

    def test_a_dry_run_reports_the_surplus_and_writes_nothing(self):
        notion = page()
        row, _ = self.live(notion)
        mark = len(notion.log)
        self.assertEqual(go(notion, live=False)["columns_removed"], 1)
        self.assertEqual(writes(notion, mark), [])
        self.assertEqual(len(notion.children[row]), 4)

    def test_the_page_updated_line_is_still_written_when_the_callout_write_fails_but_not_for_a_wrong_target(self):
        with mock.patch.object(card.header, "run", return_value="updated") as stamp, mock.patch.object(report_region, "replace_tree", side_effect=NotionError("DCC_CARD_STRUCTURE_CHANGED")):
            with self.assertRaises(NotionError):
                go(page())
        self.assertEqual(stamp.call_count, 1)
        with mock.patch.object(card.header, "run", return_value="updated") as stamp:
            with self.assertRaises(NotionError):
                go(page(), env={"DCC_CARD_BLOCK_ID": "jira-callout"})
        self.assertEqual(stamp.call_count, 0)


if __name__ == "__main__":
    unittest.main()
