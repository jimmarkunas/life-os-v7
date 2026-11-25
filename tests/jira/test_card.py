import json
import unittest
from unittest.mock import patch
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.jira import card
from lifeos.jira.card import CardError
from tests.kit.notion import FakeBlocks

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
ENV = {"JIRA_BOARDS": "AAA:11", "JIRA_CARD_BLOCK_ID": "card-1", "NOTION_JIRA_TOKEN": "x", "JIRA_SITE_URL": "https://example.invalid"}


def issue(key, status="To Do", priority="Medium", due=None, parent=None, category="new", summary="Private summary"):
    return {"key": key, "summary": summary, "status": status, "category": category, "type": "Task", "priority": priority,
            "due": due, "parent": parent, "parent_summary": "Parent work" if parent else "", "project": "AAA", "assignee": None}


def snapshot(taken=NOW, **over):
    snap = {"schema": 3, "project": "AAA", "taken_at": taken.isoformat(), "current_sprint": {"name": "AAA Sprint 1"},
            "current_tasks": [issue("AAA-1", priority="High"), issue("AAA-2"), issue("AAA-3", parent="AAA-9"), issue("AAA-4", parent="AAA-9"),
                              issue("AAA-9"), issue("AAA-5", due="2026-10-07")],
            "overdue": [issue("AAA-6", due="2026-10-01")], "blocked": [issue("AAA-7", status="Blocked")],
            "triage": [], "done": [issue("AAA-8", status="Done", category="done")]}
    snap.update(over)
    return snap



def heading(text="JIRA Execution"):
    return {"id": "h", "type": "heading_4", "heading_4": {"rich_text": [{"plain_text": text}]}}


def old(i):
    return {"id": f"old{i}", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "chatgpt wrote this"}]}}


_patchers = []


def conn(snaps):
    class Ctx:
        def __enter__(self): return self
        def __exit__(self, *e): return False
    patcher = patch.object(card.store, "load", lambda connection, project: snaps.get(project))
    patcher.start()
    _patchers.append(patcher)
    return Ctx


def texts(body):
    return [card._plain(b) for b in body]


class BucketTests(unittest.TestCase):
    def test_precedence_leaves_only_and_nothing_lost(self):
        b = card.buckets(snapshot(), date(2026, 10, 7))
        keys = {name: [i["key"] for i in items] for name, items in b.items()}
        self.assertEqual(keys["overdue"], ["AAA-6"])
        self.assertEqual(keys["blocked"], ["AAA-7"])
        self.assertEqual(sorted(keys["today"]), ["AAA-1", "AAA-5"])
        self.assertEqual(sorted(keys["this_week"]), ["AAA-2", "AAA-3", "AAA-4"])     # parent AAA-9 suppressed, its children shown
        self.assertEqual(keys["done"], ["AAA-8"])

    def test_a_ticket_that_is_both_overdue_and_blocked_is_listed_under_blocked_only_and_overdue_is_oldest_first(self):
        both = issue("AAA-20", status="Blocked", due="2026-09-01")
        snap = snapshot(current_tasks=[issue("AAA-21", due="2026-10-05"), issue("AAA-22", due="2026-09-15"), both], overdue=[both, issue("AAA-22", due="2026-09-15")], blocked=[both])
        b = card.buckets(snap, date(2026, 10, 7))
        self.assertEqual([i["key"] for i in b["blocked"]], ["AAA-20"])
        self.assertEqual([i["key"] for i in b["overdue"]], ["AAA-22", "AAA-21"])

    def test_the_visa_epic_is_left_out_of_every_list_and_count(self):
        visa = [issue("AAA-165", status="In Progress"), issue("AAA-166", due="2026-09-01", parent="AAA-165"), issue("AAA-167", parent="AAA-166", status="Blocked")]
        snap = snapshot(gtv=visa, current_tasks=[issue("AAA-1", priority="High"), issue("AAA-168", parent="AAA-165", priority="High"), *visa[:1]],
                        overdue=[visa[1]], blocked=[visa[2]], done=[issue("AAA-169", parent="AAA-165", status="Done", category="done"), issue("AAA-8", status="Done", category="done")])
        b = card.buckets(snap, date(2026, 10, 7), "AAA-165")
        every = [i["key"] for items in b.values() for i in items]
        self.assertEqual(sorted(every), ["AAA-1", "AAA-8"])
        unset = dict(snap, gtv=None)
        self.assertIn("AAA-168", [i["key"] for i in card.buckets(unset, date(2026, 10, 7))["today"]])          # no visa epic configured: nothing is left out

    def test_a_goal_due_on_the_last_day_of_the_quarter_is_left_out_of_today_and_capacity_but_shows_under_overdue_when_late(self):
        goal = issue("AAA-30", priority="High", due="2026-12-31")
        snap = snapshot(current_tasks=[goal, issue("AAA-31", priority="High")], overdue=[], blocked=[])
        self.assertEqual([i["key"] for i in card.buckets(snap, date(2026, 11, 25))["today"]], ["AAA-31"])
        self.assertEqual(card.quarter_end(date(2026, 11, 25)), date(2026, 12, 31))
        self.assertEqual((card.quarter_end(date(2026, 3, 31)), card.quarter_end(date(2026, 7, 1)), card.quarter_end(date(2026, 12, 31))), (date(2026, 3, 31), date(2026, 9, 30), date(2026, 12, 31)))
        late = card.buckets(snap, date(2027, 1, 2))
        self.assertIn("AAA-30", [i["key"] for i in late["overdue"]])
        body, counts = card.render([snap], date(2026, 11, 25), NOW, None)
        self.assertEqual((counts["today"], counts["this_week"]), (1, 0))                                       # not pushed into this week's number either
        self.assertIn("Capacity 1/3", "".join(texts(body)))

    def test_this_week_counts_a_non_epic_parents_children_once_and_an_epics_children_one_each(self):
        kids = [dict(issue(f"AAA-{n}", parent="AAA-70"), parent_type="Task") for n in range(100, 170)]       # a parent with 70 children
        epic = [dict(issue(f"AAA-{n}", parent="AAA-71"), parent_type="Epic") for n in range(171, 174)]
        alone = [issue("AAA-180")]
        snap = snapshot(current_tasks=kids + epic + alone, overdue=[], blocked=[])
        self.assertEqual(card.week_count(card.buckets(snap, date(2026, 10, 7))["this_week"]), 1 + 3 + 1)
        body, counts = card.render([snap], date(2026, 10, 7), NOW, None)
        self.assertEqual(counts["this_week"], 5)
        self.assertIn("5 This Week", texts(body)[0])

    def test_today_capacity_flags_reprioritize(self):
        many = [issue(f"AAA-{n}", priority="High") for n in range(20, 24)]
        body, counts = card.render([snapshot(current_tasks=many, overdue=[], blocked=[])], date(2026, 10, 7), NOW, None)
        self.assertEqual(counts["today"], 4)
        self.assertIn("RE-PRIORITIZE", json.dumps(body))


class LayoutTests(unittest.TestCase):
    def test_the_block_is_the_approved_layout_with_no_this_week_list_and_no_gtv_line(self):
        gtv = snapshot(gtv=[issue("AAA-50")])["gtv"]
        body, counts = card.render([snapshot(gtv=gtv)], date(2026, 10, 7), NOW, "https://example.invalid", "AAA-50")
        top = [(b["type"], card._plain(b)) for b in body if b["type"] in ("paragraph", "heading_4", "toggle")]
        self.assertEqual(top[0], ("paragraph", "AAA Sprint 1 \u00b7 1 Overdue \u00b7 1 Blocked \u00b7 2 Today \u00b7 2 This Week \u00b7 1 Done"))
        self.assertEqual([t for k, t in top[1:]], ["Overdue \u2014 1", "Blocked \u2014 1", "Today \u2014 2 \u00b7 Capacity 2/3", "Done \u2014 1"])
        text = json.dumps(body)
        for gone in ("This Week \u2014", "Show This Week", "GTV Action", "Global Talent Visa", "AAA-50"):
            self.assertNotIn(gone, text)
        self.assertEqual((counts["overdue"], counts["blocked"]), (1, 1))

    def test_blocked_rows_carry_no_status_tag_and_overdue_rows_carry_due_and_status(self):
        body, _ = card.render([snapshot()], date(2026, 10, 7), NOW, "https://example.invalid")
        rows = [card._plain(b) for b in body if b["type"] == "bulleted_list_item"]
        self.assertIn("AAA-6 \u2014 Private summary \u00b7 due Oct 1 \u00b7 To Do", rows)
        self.assertIn("AAA-7 \u2014 Private summary", rows)
        self.assertNotIn("blocked", " ".join(rows))

    def test_the_sections_group_under_their_epic_or_parent_with_links(self):
        snap = snapshot(current_tasks=[], overdue=[issue("AAA-40", due="2026-10-01", parent="AAA-9"), issue("AAA-41", due="2026-10-02", parent="AAA-9")], blocked=[])
        body, _ = card.render([snap], date(2026, 10, 7), NOW, "https://example.invalid")
        text = json.dumps(body)
        self.assertEqual(text.count("AAA-9 \\u2014 Parent work"), 1)                                           # the parent once, as a header over both children
        self.assertIn("https://example.invalid/browse/AAA-9", text)


class EpicHeaderTests(unittest.TestCase):
    def test_epic_parents_are_labelled_and_task_parents_are_not(self):
        leaf = dict(issue("AAA-80", parent="AAA-1"), parent_type="Epic", parent_summary="Health")
        sub = dict(issue("AAA-81", parent="AAA-2"), parent_type="Task", parent_summary="Work package")
        text = json.dumps(card._rows([leaf, sub], None))
        self.assertIn("Epic \\u00b7 AAA-1", text)
        self.assertNotIn("Epic \\u00b7 AAA-2", text)


class GtvTests(unittest.TestCase):
    def tearDown(self):
        while _patchers:
            _patchers.pop().stop()

    def gtv(self, *items):
        return snapshot(gtv=list(items))

    def test_priority_then_due_then_rank_and_blocked_never_chosen(self):
        snap = self.gtv(issue("AAA-50", status="Blocked", priority="Highest"), issue("AAA-51"), issue("AAA-52", priority="High"),
                        issue("AAA-53", priority="High", due="2026-10-09"))
        self.assertEqual(card.gtv_action(snap, None)["key"], "AAA-53")           # High beats Medium; earlier due beats none
        self.assertEqual(card.gtv_action(snap, "AAA-51")["key"], "AAA-51")       # sticky until done or blocked
        self.assertEqual(card.gtv_action(snap, "AAA-50")["key"], "AAA-53")       # a blocked sticky is dropped

    def test_leaves_only_and_done_work_is_ignored(self):
        snap = self.gtv(issue("AAA-60"), issue("AAA-61", parent="AAA-60"), issue("AAA-62", category="done"))
        self.assertEqual(card.gtv_action(snap, None)["key"], "AAA-61")

    def test_no_valid_action_says_needs_jira(self):
        parts, chosen = card.gtv_parts(self.gtv(), None, None, None)
        self.assertIn("Needs Jira", "".join(p["text"]["content"] for p in parts))
        self.assertIsNone(chosen)

    def callout(self, line="GTV Action \u00b7 AAA-71 \u2014 Private summary"):
        head = {"id": "gh", "type": "heading_4", "heading_4": {"rich_text": [{"plain_text": "\U0001F1EC\U0001F1E7 Global Talent Visa"}]}}
        para = {"id": "gp", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": line}]}}
        return FakeBlocks([head, para])

    def test_the_visa_callout_line_is_rewritten_in_place_and_keeps_the_sticky_choice(self):
        snaps = {"AAA": self.gtv(issue("AAA-70", priority="High"), issue("AAA-71", due="2026-10-20", status="In Progress"))}
        visa = self.callout()
        client = FakeBlocks([heading(), old(1)])
        out = card.run(1, True, environ=dict(ENV, GTV_CARD_BLOCK_ID="gtv-card", JIRA_GTV_EPIC="AAA-1", JIRA_GTV_CONTEXT_URL="https://example.invalid/ctx"), client=client, gtv_client=visa, now=NOW, connect=conn(snaps))
        self.assertEqual((out["gtv"], out["gtv_written"]), (1, 1))
        self.assertEqual([k["id"] for k in visa.kids], ["gh", "gp"])                                           # same blocks, only the text changed
        line = card._plain(visa.kids[1])
        self.assertIn("AAA-71", line)                                                                         # not AAA-70: the earlier pick stays until done
        self.assertIn("due Oct 20 \u00b7 In Progress", line)
        self.assertIn("GTV Notion Context", line)
        self.assertEqual(visa.log.count("APPEND") + visa.log.count("DELETE"), 0)                               # nothing created, nothing deleted
        self.assertNotIn("GTV Action", json.dumps(client.kids))                                                # and nothing of it inside Jira Execution

    def test_a_second_run_changes_nothing_and_a_dry_run_writes_nothing(self):
        snaps = {"AAA": self.gtv(issue("AAA-71"))}
        env = dict(ENV, GTV_CARD_BLOCK_ID="gtv-card", JIRA_GTV_EPIC="AAA-1")
        visa = self.callout("GTV Action \u00b7 AAA-71 \u2014 Private summary \u00b7 To Do")
        out = card.run(1, True, environ=env, client=FakeBlocks([heading()]), gtv_client=visa, now=NOW, connect=conn(snaps))
        self.assertEqual(out["gtv_written"], 0)
        dry = self.callout("GTV Action \u00b7 AAA-70 \u2014 old")
        card.run(1, False, environ=env, client=FakeBlocks([heading()]), gtv_client=dry, now=NOW, connect=conn(snaps))
        self.assertEqual(set(dry.log), {"GET"})

    def test_a_callout_that_is_not_the_visa_callout_or_has_no_line_is_never_written(self):
        snaps = {"AAA": self.gtv(issue("AAA-71"))}
        env = dict(ENV, GTV_CARD_BLOCK_ID="gtv-card")
        for kids, code in (([heading("Something else"), {"id": "p", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "GTV Action \u00b7 x"}]}}], "JIRA_CARD_GTV_NOT_OWNED"),
                           ([{"id": "gh", "type": "heading_4", "heading_4": {"rich_text": [{"plain_text": "Global Talent Visa"}]}}], "JIRA_CARD_GTV_NO_LINE")):
            visa, client = FakeBlocks(kids), FakeBlocks([heading(), old(1)])
            with self.assertRaises(CardError) as ctx:
                card.run(1, True, environ=env, client=client, gtv_client=visa, now=NOW, connect=conn(snaps))
            self.assertEqual(str(ctx.exception), code)
            self.assertEqual(set(visa.log), {"GET"})
            self.assertEqual(client.kids[0]["id"], "h")                                                       # the Jira card itself was still written before the stage failed
            self.assertTrue(any(k["type"] == "bulleted_list_item" or k["type"] == "toggle" for k in client.kids))

    def test_no_visa_block_setting_means_the_line_is_not_written_anywhere(self):
        out = card.run(1, True, environ=ENV, client=FakeBlocks([heading(), old(1)]), now=NOW, connect=conn({"AAA": self.gtv(issue("AAA-71"))}))
        self.assertNotIn("gtv", out)


class WriteTests(unittest.TestCase):
    def tearDown(self):
        while _patchers:
            _patchers.pop().stop()

    def run_card(self, kids, live=True, snaps=None, now=NOW):
        snaps = snaps if snaps is not None else {"AAA": snapshot()}
        client = FakeBlocks(kids)
        out = card.run(1, live, environ=ENV, client=client, now=now, connect=conn(snaps))
        return out, client

    def test_dry_run_reads_only(self):
        out, client = self.run_card([heading(), old(1)], live=False)
        self.assertEqual((out["written"], out["stale"]), (0, 0))
        self.assertEqual(set(client.log), {"GET"})

    def test_live_replaces_everything_after_the_heading_and_verifies(self):
        out, client = self.run_card([heading(), old(1), old(2)])
        self.assertEqual((out["written"], out["removed"], out["overdue"], out["blocked"], out["today"], out["this_week"], out["done"]),
                         (1, 2, 1, 1, 2, 2, 1))
        self.assertEqual(client.kids[0]["id"], "h")
        self.assertEqual(client.kids[0]["heading_4"]["rich_text"][0]["plain_text"], "JIRA Execution (10/7 · 9:00 AM CT)")   # the update time shares the heading
        self.assertFalse(any(card._plain(k).startswith(("V7 ·", "Updated")) for k in client.kids[1:]))                   # no separate status line
        self.assertLess(client.log.index("APPEND"), client.log.index("DELETE"))      # add first, remove after

    def test_a_block_that_does_not_open_with_the_heading_is_never_touched(self):
        client = FakeBlocks([old(1)])
        with self.assertRaises(CardError) as ctx:
            card.run(1, True, environ=ENV, client=client, now=NOW, connect=conn({"AAA": snapshot()}))
        self.assertEqual(str(ctx.exception), "JIRA_CARD_NOT_OWNED")
        self.assertEqual(client.log, ["GET"])

    def test_stale_snapshot_only_marks_the_heading(self):
        out, client = self.run_card([heading("JIRA Execution (Updated 6:00 AM CT)"), old(1), old(2)], snaps={"AAA": snapshot(taken=datetime(2026, 10, 7, 4, 0, tzinfo=TZ))})
        self.assertEqual((out["stale"], out["written"]), (1, 1))
        self.assertEqual(client.kids[0]["heading_4"]["rich_text"][0]["plain_text"], "JIRA Execution (STALE · last accepted 10/7 · 4:00 AM CT)")
        self.assertEqual(len(client.kids), 3)                          # no content rewritten, no false zero

    def test_the_combined_heading_is_still_ours_and_lookalikes_are_not(self):
        self.assertTrue(card._owned([heading("JIRA Execution (Updated 2:10 PM CT)")]))
        for text in ("JIRA Execution notes", "JIRA Executions (Updated 2:10 PM CT)", "JIRA Execution (Updated 2:10 PM CT) extra"):
            self.assertFalse(card._owned([heading(text)]), text)
        out, client = self.run_card([heading("JIRA Execution (Updated 6:00 AM CT)"), old(1)])        # last hour's combined heading
        self.assertEqual(out["written"], 1)
        self.assertEqual(client.kids[0]["id"], "h")                                                  # the same heading block, edited in place

    def test_stale_without_a_baseline_and_missing_snapshot_fail_closed(self):
        with self.assertRaises(CardError) as ctx:
            self.run_card([heading(), old(1)], snaps={"AAA": snapshot(taken=datetime(2026, 10, 7, 1, 0, tzinfo=TZ))})
        self.assertEqual(str(ctx.exception), "JIRA_CARD_STALE_NO_BASELINE")
        with self.assertRaises(CardError) as ctx:
            self.run_card([heading()], snaps={})
        self.assertEqual(str(ctx.exception), "JIRA_CARD_NO_SNAPSHOT")

    def test_config_codes(self):
        with self.assertRaises(CardError):
            card.run(1, False, environ={"JIRA_BOARDS": "AAA:1"}, client=FakeBlocks([]), now=NOW)
        self.assertEqual(card.card_projects({"JIRA_BOARDS": "AAA:1,BBB:2"}), ["AAA"])
        with self.assertRaises(CardError):
            card.card_projects({"JIRA_BOARDS": "AAA:1", "JIRA_CARD_PROJECTS": "ZZZ"})
        self.assertIsNone(card.site_url({"JIRA_BASE_URL": "https://api.example.invalid/ex/jira/abc"}))

    def test_logs_are_counts_only(self):
        out, _ = self.run_card([heading(), old(1)])
        text = json.dumps(out)
        for private in ("AAA", "Private summary", "card-1"):
            self.assertNotIn(private, text)


if __name__ == "__main__":
    unittest.main()
