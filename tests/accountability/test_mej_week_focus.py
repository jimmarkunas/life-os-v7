"""Matt's weekly view shows deliverables, not project internals."""
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.accountability import read, render
from lifeos.jira.snapshot import project_snapshot
from lifeos.platform.jira import JiraError


def issue(key, status="To Do", due=None, kind="Task"):
    return {"key": key, "fields": {"summary": f"Synthetic {key}", "status": {
        "name": status, "statusCategory": {"key": "done" if status == "Done" else "new"}},
        "issuetype": {"name": kind, "subtask": kind == "Sub-task"}, "duedate": due}}


class FakeJira:
    def __init__(self):
        self.queries = []

    def get(self, path, params):
        jql = params["jql"]
        self.queries.append(jql)
        if "statusCategory = Done" in jql:
            rows = [issue("MEJ-300", "Done")]
        elif "sprint in openSprints()" in jql:
            rows = [issue("MEJ-100", "To Do", "2026-11-20")]
        elif 'duedate <= "2026-11-22"' in jql:
            rows = [issue("MEJ-100", "To Do", "2026-11-20"),
                    issue("MEJ-200", "blocked", "2026-11-10")]
        else:
            raise AssertionError(f"Unexpected Jira query: {jql}")
        return {"issues": rows, "isLast": True}


class WeekFocus(unittest.TestCase):
    def test_mej_sprint_commitments_separate_from_due_backlog_and_done(self):
        fake = FakeJira()
        got = read.week(fake, "MEJ", date(2026, 11, 18), mej_task_focus=True)
        self.assertEqual([r["key"] for r in got["open"]], ["MEJ-100"])
        self.assertEqual([r["key"] for r in got["outside"]], ["MEJ-200"])
        self.assertEqual([r["key"] for r in got["done"]], ["MEJ-300"])
        self.assertTrue(all("issuetype != Sub-task" in q for q in fake.queries))
        parts, lines = render.week_view(got, date(2026, 11, 18), "https://example.invalid")
        self.assertEqual(lines[0], "1 sprint open · 1 due outside sprint · 1 overdue · 1 blocked · 1 done this week")
        self.assertIn("Sprint commitments (1):", lines)
        self.assertIn("Due outside sprint (1):", lines)
        self.assertIn("MEJ-200 Synthetic MEJ-200 (blocked, overdue)", lines)
        self.assertIn("Done this week (1):", lines)
        self.assertEqual(len([p for p in parts if p[1] == "https://example.invalid/browse/MEJ-100"]), 1)

    def test_mej_invalid_sprint_query_fails_closed_without_faking_commitments(self):
        class InvalidSprint(FakeJira):
            def get(self, path, params):
                if "sprint in openSprints()" in params["jql"]:
                    self.queries.append(params["jql"])
                    raise JiraError("ACCOUNTABILITY_JIRA_HTTP_400")
                return super().get(path, params)
        fake = InvalidSprint()
        with self.assertRaises(JiraError):
            read.week(fake, "MEJ", date(2026, 11, 18), mej_task_focus=True)
        self.assertFalse(any('statusCategory = "In Progress"' in q for q in fake.queries))
        
    def test_jfm_keeps_the_legacy_weekly_query_and_summary_shape(self):
        class JimFake(FakeJira):
            def get(self, path, params):
                jql = params["jql"]
                self.queries.append(jql)
                if "statusCategory = Done" in jql:
                    rows = []
                else:
                    rows = [issue("JFM-100")]
                return {"issues": rows, "isLast": True}
        fake = JimFake()
        got = read.week(fake, "JFM", date(2026, 11, 18))
        self.assertNotIn("outside", got)
        self.assertNotIn("issuetype != Sub-task", fake.queries[0])
        _, lines = render.week_view(got, date(2026, 11, 18), None)
        self.assertEqual(lines[0], "1 open · 0 overdue · 0 blocked · 0 done this week")
        self.assertIn("In progress / To do (1):", lines)


class FakeBoard:
    def __init__(self, project):
        self.project = project
        self.queries = []

    def board(self, board_id):
        return {"type": "scrum", "name": "Synthetic Board"}

    def sprints(self, board_id, state):
        if state == "active":
            return [{"id": 178, "name": f"{self.project} Sprint 12", "state": "active",
                     "startDate": "2026-11-16T00:00:00-06:00", "endDate": "2026-11-22T23:30:00-06:00"}]
        return []

    def board_issues(self, board_id, jql, fields):
        self.queries.append(jql)
        return []


class SnapshotFocus(unittest.TestCase):
    def test_mej_snapshot_filters_subtasks_only_from_task_level_sections(self):
        fake = FakeBoard("MEJ")
        snap = project_snapshot(fake, "MEJ", 38, True,
                                datetime(2026, 11, 18, tzinfo=ZoneInfo("America/Chicago")), keys=["MEJ"], mej_task_focus=True)
        self.assertEqual(snap["project"], "MEJ")
        self.assertTrue(fake.queries)
        self.assertTrue(all("issuetype != Sub-task" in q for q in fake.queries))

    def test_jfm_snapshot_does_not_inherit_mej_filters(self):
        fake = FakeBoard("JFM")
        project_snapshot(fake, "JFM", 38, True,
                         datetime(2026, 11, 18, tzinfo=ZoneInfo("America/Chicago")), keys=["JFM"])
        self.assertTrue(fake.queries)
        self.assertTrue(all("issuetype != Sub-task" not in q for q in fake.queries))


MAIN_WEEK_QUERIES = [  # exactly the queries main sends for any project
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


class OffByDefault(unittest.TestCase):
    """isolation: with no explicit argument MEJ behaves exactly as on main (JFM, V3 blocks and the summary untouched).
    """

    def week_run(self, project, rows_open, rows_done):
        class F:
            queries = []

            def get(self, path, params):
                F.queries.append(params["jql"])
                return {"issues": rows_done if "statusCategory = Done" in params["jql"] else rows_open, "isLast": True}
        F.queries = []
        got = read.week(F(), project, date(2026, 11, 18))
        return got, F.queries

    def test_default_week_queries_and_shape_are_mains_for_mej_and_jfm(self):
        for project in ("MEJ", "JFM"):
            got, queries = self.week_run(project, [issue(f"{project}-1", "blocked", "2026-11-10")], [issue(f"{project}-2", "Done")])
            self.assertEqual(queries, [q.format(p=project) for q in MAIN_WEEK_QUERIES])
            self.assertEqual(set(got), {"open", "done", "jql"})

    def test_default_week_view_text_is_identical_for_mej_and_jfm_in_normal_and_empty_cases(self):
        for project in ("MEJ", "JFM"):
            got, _ = self.week_run(project, [issue(f"{project}-1", "blocked", "2026-11-10"), issue(f"{project}-3")], [issue(f"{project}-2", "Done")])
            _, lines = render.week_view(got, date(2026, 11, 18), "https://example.invalid")
            self.assertEqual(lines[0], "2 open · 1 overdue · 1 blocked · 1 done this week")
            self.assertIn("In progress / To do (2):", lines)
            self.assertNotIn("Sprint commitments", " ".join(lines))
            empty, _ = self.week_run(project, [], [])
            self.assertEqual(render.week_view(empty, date(2026, 11, 18), "https://example.invalid")[1][0], "0 open · 0 overdue · 0 blocked · 0 done this week")

    def test_default_mej_keeps_mains_project_without_sprints_fallback_and_real_failures_still_raise(self):
        class NoSprints:
            queries = []

            def get(self, path, params):
                NoSprints.queries.append(params["jql"])
                if "sprint in openSprints()" in params["jql"]:
                    raise JiraError("ACCOUNTABILITY_JIRA_HTTP_400")
                return {"issues": [], "isLast": True}
        NoSprints.queries = []
        read.week(NoSprints(), "MEJ", date(2026, 11, 18))
        self.assertTrue(any('statusCategory = "In Progress"' in q for q in NoSprints.queries))

        class Down:
            def get(self, path, params):
                raise JiraError("ACCOUNTABILITY_JIRA_HTTP_500")
        with self.assertRaises(JiraError):
            read.week(Down(), "MEJ", date(2026, 11, 18))

    def test_default_snapshot_queries_are_mains_for_mej_and_jfm(self):
        for project in ("MEJ", "JFM"):
            fake = FakeBoard(project)
            project_snapshot(fake, project, 38, True, datetime(2026, 11, 18, tzinfo=ZoneInfo("America/Chicago")), keys=[project])
            self.assertEqual(sorted(fake.queries), sorted(q.format(p=project) for q in MAIN_SNAPSHOT_QUERIES))

    def test_week_caller_never_passes_the_argument(self):
        import inspect
        from lifeos.accountability import week as caller
        self.assertNotIn("mej_task_focus", inspect.getsource(caller))


if __name__ == "__main__":
    unittest.main()
