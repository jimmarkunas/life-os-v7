"""LIFE-914: Matt's weekly view shows deliverables, not project internals.

All fixtures are synthetic; these tests must not use a live Jira or Notion
account or mutate either provider. JFM's existing contract stays unchanged.
"""
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.accountability import read, render
from lifeos.jira.snapshot import project_snapshot


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
        got = read.week(fake, "MEJ", date(2026, 11, 18))
        self.assertEqual([r["key"] for r in got["open"]], ["MEJ-100"])
        self.assertEqual([r["key"] for r in got["outside"]], ["MEJ-200"])
        self.assertEqual([r["key"] for r in got["done"]], ["MEJ-300"])
        self.assertTrue(all("issuetype != Sub-task" in q for q in fake.queries))
        parts, lines = render.week_view(got, date(2026, 11, 18), "https://example.invalid")
        self.assertEqual(lines[0], "1 sprint open · 1 due outside sprint · 1 overdue · 1 blocked · 1 done this week")
        self.assertIn("Sprint commitments (1)", lines)
        self.assertIn("Due outside sprint (1)", lines)
        self.assertIn("MEJ-200 Synthetic MEJ-200 (blocked, overdue)", lines)
        self.assertIn("Done this week (1)", lines)
        self.assertEqual(len([p for p in parts if p[1] == "https://example.invalid/browse/MEJ-100"]), 1)

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
        self.assertIn("In progress / To do (1)", lines)


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
                                datetime(2026, 11, 18, tzinfo=ZoneInfo("America/Chicago")), keys=["MEJ"])
        self.assertEqual(snap["project"], "MEJ")
        self.assertTrue(fake.queries)
        self.assertTrue(all("issuetype != Sub-task" in q for q in fake.queries))

    def test_jfm_snapshot_does_not_inherit_mej_filters(self):
        fake = FakeBoard("JFM")
        project_snapshot(fake, "JFM", 38, True,
                         datetime(2026, 11, 18, tzinfo=ZoneInfo("America/Chicago")), keys=["JFM"])
        self.assertTrue(fake.queries)
        self.assertTrue(all("issuetype != Sub-task" not in q for q in fake.queries))


if __name__ == "__main__":
    unittest.main()
