"""K2/K3: synthetic verified Done history and frozen quarter membership."""

import copy
import json
import unittest
from datetime import date

from lifeos.accountability import read
from lifeos.platform.jira import JiraError

FIRST, LAST = date(2026, 10, 7), date(2027, 1, 5)
GOALS = [{"key": "MEJ-2", "person": "Matt", "lane": "Creative"}]
FROZEN = {
    "schema_version": 1,
    "quarters": {
        "2026-q4": {
            "approved": True,
            "baseline_id": "SYNTHETIC-Q4-TEST-ONLY",
            "approval_ref": "SYNTHETIC-K1-K4-TEST-ONLY",
            "tasks": {
                "MEJ-10": {"parent": "MEJ-2", "goal": "MEJ-2"},
                "MEJ-11": {"parent": "MEJ-2", "goal": "MEJ-2"},
                "MEJ-500": {"parent": "MEJ-147", "goal": "MEJ-2"},
                "MEJ-501": {"parent": "MEJ-147", "goal": "MEJ-2"},
            },
        }
    },
}


def child(key, parent, done=False, kind="Task", resolved=None, field_date=None):
    return {
        "key": key,
        "fields": {
            "parent": {"key": parent},
            "issuetype": {"name": kind, "subtask": kind == "Sub-task"},
            "status": {
                "id": "10015" if done else "10014",
                "name": "Done" if done else "To Do",
                "statusCategory": {"key": "done" if done else "new"},
            },
            "statuscategorychangedate": field_date,
            "resolutiondate": resolved,
            "resolution": {"name": "Done"} if done else None,
            "labels": [],
        },
    }


def status_change(when, before="To Do", after="Done"):
    return {
        "created": when,
        "items": [
            {
                "field": "status",
                "fieldId": "status",
                "fromString": before,
                "toString": after,
                "to": "10015" if after == "Done" else "10014",
            }
        ],
    }


class Jira:
    def __init__(self, projects=None, histories=None):
        self.queries = []
        self.projects = (
            projects
            if projects is not None
            else [
                {
                    "key": "MEJ-147",
                    "fields": {"issuelinks": [{"type": {"name": "Relates"}, "outwardIssue": {"key": "MEJ-2"}}]},
                }
            ]
        )
        self.children = [
            child("MEJ-10", "MEJ-2"),
            child("MEJ-11", "MEJ-2", done=True, resolved="2026-11-20"),
            child("MEJ-500", "MEJ-147"),
            child("MEJ-501", "MEJ-147", done=True, resolved="2026-09-01", field_date="2026-11-20T12:00:00-06:00"),
            child("MEJ-502", "MEJ-147", done=True, kind="Sub-task"),
            child("JFM-10", "JFM-2", done=True, resolved="2026-11-20"),
            child("JFM-11", "JFM-2"),
        ]
        self.histories = (
            histories
            if histories is not None
            else {
                "MEJ-11": [status_change("2026-11-20T10:00:00.000-0600")],
                "MEJ-501": [status_change("2026-11-20T10:00:00.000-0600")],
            }
        )

    def get(self, path, params):
        if path.endswith("/changelog"):
            key = path.split("/")[-2]
            rows = self.histories.get(key, [])
            start = params["startAt"]
            page = rows[start : start + params["maxResults"]]
            return {"values": page, "isLast": start + len(page) >= len(rows), "total": len(rows)}
        jql = params["jql"]
        self.queries.append(jql)
        if "parent in (" in jql:
            inside = jql.split("parent in (", 1)[1].split(")", 1)[0]
            parents = set(inside.split(","))
            rows = [item for item in self.children if item["fields"]["parent"]["key"] in parents]
        elif jql.startswith("key in ("):
            wanted = set(jql.split("key in (", 1)[1].split(")", 1)[0].split(","))
            rows = [item for item in self.children if item["key"] in wanted]
        elif 'issuetype = Epic AND labels = "mej-project"' in jql:
            rows = self.projects
        elif "project = MEJ AND issuetype != Epic AND statusCategory != Done" in jql:
            rows = [
                child("MEJ-10", "MEJ-2"),
                child("MEJ-500", "MEJ-147"),
                child("MEJ-800", "MEJ-999"),
                child("MEJ-502", "MEJ-147", kind="Sub-task"),
            ]
        else:
            raise AssertionError(f"Unexpected Jira query: {jql}")
        return {"issues": rows, "isLast": True}


class FrozenQuarterAndFirstDone(unittest.TestCase):
    def v2(self, fake, baseline=None):
        return read.tally(
            fake,
            GOALS,
            FIRST,
            LAST,
            mej_metric_version="v2",
            frozen_membership=FROZEN if baseline is None else baseline,
        )

    def test_v1_is_default_and_preserves_direct_parent_history(self):
        fake = Jira()
        self.assertEqual(read.tally(fake, GOALS, FIRST, LAST, mej_metric_version="v1"), {"MEJ-2": [1, 2]})
        self.assertEqual(read.unparented(fake, "MEJ", {"MEJ-2"}), 2)

    def test_explicit_v2_counts_only_frozen_standard_tasks(self):
        fake = Jira()
        fake.children.append(child("MEJ-503", "MEJ-147", done=True))
        self.assertEqual(self.v2(fake), {"MEJ-2": [2, 4]})
        self.assertEqual(read.unparented(fake, "MEJ", {"MEJ-2"}, mej_metric_version="v2"), 1)

    def test_first_verified_done_not_last_done_or_resolutiondate(self):
        fake = Jira()
        fake.histories["MEJ-501"] = [
            status_change("2026-09-20T12:00:00-0500"),
            status_change("2026-10-21T12:00:00-0500", "Done", "In Progress"),
            status_change("2026-11-20T10:00:00-0600", "In Progress", "Done"),
        ]
        self.assertEqual(self.v2(fake), {"MEJ-2": [1, 4]})

    def test_first_done_crosses_quarter_in_chicago_time(self):
        fake = Jira()
        fake.histories["MEJ-501"] = [status_change("2027-01-06T03:00:00+00:00")]
        self.assertEqual(self.v2(fake), {"MEJ-2": [2, 4]})

    def test_paginated_history_is_fully_checked_for_earlier_done(self):
        fake = Jira()
        fake.histories["MEJ-501"] = (
            [status_change("2026-09-20T12:00:00-0500")]
            + [{"created": "2026-11-20T10:00:00-0600", "items": []}] * 104
            + [status_change("2026-11-20T10:00:00-0600")]
        )
        self.assertEqual(self.v2(fake), {"MEJ-2": [1, 4]})

    def test_missing_done_history_refuses_even_with_resolutiondate(self):
        fake = Jira()
        fake.histories.pop("MEJ-501")
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_COMPLETION_HISTORY_UNVERIFIED"):
            self.v2(fake)

    def test_incomplete_history_page_refuses(self):
        class Broken(Jira):
            def get(self, path, params):
                if path.endswith("/changelog"):
                    return {"values": [], "isLast": False, "total": 10}
                return super().get(path, params)

        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_COMPLETION_HISTORY_UNVERIFIED"):
            self.v2(Broken())

    def test_no_approved_quarter_membership_fails_closed(self):
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED"):
            self.v2(Jira(), {"schema_version": 1, "quarters": {}})

    def test_membership_does_not_follow_the_parent_the_list_recorded_or_the_one_jira_shows(self):
        recorded = copy.deepcopy(FROZEN)
        recorded["quarters"]["2026-q4"]["tasks"]["MEJ-501"]["parent"] = "MEJ-352"
        self.assertEqual(self.v2(Jira(), recorded), self.v2(Jira()))

    def test_missing_frozen_task_fails_instead_of_silently_reducing_denominator(self):
        bad = copy.deepcopy(FROZEN)
        bad["quarters"]["2026-q4"]["tasks"]["MEJ-999"] = {"parent": "MEJ-147", "goal": "MEJ-2"}
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_MEMBERSHIP_DRIFT"):
            self.v2(Jira(), bad)

    def test_ambiguous_project_goal_links_are_rejected(self):
        projects = [
            {
                "key": "MEJ-147",
                "fields": {
                    "issuelinks": [
                        {"type": {"name": "Relates"}, "inwardIssue": {"key": "MEJ-2"}},
                        {"type": {"name": "Relates"}, "outwardIssue": {"key": "MEJ-3"}},
                    ]
                },
            }
        ]
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_GOAL_AMBIGUOUS"):
            read._mej_project_goals(Jira(projects), {"MEJ-2", "MEJ-3"})

    def test_other_link_types_cannot_assign_membership(self):
        projects = [
            {
                "key": "MEJ-147",
                "fields": {
                    "issuelinks": [
                        {"type": {"name": "Blocks"}, "outwardIssue": {"key": "MEJ-2"}},
                        {"type": {"name": "Relates"}, "inwardIssue": {"key": "MEJ-999"}},
                    ]
                },
            }
        ]
        self.assertEqual(self.v2(Jira(projects)), self.v2(Jira()))  # project Epic links never assign or move a member

    def test_jfm_gets_the_same_v2_count_from_its_own_frozen_list(self):
        """'918 uniform new' (comment 14228): two frozen lists, one per board, in one quarter row."""
        both = {
            "schema_version": 1,
            "quarters": {
                "2026-q4": {
                    "boards": {
                        "MEJ": FROZEN["quarters"]["2026-q4"],
                        "JFM": {
                            "approved": True,
                            "baseline_id": "SYNTHETIC-JFM",
                            "approval_ref": "SYNTHETIC",
                            "tasks": {
                                "JFM-10": {"parent": "JFM-2", "goal": "JFM-2"},
                                "JFM-11": {"parent": "JFM-2", "goal": "JFM-2"},
                            },
                        },
                    }
                }
            },
        }
        fake = Jira(
            histories={
                "MEJ-11": [status_change("2026-11-20T10:00:00.000-0600")],
                "MEJ-501": [status_change("2026-11-20T10:00:00.000-0600")],
                "JFM-10": [status_change("2026-11-20T10:00:00.000-0600")],
            }
        )
        goals = GOALS + [{"key": "JFM-2", "person": "Jim", "lane": "Career"}]
        counts = read.tally(fake, goals, FIRST, LAST, mej_metric_version="v2", frozen_membership=both)
        self.assertEqual(counts, {"MEJ-2": [2, 4], "JFM-2": [1, 2]})

    def test_jfm_without_its_own_approved_list_fails_closed_even_when_mej_has_one(self):
        goals = GOALS + [{"key": "JFM-2", "person": "Jim", "lane": "Career"}]
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED"):
            read.tally(Jira(), goals, FIRST, LAST, mej_metric_version="v2", frozen_membership=FROZEN)
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED"):
            read.tally(
                Jira(),
                [{"key": "JFM-2", "person": "Jim", "lane": "Career"}],
                FIRST,
                LAST,
                mej_metric_version="v2",
                frozen_membership=None,
            )

    def test_v1_issues_exactly_main_requests_and_never_reads_history_or_project_labels(self):
        """Parity with main: the default path asks Jira only for the quarter epics' children (main's field list) and the
        open-work scan
        """
        self.assertEqual(
            read.CHILD_FIELDS, "status,parent,resolutiondate,issuetype,resolution,labels,statuscategorychangedate"
        )
        for goals, project, keys in (
            (GOALS, "MEJ", {"MEJ-2"}),
            ([{"key": "JFM-2", "person": "Jim"}], "JFM", {"JFM-2"}),
        ):
            fake = Jira()
            fake.get_paths = []
            original = fake.get
            fake.get = lambda path, params, original=original, paths=fake.get_paths: (
                paths.append(path),
                original(path, params),
            )[1]
            read.tally(fake, goals, FIRST, LAST)
            if project == "MEJ":
                read.unparented(fake, project, keys)
            self.assertFalse([p for p in fake.get_paths if p.endswith("/changelog")])
            self.assertFalse([q for q in fake.queries if "mej-project" in q])

    def test_v2_never_activates_without_the_explicit_argument(self):
        fake = Jira()
        self.assertEqual(
            read.tally(fake, GOALS, FIRST, LAST, frozen_membership=FROZEN), read.tally(Jira(), GOALS, FIRST, LAST)
        )

    def test_the_shipped_manifest_holds_exactly_the_approved_q4_baselines_and_invents_nothing(self):
        manifest = json.loads(read.MEJ_MEMBERSHIP.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(list(manifest["quarters"]), ["2026-q4"])
        row = manifest["quarters"]["2026-q4"]
        self.assertEqual((row["approved"], len(row["tasks"])), (True, 83))
        per_goal = {}
        for task in row["tasks"].values():
            per_goal[task["goal"]] = per_goal.get(task["goal"], 0) + 1
        self.assertEqual(per_goal, {"MEJ-1": 29, "MEJ-2": 14, "MEJ-3": 22, "MEJ-4": 18})  # the published denominators
        self.assertNotIn("gaps", row)  # every Matt goal now has an approved list
        for task in row["tasks"].values():
            self.assertTrue(task["reason"] and task["approval"] and task["effective"])
        with self.assertRaises(JiraError) as caught:  # JFM has no approved list
            read._frozen_row(FIRST, None, "JFM")
        self.assertIn("ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED", str(caught.exception))

    def test_unknown_kpi_version_is_blocked(self):
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_METRIC_VERSION_INVALID"):
            read.tally(Jira(), GOALS, FIRST, LAST, mej_metric_version="v0")


if __name__ == "__main__":
    unittest.main()
