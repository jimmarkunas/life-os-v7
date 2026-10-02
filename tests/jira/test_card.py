import json
import unittest
from unittest.mock import patch
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.jira import card
from lifeos.jira.card import CardError

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


class FakeNotion:
    def __init__(self, kids):
        self.kids, self.log, self.n = list(kids), [], 100

    def call(self, method, path, body=None):
        self.log.append(method)
        if method == "GET":
            return {"results": [dict(k) for k in self.kids], "has_more": False}
        if method == "DELETE":
            bid = path.rsplit("/", 1)[1]
            self.kids = [k for k in self.kids if k["id"] != bid]
            return {}
        if method == "PATCH" and path.startswith("/blocks/") and "children" not in path:
            for k in self.kids:
                if k["id"] == path.rsplit("/", 1)[1]:
                    k["paragraph"] = body["paragraph"]
            return {}
        raise AssertionError(path)

    def call_once(self, method, path, body=None):
        self.log.append("APPEND")
        if path != "/blocks/card-1/children":
            return {"results": [self._made(b) for b in body["children"]]}
        made = []
        for b in body["children"]:
            made.append(self._made(b))
            self.kids.append(made[-1])
        return {"results": made}

    def _made(self, b):
        self.n += 1
        t = b["type"]
        return {"id": f"b{self.n}", "type": t, t: {"rich_text": [{"plain_text": r["text"]["content"]} for r in b[t]["rich_text"]]}}


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


class BucketTests(unittest.TestCase):
    def test_v1_precedence_leaves_only_and_nothing_lost(self):
        b = card.buckets(snapshot(), date(2026, 10, 7))
        keys = {name: [i["key"] for i in items] for name, items in b.items()}
        self.assertEqual(keys["overdue_blocked"], ["AAA-6", "AAA-7"])
        self.assertEqual(sorted(keys["today"]), ["AAA-1", "AAA-5"])
        self.assertEqual(sorted(keys["this_week"]), ["AAA-2", "AAA-3", "AAA-4"])     # parent AAA-9 suppressed, its children shown
        self.assertEqual(keys["done"], ["AAA-8"])

    def test_today_capacity_flags_reprioritize(self):
        many = [issue(f"AAA-{n}", priority="High") for n in range(20, 24)]
        body, counts = card.render([snapshot(current_tasks=many, overdue=[], blocked=[])], date(2026, 10, 7), NOW, None)
        self.assertEqual(counts["today"], 4)
        self.assertIn("RE-PRIORITIZE", json.dumps(body))

    def test_this_week_goes_behind_a_toggle_over_five_and_children_group_under_their_parent(self):
        body, _ = card.render([snapshot()], date(2026, 10, 7), NOW, "https://example.invalid")
        text = json.dumps(body)
        self.assertIn("AAA-9 \\u2014 Parent work", text)
        self.assertNotIn("Show This Week items", text)                   # 3 rows: stay expanded
        crowded = [issue(f"AAA-{n}") for n in range(30, 37)]
        body, _ = card.render([snapshot(current_tasks=crowded, overdue=[], blocked=[])], date(2026, 10, 7), NOW, None)
        self.assertIn("Show This Week items", json.dumps(body))


class EpicHeaderTests(unittest.TestCase):
    def test_epic_parents_are_labelled_and_task_parents_are_not(self):
        leaf = dict(issue("AAA-80", parent="AAA-1"), parent_type="Epic", parent_summary="Health")
        sub = dict(issue("AAA-81", parent="AAA-2"), parent_type="Task", parent_summary="Work package")
        text = json.dumps(card._rows([leaf, sub], None))
        self.assertIn("Epic \\u00b7 AAA-1", text)
        self.assertNotIn("Epic \\u00b7 AAA-2", text)


class GtvTests(unittest.TestCase):
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

    def test_no_valid_action_renders_needs_jira_and_unconfigured_renders_nothing(self):
        body, _ = card.render([self.gtv()], date(2026, 10, 7), NOW, None)
        self.assertIn("Needs Jira", json.dumps(body))
        body, counts = card.render([snapshot(gtv=None)], date(2026, 10, 7), NOW, None)
        self.assertNotIn("Global Talent Visa", json.dumps(body))
        self.assertEqual(counts["gtv"], 0)

    def test_the_card_carries_the_gtv_line_and_reuses_the_sticky_choice_from_the_previous_card(self):
        snaps = {"AAA": self.gtv(issue("AAA-70", priority="High"), issue("AAA-71"))}
        previous = {"id": "g", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "GTV Action \u00b7 AAA-71 \u2014 Private summary"}]}}
        status = {"id": "s", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "V7 \u00b7 updated 6:00 AM CT"}]}}
        client = FakeNotion([heading(), status, previous])
        out = card.run(1, True, environ=dict(ENV, JIRA_GTV_CONTEXT_URL="https://example.invalid/ctx"), client=client, now=NOW, connect=conn(snaps))
        self.assertEqual(out["gtv"], 1)
        text = json.dumps([k for k in client.kids])
        self.assertIn("GTV Action", text)
        self.assertIn("AAA-71", text)                                            # not AAA-70: the earlier pick stays until done
        self.assertNotIn("AAA-70", text)


class WriteTests(unittest.TestCase):
    def tearDown(self):
        while _patchers:
            _patchers.pop().stop()

    def run_card(self, kids, live=True, snaps=None, now=NOW):
        snaps = snaps if snaps is not None else {"AAA": snapshot()}
        client = FakeNotion(kids)
        out = card.run(1, live, environ=ENV, client=client, now=now, connect=conn(snaps))
        return out, client

    def test_dry_run_reads_only(self):
        out, client = self.run_card([heading(), old(1)], live=False)
        self.assertEqual((out["written"], out["stale"]), (0, 0))
        self.assertEqual(set(client.log), {"GET"})

    def test_live_replaces_everything_after_the_heading_and_verifies(self):
        out, client = self.run_card([heading(), old(1), old(2)])
        self.assertEqual((out["written"], out["removed"], out["overdue_blocked"], out["today"], out["this_week"], out["done"]),
                         (1, 2, 2, 2, 3, 1))
        self.assertEqual(client.kids[0]["id"], "h")
        self.assertTrue(client.kids[1]["paragraph"]["rich_text"][0]["plain_text"].startswith("V7 · updated"))
        self.assertLess(client.log.index("APPEND"), client.log.index("DELETE"))      # add first, remove after

    def test_a_block_that_does_not_open_with_the_heading_is_never_touched(self):
        client = FakeNotion([old(1)])
        with self.assertRaises(CardError) as ctx:
            card.run(1, True, environ=ENV, client=client, now=NOW, connect=conn({"AAA": snapshot()}))
        self.assertEqual(str(ctx.exception), "JIRA_CARD_NOT_OWNED")
        self.assertEqual(client.log, ["GET"])

    def test_stale_snapshot_only_marks_the_status_line(self):
        status = {"id": "s", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "V7 · updated 6:00 AM CT"}]}}
        out, client = self.run_card([heading(), status, old(1)], snaps={"AAA": snapshot(taken=datetime(2026, 10, 7, 4, 0, tzinfo=TZ))})
        self.assertEqual((out["stale"], out["written"]), (1, 1))
        self.assertIn("STALE", client.kids[1]["paragraph"]["rich_text"][0]["text"]["content"])
        self.assertEqual(len(client.kids), 3)                          # no content rewritten, no false zero

    def test_stale_without_a_baseline_and_missing_snapshot_fail_closed(self):
        with self.assertRaises(CardError) as ctx:
            self.run_card([heading(), old(1)], snaps={"AAA": snapshot(taken=datetime(2026, 10, 7, 1, 0, tzinfo=TZ))})
        self.assertEqual(str(ctx.exception), "JIRA_CARD_STALE_NO_BASELINE")
        with self.assertRaises(CardError) as ctx:
            self.run_card([heading()], snaps={})
        self.assertEqual(str(ctx.exception), "JIRA_CARD_NO_SNAPSHOT")

    def test_config_codes(self):
        with self.assertRaises(CardError):
            card.run(1, False, environ={"JIRA_BOARDS": "AAA:1"}, client=FakeNotion([]), now=NOW)
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
