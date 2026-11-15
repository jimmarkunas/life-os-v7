"""LIFE-671 (synthetic): the three accountability stages fill the V3 callouts from fake Jira, say so plainly when there are no quarter epics, and go DEGRADED when Jira cannot be read."""
import unittest
from unittest import mock

from lifeos.accountability import artifacts, changelog, goals, read, region, render, review, week
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
        elif middle_kind == "artifacts":
            notion.callout(block, f"Artifacts {who}", "Not updated yet", [F.para(f"art-{who}", "Nothing yet. old text")], f"pg-{who}")
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
        pages = {"v3": {"properties": {"title": {"type": "title", "title": F.rich(parent_title)}}}}          # a real page keeps its title in the title property
        register = [{"id": "r", "type": "code", "code": {"rich_text": F.rich("R4L jim.week        " + CAL + "\nR4R matt.week       " + CAL + "")}}]

        class Fake:
            def call(self, method, path, body=None, *args, **kwargs):
                if method == "POST":
                    return {"results": [{"id": "reg", "object": "page", "properties": {"title": {"type": "title", "title": F.rich(region.REGISTER_TITLE)}}}]}
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


class Knowledge(unittest.TestCase):
    def go(self, notion, fake, live=True):
        with mock.patch.object(region, "address", lambda _n, key: f"b-{key.split('.')[0]}-artifacts"):
            return artifacts.run(0, live, environ={}, jira=fake, notion=notion, now=F.NOW)

    def test_an_artifact_with_a_link_is_listed_and_one_without_is_missing_a_link(self):
        notion = F.Notion()
        page(notion, "artifacts")
        done_issue = {"key": "MEJ-74", "fields": {"summary": "Build Travel Tracker", "description": None}}
        bare = {"key": "MEJ-75", "fields": {"summary": "Trip notes", "description": {"type": "doc", "content": [{"type": "text", "text": "no address here"}]}}}
        in_text = {"key": "MEJ-76", "fields": {"summary": "Packing list", "description": {"type": "doc", "content": [{"type": "text", "text": "see https://example.com/pack for it"}]}}}
        fake = F.Jira({'labels = "artifact"': [done_issue, bare, in_text]}, remote={"MEJ-74": ["https://example.com/travel"]})
        self.go(notion, fake)
        lines = notion.text("art-matt").split("\n")
        self.assertEqual(lines[:2], ["Build Travel Tracker — https://example.com/travel", "Packing list — https://example.com/pack"])
        self.assertEqual(lines[2], "Missing link: MEJ-75 Trip notes")
        self.assertLessEqual(len(notion.text("pg-matt-p")), render.SUMMARY_MAX)

    def test_the_current_sprints_tasks_and_sub_tasks_are_listed_with_sub_tasks_under_their_task(self):
        notion = F.Notion()
        page(notion, "artifacts")
        sprint = [{"key": "JFM-2", "fields": {"summary": "Sub one", "status": {"name": "To Do"}, "parent": {"key": "JFM-1"}, "issuetype": {"subtask": True}}},
                  {"key": "JFM-1", "fields": {"summary": "The task", "status": {"name": "In Progress"}, "issuetype": {"subtask": False}}},
                  {"key": "JFM-9", "fields": {"summary": "Orphan sub", "status": {"name": "Done"}, "parent": {"key": "JFM-8"}, "issuetype": {"subtask": True}}}]
        self.go(notion, F.Jira({"sprint in openSprints()": sprint}))
        lines = notion.text("art-jim").split("\n")
        i = lines.index("To-do lists, this week's sprint (3):")
        self.assertEqual(lines[i + 1:], ["JFM-1 The task (In Progress)", "  - JFM-2 Sub one (To Do)", "  - JFM-9 Orphan sub (Done)"])

    def test_no_artifacts_says_so_plainly(self):
        notion = F.Notion()
        page(notion, "artifacts")
        self.go(notion, F.Jira({}))
        self.assertEqual(notion.text("art-jim").split("\n")[0], render.NO_ARTIFACTS)
        self.assertIn("nothing in a sprint yet", notion.text("art-jim"))


class Changelog(unittest.TestCase):
    def build(self, lines):
        notion = F.Notion()
        bullets = [{"id": f"bl{i}", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": F.rich(t)}} for i, t in enumerate(lines)]
        notion.callout("b-shared-log", "Canon / Change Log", "New lines are added; old ones never change", bullets, "pg-log")
        return notion

    def go(self, notion, now=F.NOW, live=True):
        with mock.patch.object(region, "address", lambda _n, key: "b-shared-log"):
            return changelog.run(0, live, environ={}, notion=notion, now=now)

    def test_a_new_quarter_adds_one_dated_line_and_never_touches_earlier_ones(self):
        old = ["Nov 14 2026 — V3 page built from the approved mockup", "V1 link: to be added"]
        notion = self.build(old)
        counts = self.go(notion)
        self.assertEqual(counts["shared"]["appended"], 1)
        kids = [notion.text(k) for k in notion.kids["b-shared-log"] if notion.blocks[k]["type"] == "bulleted_list_item"]
        self.assertEqual(kids[:2], old)
        self.assertEqual(kids[2], "Oct 7 2026 — Q4 2026 began (first Wednesday of the quarter): Jim and Matt create the quarter's epics, labelled goals-2026-q4")
        self.assertEqual(notion.writes.count("b-shared-log"), 1)

    def test_a_second_run_in_the_same_quarter_appends_nothing(self):
        notion = self.build(["x"])
        self.go(notion)
        notion.writes.clear()
        self.go(notion)
        self.assertEqual([w for w in notion.writes if w == "b-shared-log"], [])

    def test_a_dry_run_writes_nothing_and_a_foreign_block_in_the_callout_stops_the_run(self):
        notion = self.build(["x"])
        self.go(notion, live=False)
        self.assertEqual(notion.writes, [])
        notion.kids["b-shared-log"].insert(3, "bl0")
        notion.blocks["bl0"]["type"] = "paragraph"
        notion.blocks["bl0"]["paragraph"] = notion.blocks["bl0"].pop("bulleted_list_item")
        with self.assertRaises(region.AccountabilityError):
            self.go(notion)


class QuarterSchedule(unittest.TestCase):
    """A quarter begins on the first Wednesday of January, April, July and October (Jim, Nov 15 2026)."""

    def test_the_first_wednesdays(self):
        from datetime import date
        self.assertEqual([read.first_wednesday(2026, m) for m in (1, 4, 7, 10)], [date(2026, 1, 7), date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 7)])
        self.assertEqual(read.first_wednesday(2027, 1), date(2027, 1, 6))

    def test_the_day_before_a_first_wednesday_still_belongs_to_the_old_quarter(self):
        from datetime import date
        self.assertEqual(read.quarter(date(2026, 10, 6))[:2], (2026, 3))
        self.assertEqual(read.quarter(date(2026, 10, 7))[:2], (2026, 4))
        self.assertEqual(read.quarter(date(2027, 1, 5))[:2], (2026, 4))
        self.assertEqual(read.quarter(date(2027, 1, 6))[:2], (2027, 1))
        self.assertEqual(read.quarter(date(2026, 11, 15)), (2026, 4, date(2026, 10, 7), date(2027, 1, 5)))
        self.assertEqual(read.quarter_bounds(2026, 3), (date(2026, 7, 1), date(2026, 10, 6)))


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
