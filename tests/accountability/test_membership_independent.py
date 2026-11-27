"""LIFE-1025: quarterly membership is the approved key list, never the Jira hierarchy (R2.1 to R2.8). Synthetic keys only."""

import copy
import unittest
from datetime import date

from lifeos.accountability import read
from lifeos.platform.jira import JiraError
from tests.accountability.test_mej_goal_links import FIRST, LAST, Jira, child, status_change

GOAL = "MEJ-1"
GOALS = [{"key": GOAL, "person": "Matt", "lane": "Career"}]
KEYS = [f"MEJ-{n}" for n in range(100, 129)]  # 29 members
DONE = set(KEYS[:12])  # 12 delivered inside the quarter


def task(key, goal=GOAL):
    return {
        "parent": goal,
        "goal": goal,
        "reason": "Q4 baseline",
        "approval": "SYNTHETIC-APPROVAL-TEST-ONLY",
        "effective": "2026-10-07",
    }


def manifest(**extra):
    return {
        "schema_version": 2,
        "quarters": {
            "2026-q4": {
                "approved": True,
                "baseline_id": "SYNTHETIC-Q4",
                "approval_ref": "SYNTHETIC",
                "tasks": {k: task(k) for k in KEYS},
                **extra,
            }
        },
    }


def world(parent_of=lambda key: GOAL, extra=()):
    fake = Jira()
    fake.children = [child(k, parent_of(k), done=k in DONE, resolved="2026-11-20") for k in KEYS] + list(extra)
    fake.histories = {k: [status_change("2026-11-20T10:00:00.000-0600")] for k in DONE}
    return fake


def v2(fake, mf=None, today=None):
    return read._mej_v2_tally(fake, {GOAL}, FIRST, LAST, mf or manifest(), "MEJ", today=today)


class Independent(unittest.TestCase):
    def test_a_parent_change_does_not_move_a_member_and_14_reparented_keys_keep_the_denominator_29(self):
        before = v2(world())
        moved = v2(world(lambda k: "MEJ-383" if k in KEYS[:14] else GOAL))  # R2.1 and R2.3
        self.assertEqual((before, moved), ({GOAL: [12, 29]}, {GOAL: [12, 29]}))

    def test_a_new_project_epic_task_is_unassigned_until_it_is_on_the_approved_list(self):
        new = child("MEJ-900", "MEJ-383", done=True, resolved="2026-11-25")
        self.assertEqual(v2(world(extra=[new])), {GOAL: [12, 29]})  # R2.2

    def test_delivery_inside_the_quarter_counts_whatever_the_hierarchy(self):
        fake = world(lambda k: "MEJ-384")
        fake.children[20] = child(KEYS[20], "MEJ-384", done=True, resolved="2026-11-26")
        fake.histories[KEYS[20]] = [status_change("2026-11-26T09:00:00.000-0600")]
        self.assertEqual(v2(fake), {GOAL: [13, 29]})  # R2.6


class Auditable(unittest.TestCase):
    def test_every_amendment_names_key_quarter_goal_reason_approval_and_effective_date(self):
        row = read._frozen_row(FIRST, manifest(), "MEJ")
        self.assertEqual(sorted(row["tasks"][KEYS[0]]), ["approval", "effective", "goal", "parent", "reason"])  # R2.4
        self.assertTrue(manifest()["quarters"]["2026-q4"]["baseline_id"])
        bad = copy.deepcopy(manifest())
        del bad["quarters"]["2026-q4"]["tasks"][KEYS[3]]["approval"]
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_MEMBERSHIP_INCOMPLETE"):
            read._frozen_row(FIRST, bad, "MEJ")
        bad = copy.deepcopy(manifest())
        bad["quarters"]["2026-q4"]["tasks"][KEYS[3]]["effective"] = "soon"
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_MEMBERSHIP_INCOMPLETE"):
            read._frozen_row(FIRST, bad, "MEJ")

    def test_missing_duplicate_or_ambiguous_membership_degrades_with_a_specific_code(self):
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED"):
            v2(world(), {"schema_version": 2, "quarters": {}})  # R2.5 not approved
        gone = world()
        gone.children.pop()
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_MEMBERSHIP_DRIFT"):
            v2(gone)  # a member that no longer reads
        twice = world()
        twice.children.append(copy.deepcopy(twice.children[0]))
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_MEMBERSHIP_DRIFT"):
            v2(twice)  # duplicate
        stray = copy.deepcopy(manifest())
        stray["quarters"]["2026-q4"]["tasks"][KEYS[0]]["goal"] = "MEJ-2"  # a goal that is not this quarter's board
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_MEMBERSHIP_DRIFT"):
            v2(world(), stray)  # ambiguous goal


class History(unittest.TestCase):
    def test_a_closed_quarter_snapshot_is_returned_and_never_recounted(self):
        snap = {"version": "q4-published-1", "figures": {GOAL: [12, 29]}}
        mf = manifest(snapshot=snap)
        later = world(lambda k: "MEJ-383")  # a later migration
        later.children = later.children[:5]  # and members gone
        self.assertEqual(v2(later, mf, today=date(2027, 1, 6)), {GOAL: [12, 29]})  # R2.7
        self.assertEqual(later.queries, [])  # no Jira read at all
        self.assertEqual(snap, {"version": "q4-published-1", "figures": {GOAL: [12, 29]}})
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_SNAPSHOT_INCOMPLETE"):
            v2(world(), manifest(snapshot={"version": "x", "figures": {}}), today=date(2027, 1, 6))
        self.assertEqual(v2(world(), mf, today=date(2026, 12, 1)), {GOAL: [12, 29]})  # open quarter: recounted

    def test_a_goal_with_incomplete_baseline_evidence_is_a_reported_gap_not_a_reconstruction(self):
        mf = manifest(gaps={GOAL: "no pre-migration published figure"})
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_MEJ_BASELINE_GAP"):
            v2(world(), mf)  # R2.8


if __name__ == "__main__":
    unittest.main()
