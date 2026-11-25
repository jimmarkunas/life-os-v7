"""LIFE-918 AC3: JFM and MEJ share one weekly view (grouped Sub-tasks, blocked and overdue backlog apart), OFF BY DEFAULT.

All fixtures are synthetic: no live Jira or Notion, nothing is written. The default path is pinned to main's queries and text elsewhere
(test_mej_week_focus.OffByDefault); here the new argument is proven and the default is re-pinned for the new code.
"""
import inspect
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.accountability import read, render
from lifeos.accountability import week as week_caller
from lifeos.jira.snapshot import project_snapshot
from lifeos.platform.jira import JiraError

TODAY = date(2026, 11, 18)          # Wednesday; the week is Mon Nov 16 to Sun Nov 22
SITE = "https://example.invalid"


def issue(key, status="To Do", due=None, kind="Task", parent=None):
    fields = {"summary": f"Synthetic {key}", "status": {"name": status, "statusCategory": {"key": "done" if status == "Done" else "new"}},
              "issuetype": {"name": kind, "subtask": kind == "Sub-task"}, "duedate": due}
    if parent:
        fields["parent"] = {"key": parent}
    return {"key": key, "fields": fields}


class Board:
    """A synthetic project: answers each of the view's queries from fixed lists."""

    def __init__(self, project, sprint, outside, done=()):
        self.project, self.sprint, self.outside, self.done, self.queries = project, list(sprint), list(outside), list(done), []

    def get(self, path, params):
        jql = params["jql"]
        self.queries.append(jql)
        if "statusCategory = Done" in jql:
            rows = self.done
        elif "sprint not in openSprints()" in jql:
            rows = self.outside
        elif "sprint in openSprints()" in jql:
            rows = self.sprint
        else:
            raise AssertionError(f"Unexpected Jira query: {jql}")
        return {"issues": rows, "isLast": True}


def fixture(project):
    p = project
    sprint = [issue(f"{p}-1", "In Progress", "2026-11-20"), issue(f"{p}-2"), issue(f"{p}-3", "blocked"),
              issue(f"{p}-11", "To Do", None, "Sub-task", f"{p}-1"), issue(f"{p}-12", "In Progress", None, "Sub-task", f"{p}-1"),
              issue(f"{p}-13", "To Do", None, "Sub-task", f"{p}-99")]               # -99 is not an open sprint commitment
    outside = [issue(f"{p}-20", "To Do", "2026-11-10"), issue(f"{p}-21", "blocked", "2026-11-05"), issue(f"{p}-22", "blocked", None),
               issue(f"{p}-1", "In Progress", "2026-11-20")]                         # the same key as a commitment: must never count twice
    return Board(p, sprint, outside, [issue(f"{p}-30", "Done")])


class WeekView(unittest.TestCase):
    def keys_of(self, rows):
        return [r["key"] for r in rows]

    def test_both_boards_get_the_same_sections_with_subtasks_under_their_parent(self):
        for project in ("JFM", "MEJ"):
            got = read.week(fixture(project), project, TODAY, v3_week_view=True)
            self.assertTrue(got["v3"])
            self.assertEqual(self.keys_of(got["open"]), [f"{project}-1", f"{project}-2", f"{project}-3"])
            self.assertEqual({k: self.keys_of(v) for k, v in got["subtasks"].items()}, {f"{project}-1": [f"{project}-11", f"{project}-12"]})
            self.assertEqual(got["subtasks_elsewhere"], 1)
            self.assertEqual(self.keys_of(got["blocked"]), [f"{project}-21", f"{project}-22"])
            self.assertEqual(self.keys_of(got["overdue"]), [f"{project}-20"])
            self.assertEqual(self.keys_of(got["done"]), [f"{project}-30"])

    def test_no_key_is_counted_twice(self):
        got = read.week(fixture("MEJ"), "MEJ", TODAY, v3_week_view=True)
        commitments = self.keys_of(got["open"])
        subs = [r["key"] for rows in got["subtasks"].values() for r in rows]
        sections = commitments + self.keys_of(got["blocked"]) + self.keys_of(got["overdue"]) + subs
        self.assertEqual(len(sections), len(set(sections)))
        self.assertNotIn("MEJ-1", self.keys_of(got["blocked"]) + self.keys_of(got["overdue"]))      # outside list echoed a commitment: dropped
        for key in subs:
            self.assertNotIn(key, commitments)                                                       # a Sub-task is never a commitment of its own

    def test_an_overdue_item_outside_the_sprint_never_also_counts_as_a_commitment(self):
        got = read.week(fixture("JFM"), "JFM", TODAY, v3_week_view=True)
        self.assertIn("JFM-20", self.keys_of(got["overdue"]))
        self.assertNotIn("JFM-20", self.keys_of(got["open"]))
        _, lines = render.week_view(got, TODAY, SITE)
        self.assertEqual(sum(1 for line in lines if line.startswith("JFM-20 ")), 1)

    def test_rendered_view_groups_subtasks_and_keeps_separate_sections(self):
        got = read.week(fixture("MEJ"), "MEJ", TODAY, v3_week_view=True)
        parts, lines = render.week_view(got, TODAY, SITE)
        self.assertEqual(lines[0], "3 sprint commitments · 2 sub-tasks under them · 2 blocked outside sprint · 1 overdue backlog · 1 done this week")
        at = lines.index("Sprint commitments (3):")
        self.assertTrue(lines[at + 1].startswith("MEJ-1 "))
        self.assertTrue(lines[at + 2].startswith("    · MEJ-11 "))
        self.assertTrue(lines[at + 3].startswith("    · MEJ-12 "))
        for heading in ("Blocked outside sprint (2):", "Overdue backlog (1):", "Done this week (1):"):
            self.assertIn(heading, lines)
        self.assertNotIn("MEJ-13", " ".join(lines))                                                  # its parent is not a commitment: not shown under another
        self.assertEqual(len([p for p in parts if p[1] == f"{SITE}/browse/MEJ-11"]), 1)

    def test_empty_boards_say_nothing_and_stay_inside_the_summary_limit(self):
        empty = read.week(Board("JFM", [], []), "JFM", TODAY, v3_week_view=True)
        _, lines = render.week_view(empty, TODAY, SITE)
        self.assertEqual(lines[0], "0 sprint commitments · 0 sub-tasks under them · 0 blocked outside sprint · 0 overdue backlog · 0 done this week")
        self.assertEqual(lines.count("Nothing."), 4)
        big = Board("MEJ", [issue(f"MEJ-{n}", "To Do", "2026-11-01") for n in range(1, 60)], [])
        _, many = render.week_view(read.week(big, "MEJ", TODAY, v3_week_view=True), TODAY, SITE)
        self.assertLessEqual(len(render.summary("This week (Matt)", render.stamp(datetime(2026, 11, 18, tzinfo=ZoneInfo("America/Chicago"))), many)), render.SUMMARY_MAX)
        self.assertIn(f"…and {59 - render.OPEN_SHOWN} more in Jira", many)

    def test_an_invalid_or_failed_read_fails_closed_for_both_boards(self):
        for project in ("JFM", "MEJ"):
            for needle in ("sprint in openSprints()", "sprint not in openSprints()", "statusCategory = Done"):
                class Failing(Board):
                    fail_on = needle

                    def get(self, path, params):
                        if self.fail_on in params["jql"]:
                            self.queries.append(params["jql"])
                            raise JiraError("ACCOUNTABILITY_JIRA_HTTP_400")
                        return super().get(path, params)
                board = Failing(project, [], [])
                with self.assertRaises(JiraError):
                    read.week(board, project, TODAY, v3_week_view=True)
                self.assertFalse(any('statusCategory = "In Progress"' in q for q in board.queries))     # never falls back to arbitrary In Progress issues

    def test_the_same_queries_run_on_both_boards_except_the_project(self):
        a, b = fixture("JFM"), fixture("MEJ")
        read.week(a, "JFM", TODAY, v3_week_view=True)
        read.week(b, "MEJ", TODAY, v3_week_view=True)
        self.assertEqual([q.replace("JFM", "P") for q in a.queries], [q.replace("MEJ", "P") for q in b.queries])
        self.assertTrue(all("project =" in q for q in a.queries))

    def test_the_view_never_writes(self):
        board = fixture("MEJ")
        read.week(board, "MEJ", TODAY, v3_week_view=True)
        self.assertFalse(hasattr(board, "post") or hasattr(board, "put"))


MAIN_WEEK_QUERIES = [   # main's queries for any project (see test_mej_week_focus.MAIN_WEEK_QUERIES)
    'project = {p} AND issuetype != Epic AND statusCategory != Done AND (sprint in openSprints() OR duedate <= "2026-11-22") ORDER BY duedate ASC, key ASC',
    'project = {p} AND issuetype != Epic AND statusCategory = Done AND statusCategoryChangedDate >= "2026-11-16" ORDER BY key ASC',
]
MAIN_SNAPSHOT_QUERIES = [
    "sprint = 178 AND issuetype != Epic AND statusCategory != Done ORDER BY Rank ASC",
    'project = {p} AND issuetype != Epic AND statusCategory != Done AND duedate < "2026-11-18" ORDER BY Rank ASC',
    "project = {p} AND issuetype != Epic AND status = blocked ORDER BY Rank ASC",
    "project = {p} AND sprint is EMPTY AND issuetype != Epic AND statusCategory != Done ORDER BY Rank ASC",
    "issuetype != Epic AND sprint = 178 AND statusCategory = Done ORDER BY Rank ASC",
]


class SnapshotBoard:
    def __init__(self, project):
        self.project, self.queries = project, []

    def board(self, board_id):
        return {"type": "scrum", "name": "Synthetic Board"}

    def sprints(self, board_id, state):
        return [{"id": 178, "name": f"{self.project} Sprint 12", "state": "active", "startDate": "2026-11-16T00:00:00-06:00", "endDate": "2026-11-22T23:30:00-06:00"}] if state == "active" else []

    def board_issues(self, board_id, jql, fields):
        self.queries.append(jql)
        return []


class OffByDefault(unittest.TestCase):
    def test_default_week_is_mains_for_both_boards_with_the_new_argument_unset_or_false(self):
        for project in ("JFM", "MEJ"):
            for kwargs in ({}, {"v3_week_view": False}):
                class Main:
                    queries = []
                    rows = [issue(f"{project}-1")]

                    def get(self, path, params):
                        Main.queries.append(params["jql"])
                        return {"issues": [] if "statusCategory = Done" in params["jql"] else self.rows, "isLast": True}
                Main.queries = []
                got = read.week(Main(), project, TODAY, **kwargs)
                self.assertEqual(Main.queries, [q.format(p=project) for q in MAIN_WEEK_QUERIES])
                self.assertEqual(set(got), {"open", "done", "jql"})

    def test_default_snapshot_is_mains_for_both_boards_and_the_view_only_drops_subtasks(self):
        now = datetime(2026, 11, 18, tzinfo=ZoneInfo("America/Chicago"))
        for project in ("JFM", "MEJ"):
            off = SnapshotBoard(project)
            project_snapshot(off, project, 38, True, now, keys=[project])
            self.assertEqual(sorted(off.queries), sorted(q.format(p=project) for q in MAIN_SNAPSHOT_QUERIES))
            on = SnapshotBoard(project)
            project_snapshot(on, project, 38, True, now, keys=[project], v3_week_view=True)
            self.assertTrue(on.queries)
            self.assertTrue(all("issuetype != Sub-task" in q for q in on.queries))

    def test_only_the_gated_week_builder_and_never_the_scheduled_snapshot_passes_the_argument(self):
        from lifeos.jira import snapshot
        self.assertEqual(inspect.getsource(week_caller).count("v3_week_view=True"), 1)                  # only the V3_WEEK_VIEW-gated builder passes it
        self.assertIn('switches.on("V3_WEEK_VIEW"', inspect.getsource(week_caller))
        self.assertNotIn("v3_week_view", inspect.getsource(snapshot.run))

    def test_the_default_view_text_is_unchanged_by_the_new_branch(self):
        got = {"open": [{"key": "JFM-1", "summary": "S", "status": "blocked", "due": "2026-11-10", "done": False}],
               "done": [], "jql": "project = JFM"}
        _, lines = render.week_view(got, TODAY, SITE)
        self.assertEqual(lines[0], "1 open · 1 overdue · 1 blocked · 0 done this week")


if __name__ == "__main__":
    unittest.main()
