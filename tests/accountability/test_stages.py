"""LIFE-671 (synthetic): the three accountability stages fill the V3 callouts from fake Jira, say so plainly when there are no quarter epics, and go DEGRADED when Jira cannot be read."""
import unittest
from unittest import mock

from lifeos.accountability import goals, region, render, review, week
from tests.accountability import fakes as F

Q4_EPICS = [F.epic("JFM-9", "Career Q4 2026"), F.epic("JFM-102", "LIFE OS Q4 2026"), F.epic("MEJ-1", "Career Q4 2026"), F.epic("MEJ-3", "Health Q4 2026")]
KIDS = [F.child("JFM-20", "JFM-9", True, "2026-10-12T10:00:00.000-0500"), F.child("JFM-21", "JFM-9", True, "2026-08-01T10:00:00.000-0500"),
        F.child("JFM-22", "JFM-9", False), F.child("JFM-23", "JFM-102", False), F.child("JFM-24", "JFM-9", False, subtask=True),
        F.child("MEJ-20", "MEJ-1", True, "2026-11-02T10:00:00.000-0600"), F.child("MEJ-21", "MEJ-3", False)]


def page(notion, middle_kind):
    for who in ("jim", "matt"):
        block = f"b-{who}-{middle_kind}"
        if middle_kind == "goals":
            lane_rows = [("Career", "–"), ("Creative", "–"), ("Health, Finances & Travel" if who == "matt" else "Health", "–"), ("LIFE OS", "–")]
            notion.callout(block, f"Quarterly goals {who}", "Not updated yet · done / all tasks", [notion.table(f"t-{who}", lane_rows)], f"pg-{who}")
        elif middle_kind == "review":
            notion.callout(block, f"Quarterly review {who}", "Not updated yet", [notion.toggle(f"tg-{who}", "Nothing yet."), F.para(f"past-{who}", "Past quarters appear here when each quarter closes.")], f"pg-{who}")
        else:
            notion.callout(block, f"This week {who}", "Not updated yet", [F.para(f"body-{who}", "Nothing yet.")], f"pg-{who}")


def jira(extra=None, down=False):
    answers = {"issuetype = Epic": Q4_EPICS, "parent in (": KIDS, **(extra or {})}
    return F.Jira(answers, down)


class Address(unittest.TestCase):
    """The register row names a callout; it is accepted only when that callout sits on the Jim & Matt V3 page, also inside a column."""

    def client(self, parent_title="Jim & Matt V3"):
        CAL = "c" * 32
        blocks = {CAL: {"parent": {"type": "block_id", "block_id": "col"}}, "col": {"parent": {"type": "block_id", "block_id": "cols"}}, "cols": {"parent": {"type": "page_id", "page_id": "v3"}}}
        pages = {"v3": {"title": F.rich(parent_title)}}
        register = [{"id": "r", "type": "code", "code": {"rich_text": F.rich("R4L jim.week        " + CAL + "\nR4R matt.week       " + CAL + "")}}]

        class Fake:
            def call(self, method, path, body=None, *args, **kwargs):
                if method == "POST":
                    return {"results": [{"id": "reg", "title": F.rich(region.REGISTER_TITLE)}]}
                if "/children" in path:
                    return {"results": register, "has_more": False}
                if path.startswith("/pages/"):
                    return pages[path.split("/pages/")[1]]
                return blocks[path.split("/blocks/")[1]]
        return Fake()

    def test_a_callout_in_a_column_on_the_v3_page_is_found_by_its_register_row(self):
        self.assertEqual(region.address(self.client(), "jim.week"), "c" * 32)

    def test_a_page_keeps_its_title_in_its_title_property_as_notion_returns_it(self):
        page = {"id": "reg", "properties": {"Name": {"type": "title", "title": F.rich(region.REGISTER_TITLE)}}}
        self.assertEqual(region._title(page).strip(), region.REGISTER_TITLE)
        self.assertEqual(region._title({"properties": {"Other": {"type": "rich_text", "rich_text": []}}}), "")

    def test_a_row_pointing_off_the_v3_page_or_a_missing_row_is_refused(self):
        with self.assertRaises(region.AccountabilityError) as caught:
            region.address(self.client("Some other page"), "jim.week")
        self.assertEqual(str(caught.exception), "ACCOUNTABILITY_TARGET_NOT_FOUND")
        with self.assertRaises(region.AccountabilityError):
            region.address(self.client(), "jim.nothing")


class Stages(unittest.TestCase):
    def run_stage(self, module, kind, notion, fake, live=True):
        names = {"week": "week", "goals": "goals", "review": "review"}
        with mock.patch.object(region, "address", lambda _n, key: f"b-{key.split('.')[0]}-{names[kind]}"):
            return module.run(0, live, environ={}, jira=fake, notion=notion, now=F.NOW)

    def test_goals_show_done_over_all_counting_only_this_quarters_tasks(self):
        notion = F.Notion()
        page(notion, "goals")
        counts = self.run_stage(goals, "goals", notion, jira(), True)
        cells = [[p["plain_text"] for p in notion.blocks[f"t-jim-r{i}"]["table_row"]["cells"][1]][0] for i in range(1, 5)]
        self.assertEqual(cells[0], "1 / 2")                                  # JFM-20 done in quarter, JFM-21 done before it (not counted), JFM-22 open; sub-task not counted
        self.assertEqual(cells[1], "–")                                      # no Creative epic
        self.assertEqual(cells[3], "0 / 1")                                  # LIFE OS
        matt = [[p["plain_text"] for p in notion.blocks[f"t-matt-r{i}"]["table_row"]["cells"][1]][0] for i in range(1, 5)]
        self.assertEqual((matt[0], matt[2]), ("1 / 1", "0 / 1"))             # "Health, Finances & Travel" is matched by "Health"
        self.assertTrue(notion.text("pg-jim-p").startswith("Quarterly goals Q4 2026 (Jim)"))
        self.assertLessEqual(len(notion.text("pg-jim-p")), render.SUMMARY_MAX)
        self.assertEqual(counts["jim"]["epics"], 2)

    def test_a_new_quarter_with_no_epics_says_so_plainly_and_succeeds(self):
        notion = F.Notion()
        page(notion, "goals")
        counts = self.run_stage(goals, "goals", notion, F.Jira({}), True)
        self.assertEqual(counts["jim"]["epics"], 0)
        self.assertEqual(notion.text("b-jim-goals-new"), render.NO_EPICS)
        self.assertIn("Last refreshed 11/18 · 9:05 AM CT", notion.text("b-jim-goals-st"))

    def test_jira_down_keeps_the_body_and_marks_the_old_stamp_degraded(self):
        notion = F.Notion()
        page(notion, "week")
        notion.blocks["b-jim-week-st"]["paragraph"] = {"rich_text": F.rich("Last refreshed 11/18 · 8:05 AM CT")}
        with self.assertRaises(region.AccountabilityError) as caught:
            self.run_stage(week, "week", notion, F.Jira({}, down=True), True)
        self.assertEqual(str(caught.exception), "ACCOUNTABILITY_DEGRADED:jim,matt")
        self.assertEqual(notion.text("body-jim"), "Nothing yet.")
        self.assertEqual(notion.text("b-jim-week-st"), "DEGRADED · Jira could not be read at 11/18 · 9:05 AM CT · showing last accepted 11/18 · 8:05 AM CT")
        self.assertEqual(notion.text("pg-jim-p"), "old summary")             # the summary page keeps its last accepted text too

    def test_week_lists_open_work_overdue_first_and_a_dry_run_writes_nothing(self):
        notion = F.Notion()
        page(notion, "week")
        open_issues = [{"key": "JFM-30", "fields": {"summary": "Send the note", "status": {"name": "To Do"}, "duedate": "2026-11-20"}},
                       {"key": "JFM-31", "fields": {"summary": "Book the call", "status": {"name": "In Progress"}, "duedate": "2026-11-10"}}]
        fake = jira({"statusCategory != Done AND (sprint": open_issues, "statusCategory = Done": [{"key": "JFM-1"}]})
        self.run_stage(week, "week", notion, fake, False)
        self.assertEqual(notion.writes, [])
        self.run_stage(week, "week", notion, fake, True)
        body = notion.text("body-jim").split("\n")
        self.assertEqual(body[0], "2 open · 1 overdue · 0 blocked · 1 done this week")
        self.assertEqual(body[1], "JFM-31 Book the call (In Progress, overdue)")
        self.assertEqual(body[2], "JFM-30 Send the note (To Do)")

    def test_a_second_identical_run_writes_nothing(self):
        notion = F.Notion()
        page(notion, "goals")
        self.run_stage(goals, "goals", notion, jira(), True)
        notion.writes.clear()
        self.run_stage(goals, "goals", notion, jira(), True)
        self.assertEqual(notion.writes, [])

    def test_a_past_quarter_line_is_written_once_and_never_rewritten(self):
        notion = F.Notion()
        page(notion, "review")
        q3 = jira()
        q3.answers = {"labels = \"goals-2026-q3\"": [F.epic("JFM-5", "Career Q3 2026")], "labels = \"goals-2026-q4\"": Q4_EPICS, "parent in (JFM-5": [F.child("JFM-50", "JFM-5", True, "2026-09-01T10:00:00.000-0500")],
                      "parent in (": KIDS}
        self.run_stage(review, "review", notion, q3, True)
        self.assertEqual(notion.text("past-jim"), "Q3 2026 — closed: Career 1 / 1")
        self.assertTrue(notion.text("tg-jim-in").startswith("Q4 2026 (current): Career 1 / 2"))
        q3.answers["parent in (JFM-5"] = [F.child("JFM-50", "JFM-5", True, "2026-09-01T10:00:00.000-0500"), F.child("JFM-51", "JFM-5", False)]
        self.run_stage(review, "review", notion, q3, True)
        self.assertEqual(notion.text("past-jim"), "Q3 2026 — closed: Career 1 / 1")           # later runs never change it

    def test_an_unexpected_callout_shape_writes_nothing(self):
        notion = F.Notion()
        page(notion, "week")
        notion.kids["b-jim-week"].remove("body-jim")
        with self.assertRaises(region.AccountabilityError) as caught:
            self.run_stage(week, "week", notion, jira(), True)
        self.assertEqual(str(caught.exception), "ACCOUNTABILITY_SHAPE")
        self.assertEqual(notion.writes, [])

    def test_the_degraded_stamp_of_a_region_never_accepted_says_so(self):
        self.assertTrue(render.degraded_stamp("Not updated yet · V7 fills this", F.NOW).endswith("showing last accepted nothing yet"))
        again = render.degraded_stamp(render.degraded_stamp("Last refreshed 11/18 · 8:05 AM CT", F.NOW), F.NOW)
        self.assertTrue(again.endswith("showing last accepted 11/18 · 8:05 AM CT"))


if __name__ == "__main__":
    unittest.main()
