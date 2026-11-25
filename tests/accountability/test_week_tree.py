"""LIFE-918 AC3 (synthetic): the V3 weekly sprint view as native blocks, built to the approved wireframe (Jim left, Matt right, 2 x 2 tiles). No live Jira or Notion."""
import unittest
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from lifeos.accountability import read, region, render, week, weekview
from tests.accountability import fakes as F
from tests.accountability import test_v3_week_view as W
from tests.accountability.treefake import TreeNotion

NOW = datetime(2026, 11, 25, 8, 0, tzinfo=ZoneInfo("America/Chicago"))
TODAY = date(2026, 11, 25)
SITE = "https://example.invalid"


class Wireframe(unittest.TestCase):
    def test_both_boards_render_the_same_structure_as_the_wireframe(self):
        for project in ("JFM", "MEJ"):
            specs = weekview.spec(weekview._fixture(project), TODAY, SITE, project, NOW)
            kinds = [s["type"] for s in specs]
            self.assertEqual(kinds, ["paragraph", "column_list", "paragraph", "bulleted_list_item", "bulleted_list_item", "bulleted_list_item", "paragraph", "bulleted_list_item", "bulleted_list_item",
                                     "paragraph", "bulleted_list_item", "toggle", "paragraph"])
            self.assertEqual(specs[0]["rich"][0][0], f"{project} W48 2026 · Mon Nov 23 – Sun Nov 29 (11/25 · 8:00 AM CT)")
            columns = specs[1]["children"]
            self.assertEqual([c["type"] for c in columns], ["column", "column"])
            labels = [[t["children"][1]["rich"][0][0] for t in c["children"]] for c in columns]
            self.assertEqual(labels, [["Sprint commitments", "Overdue backlog"], ["Blocked outside sprint", "Done this week"]])          # a 2 x 2 grid
            numbers = [[(t["children"][0]["rich"][0][0], t["children"][0]["rich"][0][3]) for t in c["children"]] for c in columns]
            self.assertEqual(numbers, [[("3", None), ("1", "red")], [("2", "red"), ("1", None)]])                                        # Blocked and Overdue red above 0
            self.assertEqual(columns[1]["children"][1]["children"][1]["rich"][1][0], "\n1 auto-closed · 0 by hand")
            self.assertEqual(specs[3]["children"][0]["rich"][0][0], f"{project}-11")                                                       # Sub-tasks nest under their parent
            self.assertEqual(specs[-1]["rich"][0][0], f"Next sprints: {project} W49 2026 · {project} W50 2026 · {project} W51 2026 · {project} W52 2026")
            self.assertEqual(specs[-2]["rich"][0][0], "Done this week — 1")

    def test_zero_counts_are_not_red_and_empty_sections_say_nothing(self):
        empty = {"v3": True, "open": [], "subtasks": {}, "blocked": [], "overdue": [], "done": [], "jql": "x", "outside_jql": "y"}
        specs = weekview.spec(empty, TODAY, SITE, "MEJ", NOW)
        tiles = [t for c in specs[1]["children"] for t in c["children"]]
        self.assertTrue(all(t["children"][0]["rich"][0][3] is None for t in tiles))
        self.assertEqual(sum(1 for line in weekview.plain(specs) if line.strip() == "Nothing."), 4)

    def test_a_long_list_shows_ten_and_links_the_rest(self):
        rows = [{"key": f"MEJ-{n}", "summary": "s", "status": "To Do", "due": None, "done": False, "labels": []} for n in range(1, 16)]
        week_ = {"v3": True, "open": rows, "subtasks": {}, "blocked": [], "overdue": [], "done": [], "jql": "project = MEJ", "outside_jql": "project = MEJ"}
        text = weekview.plain(weekview.spec(week_, TODAY, SITE, "MEJ", NOW))
        self.assertIn("…and 5 more in Jira", text)

    def test_the_pure_renderer_is_the_same_for_both_date_variants(self):
        a = weekview.spec(weekview._fixture("JFM"), date(2026, 12, 31), SITE, "JFM", datetime(2026, 12, 31, 8, tzinfo=ZoneInfo("America/Chicago")))
        self.assertEqual(a[0]["rich"][0][0], "JFM W53 2026 · Mon Dec 28 – Sun Jan 3 (12/31 · 8:00 AM CT)")                                   # ISO week 53 and the year end
        self.assertEqual(a[-1]["rich"][0][0], "Next sprints: JFM W1 2027 · JFM W2 2027 · JFM W3 2027 · JFM W4 2027")


def page(notion):
    for who in ("jim", "matt"):
        notion.callout(f"b-{who}-week", f"This week {who}", "Not updated yet", [F.para(f"body-{who}", "Nothing yet.")], f"pg-{who}")


class Writer(unittest.TestCase):
    def run_stage(self, notion, live, environ=None):
        env = {"JIRA_SITE_URL": SITE, "V3_WEEK_VIEW": "on", **(environ or {})}
        with mock.patch.object(region, "address", lambda _n, key: f"b-{key.split('.')[0]}-week"):
            return week.run(0, live, environ=env, jira=BOARDS, notion=notion, now=NOW)

    def test_a_live_run_replaces_the_legacy_paragraph_with_the_tree_and_reads_it_back(self):
        notion = TreeNotion()
        page(notion)
        self.run_stage(notion, True)
        for who, project in (("jim", "JFM"), ("matt", "MEJ")):
            order = notion.kids[f"b-{who}-week"]
            self.assertEqual(notion.blocks[order[0]]["type"], "heading_3")
            self.assertEqual(order[1], f"b-{who}-week-st")
            self.assertEqual(order[-1], f"s-pg-{who}")                                                  # the Summary for Claude line stays last
            self.assertNotIn(f"body-{who}", order)
            tree = [notion.blocks[k]["type"] for k in order[2:-1]]
            self.assertEqual(tree[:2], ["paragraph", "column_list"])
            self.assertEqual(notion.text(order[2]), f"{project} W48 2026 · Mon Nov 23 – Sun Nov 29 (11/25 · 8:00 AM CT)")
            self.assertEqual(notion.text(f"b-{who}-week-st"), "Last refreshed 11/25 · 8:00 AM CT")
            self.assertTrue(notion.text(f"pg-{who}-p").startswith(f"This week ({'Jim' if who == 'jim' else 'Matt'}) · Last refreshed"))

    def test_a_second_run_changes_nothing(self):
        notion = TreeNotion()
        page(notion)
        self.run_stage(notion, True)
        notion.writes.clear()
        self.run_stage(notion, True)
        self.assertEqual(notion.writes, [])

    def test_a_dry_run_writes_nothing(self):
        notion = TreeNotion()
        page(notion)
        counts = self.run_stage(notion, False)
        self.assertEqual(notion.writes, [])
        self.assertEqual(counts["jim"]["changed"], 2)

    def test_the_switch_off_is_the_legacy_stage(self):
        notion = TreeNotion()
        page(notion)
        with mock.patch.object(region, "address", lambda _n, key: f"b-{key.split('.')[0]}-week"):
            week.run(0, True, environ={"JIRA_SITE_URL": SITE}, jira=F.Jira({}), notion=notion, now=NOW)
        self.assertEqual(notion.kids["b-jim-week"][2], "body-jim")
        self.assertTrue(notion.text("body-jim").startswith("0 open · 0 overdue · 0 blocked · 0 done this week\nIn progress / To do (0)"))

    def test_an_unknown_block_in_the_body_is_refused_and_nothing_is_written(self):
        notion = TreeNotion()
        page(notion)
        notion.blocks["tbl"] = {"id": "tbl", "type": "table", "table": {"table_width": 2}}
        notion.kids["b-jim-week"].insert(3, "tbl")
        with self.assertRaises(region.AccountabilityError) as caught:
            self.run_stage(notion, True)
        self.assertEqual(str(caught.exception), "ACCOUNTABILITY_SHAPE")
        self.assertEqual([w for w in notion.writes if w.startswith("b-jim")], [])

    def test_a_write_that_cannot_be_read_back_fails_verify(self):
        notion = TreeNotion()
        page(notion)
        real = TreeNotion._make
        with mock.patch.object(TreeNotion, "_make", lambda self, parent, child: real(self, parent, {**child, child["type"]: {**child[child["type"]], "rich_text": []}}) if child["type"] == "heading_3" else real(self, parent, child)):
            with mock.patch("lifeos.platform.readback.time.sleep"):
                with self.assertRaises(region.AccountabilityError) as caught:
                    self.run_stage(notion, True)
        self.assertEqual(str(caught.exception), "ACCOUNTABILITY_VERIFY_FAILED")


class Jira(W.Board):
    pass


class Both:
    """One fake Jira answering for either board."""

    def get(self, path, params):
        project = "JFM" if "project = JFM" in params["jql"] else "MEJ"
        return W.fixture(project).get(path, params)


BOARDS = Both()


if __name__ == "__main__":
    unittest.main()
