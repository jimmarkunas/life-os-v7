"""LIFE-671 (synthetic): the three accountability stages fill the V3 callouts from fake Jira, say so plainly when there are no quarter epics, and go DEGRADED when Jira cannot be read."""
import unittest
from unittest import mock

from lifeos.accountability import artifacts, changelog, goals, index, read, region, render, review, week
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


SITE = "https://jira.example.com"


SHEET = "https://docs.google.com/spreadsheets/d/{}/edit"
ID_A, ID_B, ID_C = "a" * 25, "b" * 25, "c" * 25


def indexed(rows):
    """What the artifact index returns for [(owner, title, file id, keys, kind, state)]: ([cells], link)."""
    return [([o, title, fid, keys, kind, state], SHEET.format(fid)) for o, title, fid, keys, kind, state in rows]


class Knowledge(unittest.TestCase):
    def go(self, notion, fake, rows, live=True):
        with mock.patch.object(region, "address", lambda _n, key: f"b-{key.split('.')[0]}-artifacts"), mock.patch.object(index, "rows", lambda _n, key: rows):
            return artifacts.run(0, live, environ={"JIRA_SITE_URL": SITE}, jira=fake, notion=notion, now=F.NOW)

    def test_each_person_shows_only_their_verified_artifacts_once_and_no_task_lists(self):
        notion = F.Notion()
        page(notion, "artifacts")
        rows = indexed([("Matt", "Travel Tracker", ID_A, "MEJ-74,MEJ-99", "Sheet", "verified"), ("Matt", "Band Sheet", ID_B, "MEJ-33", "Sheet", "verified"),
                        ("Jim", "Plan Doc", ID_C, "JFM-5", "Doc", "verified"), ("Jim", "Lost Doc", "d" * 25, "JFM-6", "Doc", "unresolved")])
        counts = self.go(notion, F.Jira({}), rows)
        self.assertEqual(notion.text("art-matt").split("\n"), ["Travel Tracker", "Band Sheet"])
        self.assertEqual({k: v for k, v in notion.links("art-matt") if v}, {"Travel Tracker": SHEET.format(ID_A), "Band Sheet": SHEET.format(ID_B)})
        self.assertEqual(notion.text("art-jim"), "Plan Doc")
        self.assertNotIn("To-do", notion.text("art-jim") + notion.text("art-matt"))
        self.assertEqual((counts["matt"]["artifacts"], counts["jim"]["artifacts"], counts["jim"]["unresolved"]), (2, 1, 1))
        self.assertLessEqual(len(notion.text("pg-matt-p")), render.SUMMARY_MAX)

    def test_a_body_a_person_turned_into_one_bullet_is_written_as_a_bullet(self):
        notion = F.Notion()
        page(notion, "artifacts")
        notion.blocks["art-matt"] = {"id": "art-matt", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": F.rich("old text")}}
        rows = indexed([("Matt", "Travel Tracker", ID_A, "MEJ-74", "Sheet", "verified")])
        self.go(notion, F.Jira({}), rows)
        self.assertEqual(notion.text("art-matt"), "Travel Tracker")
        self.assertEqual(notion.blocks["art-matt"]["type"], "bulleted_list_item")

    def test_a_second_identical_run_writes_nothing(self):
        notion = F.Notion()
        page(notion, "artifacts")
        rows = indexed([("Matt", "Travel Tracker", ID_A, "MEJ-74", "Sheet", "verified")])
        self.go(notion, F.Jira({}), rows)
        notion.writes.clear()
        self.go(notion, F.Jira({}), rows)
        self.assertEqual(notion.writes, [])

    def test_jims_jira_candidates_are_found_deduplicated_by_file_id_and_counted_against_the_index(self):
        notion = F.Notion()
        page(notion, "artifacts")
        doc = lambda fid: {"type": "doc", "content": [{"type": "text", "text": "see", "marks": [{"type": "link", "attrs": {"href": f"https://docs.google.com/document/d/{fid}/edit"}}]}]}
        issues = [{"key": "JFM-5", "fields": {"description": doc(ID_C), "comment": {"comments": []}}},
                  {"key": "JFM-6", "fields": {"description": None, "comment": {"comments": [{"body": doc(ID_C)}, {"body": doc("e" * 25)}]}}},
                  {"key": "JFM-7", "fields": {"description": {"type": "doc", "content": [{"type": "text", "text": "make a spreadsheet, no link"}]}, "comment": {"comments": []}}}]
        found = read.google_files(F.Jira({"project = JFM": issues}), "JFM")
        self.assertEqual(found, {ID_C: {"kind": "Doc", "keys": ["JFM-5", "JFM-6"]}, "e" * 25: {"kind": "Doc", "keys": ["JFM-6"]}})
        rows = indexed([("Jim", "Plan Doc", ID_C, "JFM-5,JFM-6", "Doc", "verified")])
        counts = self.go(notion, F.Jira({"project = JFM": issues}), rows)
        self.assertEqual(counts["jim"]["unindexed_candidates"], 1)           # the second doc is a candidate nobody verified yet; it is counted, never shown

    def test_a_missing_index_or_an_empty_one_says_so_plainly(self):
        notion = F.Notion()
        page(notion, "artifacts")
        self.go(notion, F.Jira({}), None)
        self.assertEqual(notion.text("art-jim"), render.NO_INDEX)
        self.go(notion, F.Jira({}), [])
        self.assertEqual(notion.text("art-jim"), render.NO_ARTIFACTS)


class IndexPages(unittest.TestCase):
    """The index pages are found by their register row and accepted only with the expected title; a person's rows are read in order."""

    def make(self, title):
        notion = F.Notion()
        notion.index_page("index.reviews", title, [("Jim | Q3 2026 | past → Quarterly review — Jim — Q3 2026", "https://www.notion.so/" + "1" * 32), ("Jim | Q4 2026 | current → Quarterly review — Jim — Q4 2026", "https://www.notion.so/" + "2" * 32), ("Matt | Q4 2026 | current → Quarterly review — Matt — Q4 2026", "https://www.notion.so/" + "3" * 32)])
        return notion

    def test_the_review_links_put_the_current_quarter_first_and_only_the_persons_own(self):
        notion = self.make(index.TITLES["index.reviews"])
        with mock.patch.object(region, "register_row", lambda n, key: getattr(n, "indexes", {}).get(key)):
            self.assertEqual(index.reviews(notion, "Jim"), [("Q4 2026 (current)", "https://www.notion.so/" + "2" * 32), ("Q3 2026", "https://www.notion.so/" + "1" * 32)])
            self.assertEqual([label for label, _ in index.reviews(notion, "Matt")], ["Q4 2026 (current)"])

    def test_a_page_with_another_title_or_no_register_row_is_not_an_index(self):
        with mock.patch.object(region, "register_row", lambda n, key: getattr(n, "indexes", {}).get(key)):
            self.assertIsNone(index.reviews(self.make("Some other page"), "Jim"))
            self.assertIsNone(index.reviews(F.Notion(), "Jim"))


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
            return module.run(0, live, environ={"JIRA_SITE_URL": SITE}, jira=fake, notion=notion, now=F.NOW)

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

    def test_week_links_every_ticket_and_keeps_open_work_apart_from_done(self):
        notion = F.Notion()
        page(notion, "week")
        open_issues = [{"key": "JFM-30", "fields": {"summary": "Send the note", "status": {"name": "To Do"}, "duedate": "2026-11-20"}},
                       {"key": "JFM-31", "fields": {"summary": "Book the call", "status": {"name": "In Progress"}, "duedate": "2026-11-10"}}]
        finished = [{"key": "JFM-1", "fields": {"summary": "Shipped it", "status": {"name": "Done", "statusCategory": {"key": "done"}}}}]
        fake = jira({"statusCategory != Done AND (sprint": open_issues, "statusCategory = Done": finished})
        self.run_stage(week, "week", notion, fake, False)
        self.assertEqual(notion.writes, [])
        self.run_stage(week, "week", notion, fake, True)
        body = notion.text("body-jim").split("\n")
        self.assertEqual(body, ["2 open · 1 overdue · 0 blocked · 1 done this week", "In progress / To do (2)", "JFM-31 Book the call (In Progress, overdue)",
                                "JFM-30 Send the note (To Do)", "", "Done this week (1)", "JFM-1 Shipped it (Done)"])
        links = dict(notion.links("body-jim"))
        self.assertEqual([links[k] for k in ("JFM-31", "JFM-30", "JFM-1")], [f"{SITE}/browse/{k}" for k in ("JFM-31", "JFM-30", "JFM-1")])

    def test_a_more_in_jira_link_survives_notion_rewriting_its_escapes(self):
        notion = F.Notion()
        page(notion, "week")
        many = [{"key": f"JFM-{n}", "fields": {"summary": f"Task {n}", "status": {"name": "To Do"}, "duedate": None}} for n in range(40, 56)]
        fake = jira({"statusCategory != Done AND (sprint": many})
        self.run_stage(week, "week", notion, fake, True)                      # a first run that cannot read its own write back raises ACCOUNTABILITY_VERIFY_FAILED
        links = [url for text, url in notion.links("body-jim") if "more in Jira" in text]
        self.assertEqual(len(links), 1)
        self.assertIn("(", links[0])                                         # the stored address has the characters Notion unescapes
        notion.writes.clear()
        self.run_stage(week, "week", notion, fake, True)
        self.assertEqual(notion.writes, [])                                   # and a second identical run still writes nothing

    def test_without_a_site_address_the_lines_are_plain_text_not_broken_links(self):
        notion = F.Notion()
        page(notion, "week")
        open_issues = [{"key": "JFM-30", "fields": {"summary": "Send the note", "status": {"name": "To Do"}, "duedate": None}}]
        with mock.patch.object(region, "address", lambda _n, key: f"b-{key.split('.')[0]}-week"):
            week.run(0, True, environ={}, jira=jira({"statusCategory != Done AND (sprint": open_issues}), notion=notion, now=F.NOW)
        self.assertEqual({url for _, url in notion.links("body-jim")}, {None})

    def combined(self, notion, fake, reviews=None, live=True):
        names = {"goals": "goals"}
        with mock.patch.object(region, "address", lambda _n, key: f"b-{key.split('.')[0]}-goals"), mock.patch.object(index, "reviews", lambda _n, person: reviews):
            return goals.run(0, live, environ={"JIRA_SITE_URL": SITE}, jira=fake, notion=notion, now=F.NOW)

    def test_the_combined_callout_has_title_epic_rows_with_links_a_note_and_the_review_toggle_and_loses_the_summary_line(self):
        notion = F.Notion()
        page(notion, "goals")
        links = [("Q4 2026 (current)", "https://www.notion.so/" + "2" * 32), ("Q3 2026", "https://www.notion.so/" + "1" * 32)]
        counts = self.combined(notion, jira(), links)
        self.assertEqual(notion.text("b-jim-goals-h"), "Quarterly Goals & Review — Jim · Q4 2026")
        self.assertEqual(notion.text("b-matt-goals-h"), "Quarterly Goals & Review — Matt · Q4 2026")
        self.assertIn("Last refreshed 11/18 · 9:05 AM CT", notion.text("b-jim-goals-st"))
        row = lambda who, i: [[(p["plain_text"], p["href"]) for p in cell] for cell in notion.blocks[f"t-{who}-r{i}"]["table_row"]["cells"]]
        self.assertEqual(row("jim", 1), [[("Career", f"{SITE}/browse/JFM-9")], [("1 / 2", None)]])          # JFM-20 done in quarter, JFM-21 done before it (not counted), JFM-22 open; sub-task not counted
        self.assertEqual(row("jim", 2), [[("Creative", None)], [("–", None)]])                                  # no Creative epic: no link, a dash
        self.assertEqual(row("jim", 4), [[("LIFE OS", f"{SITE}/browse/JFM-102")], [("0 / 1", None)]])
        self.assertEqual(row("matt", 3), [[("Health, Finances & Travel", f"{SITE}/browse/MEJ-3")], [("0 / 1", None)]])   # matched by "Health"
        kids = notion.kids["b-jim-goals"]
        self.assertEqual([notion.blocks[k]["type"] for k in kids], ["heading_3", "paragraph", "table", "paragraph", "toggle"])
        self.assertIn("open tasks are not under a quarter epic", notion.text(kids[3]))
        self.assertEqual(notion.text(kids[4]), "Quarterly Reviews")
        inner = notion.kids[kids[4]]
        self.assertEqual(notion.links(inner[0]), [("Q4 2026 (current)", links[0][1]), ("\n", None), ("Q3 2026", links[1][1])])
        self.assertNotIn("summary for claude", " ".join(notion.text(k).lower() for k in kids if notion.blocks[k]["type"] != "table"))
        self.assertEqual(notion.deleted, ["s-pg-jim", "s-pg-matt"])
        self.assertEqual((counts["jim"]["epics"], counts["jim"]["review_pages"]), (2, 2))

    def test_a_second_identical_run_writes_nothing(self):
        notion = F.Notion()
        page(notion, "goals")
        links = [("Q4 2026 (current)", "https://www.notion.so/" + "2" * 32)]
        self.combined(notion, jira(), links)
        notion.writes.clear()
        self.combined(notion, jira(), links)
        self.assertEqual(notion.writes, [])
        self.assertEqual([k for k in notion.kids["b-jim-goals"] if notion.blocks[k]["type"] == "toggle"].__len__(), 1)      # no second toggle

    def test_a_dry_run_changes_nothing(self):
        notion = F.Notion()
        page(notion, "goals")
        self.combined(notion, jira(), None, live=False)
        self.assertEqual(notion.writes, [])

    def test_a_new_quarter_with_no_epics_and_no_review_index_says_both_plainly_and_succeeds(self):
        notion = F.Notion()
        page(notion, "goals")
        counts = self.combined(notion, F.Jira({}), None)
        self.assertEqual(counts["jim"]["epics"], 0)
        kids = notion.kids["b-jim-goals"]
        self.assertEqual(notion.text(kids[3]), render.NO_EPICS)
        self.assertEqual(notion.text(notion.kids[kids[4]][0]), render.REVIEWS_NONE)

    def test_the_retired_review_stage_writes_nothing(self):
        notion = F.Notion()
        page(notion, "review")
        self.assertEqual(review.run(0, True, environ={}, jira=jira(), notion=notion, now=F.NOW), {"retired": True, "writes": 0})
        self.assertEqual(notion.writes, [])

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
