"""LIFE-913: synthetic opt-in MEJ project-to-quarter attribution tests.

These tests use no live Jira/Notion data and write nothing to any provider.
The historical direct-child model remains the default until Matt approves
the migration and a project Epic carries the 'mej-project' opt-in label.
"""
import unittest
from datetime import date

from lifeos.accountability import read
from lifeos.platform.jira import JiraError

FIRST, LAST = date(2026, 10, 7), date(2027, 1, 5)
GOALS = [{"key": "MEJ-2", "person": "Matt", "lane": "Creative"}]


def child(key, parent, done=False, kind="Task", completed=None, resolved=None):
    return {"key": key, "fields": {
        "parent": {"key": parent},
        "issuetype": {"name": kind, "subtask": kind == "Sub-task"},
        "status": {"name": "Done" if done else "To Do",
                   "statusCategory": {"key": "done" if done else "new"}},
        "statuscategorychangedate": completed,
        "resolutiondate": resolved,
    }}


class Jira:
    def __init__(self, projects=None):
        self.queries = []
        self.projects = projects if projects is not None else [
            {"key": "MEJ-147", "fields": {"issuelinks": [
                {"type": {"name": "Relates"}, "outwardIssue": {"key": "MEJ-2"}}
            ]}}
        ]

    def get(self, path, params):
        jql = params["jql"]
        self.queries.append(jql)
        if "parent in (MEJ-2)" in jql:
            rows = [child("MEJ-10", "MEJ-2"),
                    child("MEJ-11", "MEJ-2", done=True, resolved="2026-11-20")]
        elif 'issuetype = Epic AND labels = "mej-project"' in jql:
            rows = self.projects
        elif "parent in (MEJ-147)" in jql:
            rows = [child("MEJ-500", "MEJ-147"),
                    child("MEJ-501", "MEJ-147", done=True,
                          completed="2026-11-20T12:00:00-06:00",
                          resolved="2026-09-01T12:00:00-05:00"),
                    child("MEJ-502", "MEJ-147", done=True, kind="Sub-task",
                          completed="2026-11-20T12:00:00-06:00")]
        elif "project = MEJ AND issuetype != Epic AND statusCategory != Done" in jql:
            rows = [child("MEJ-10", "MEJ-2"), child("MEJ-500", "MEJ-147"),
                    child("MEJ-800", "MEJ-999"),
                    child("MEJ-502", "MEJ-147", kind="Sub-task")]
        else:
            raise AssertionError(f"Unexpected Jira query: {jql}")
        return {"issues": rows, "isLast": True}


class ProjectGoalAttribution(unittest.TestCase):
    def test_linked_project_deliverables_count_once_and_subtasks_do_not(self):
        fake = Jira()
        counts = read.tally(fake, GOALS, FIRST, LAST)
        self.assertEqual(counts, {"MEJ-2": [2, 4]})
        self.assertEqual(read.unparented(fake, "MEJ", {"MEJ-2"}), 1)
        self.assertTrue(any("issuelinks" in params for params in fake.queries) is False)  # JQL strings only

    def test_no_opted_in_projects_keeps_old_direct_parent_semantics(self):
        fake = Jira(projects=[])
        self.assertEqual(read.tally(fake, GOALS, FIRST, LAST), {"MEJ-2": [1, 2]})
        self.assertEqual(read.unparented(fake, "MEJ", {"MEJ-2"}), 2)

    def test_done_outside_quarter_keeps_denominator_but_not_quarter_numerator(self):
        class Outside(Jira):
            def get(self, path, params):
                answer = super().get(path, params)
                if "parent in (MEJ-147)" in params["jql"]:
                    answer["issues"][1]["fields"]["statuscategorychangedate"] = "2026-09-20T12:00:00-05:00"
                return answer
        self.assertEqual(read.tally(Outside(), GOALS, FIRST, LAST), {"MEJ-2": [1, 4]})

    def test_missing_done_status_transition_fails_closed(self):
        class Missing(Jira):
            def get(self, path, params):
                answer = super().get(path, params)
                if "parent in (MEJ-147)" in params["jql"]:
                    answer["issues"][1]["fields"]["statuscategorychangedate"] = None
                return answer
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_COMPLETION_DATE_MISSING"):
            read.tally(Missing(), GOALS, FIRST, LAST)

    def test_multiple_quarter_goal_links_fail_closed(self):
        projects = [{"key": "MEJ-147", "fields": {"issuelinks": [
            {"type": {"name": "Relates"}, "inwardIssue": {"key": "MEJ-2"}},
            {"type": {"name": "Relates"}, "outwardIssue": {"key": "MEJ-3"}},
        ]}}]
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_GOAL_AMBIGUOUS"):
            read._mej_project_goals(Jira(projects), {"MEJ-2", "MEJ-3"})

    def test_other_link_types_and_unrelated_epics_do_not_establish_goals(self):
        projects = [{"key": "MEJ-147", "fields": {"issuelinks": [
            {"type": {"name": "Blocks"}, "outwardIssue": {"key": "MEJ-2"}},
            {"type": {"name": "Relates"}, "inwardIssue": {"key": "MEJ-999"}},
        ]}}]
        self.assertEqual(read._mej_project_goals(Jira(projects), {"MEJ-2"}), {})


if __name__ == "__main__":
    unittest.main()
