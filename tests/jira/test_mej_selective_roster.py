"""LIFE-915: synthetic, no Jira writes. The selective path is inert pending Matt consent and backlog API verification."""
import unittest

from lifeos.jira.rollover import preview_mej_selective_carry
from lifeos.platform.jira import JiraError


def issue(key, kind="Task", parent=None):
    return {"key": key, "fields": {"issuetype": {"name": kind, "subtask": kind == "Sub-task"},
                                    "parent": {"key": parent} if parent else None}}


class RosterGuard(unittest.TestCase):
    def test_selected_task_and_its_child_move_together(self):
        rows = [issue("MEJ-100"), issue("MEJ-101", "Sub-task", "MEJ-100"), issue("MEJ-102", "Sub-task", "MEJ-900")]
        got, stranded = preview_mej_selective_carry(rows, ["MEJ-100"])
        self.assertEqual((got, stranded), (["MEJ-100", "MEJ-101"], ["MEJ-102"]))

    def test_unapproved_unfinished_standard_work_blocks_closure_before_any_write(self):
        rows = [issue("MEJ-100"), issue("MEJ-102")]
        with self.assertRaisesRegex(JiraError, "JIRA_MEJ_BACKLOG_UNVERIFIED"):
            preview_mej_selective_carry(rows, ["MEJ-100"])

    def test_finite_weekly_occurrence_never_auto_carries_even_when_in_roster(self):
        with self.assertRaisesRegex(JiraError, "JIRA_MEJ_FINITE_NOT_CARRY"):
            preview_mej_selective_carry([issue("MEJ-100")], ["MEJ-100"], ["MEJ-100"])

    def test_unapproved_subtask_cannot_be_selected_independently(self):
        rows = [issue("MEJ-100"), issue("MEJ-101", "Sub-task", "MEJ-100")]
        with self.assertRaisesRegex(JiraError, "JIRA_MEJ_ROSTER_INVALID"):
            preview_mej_selective_carry(rows, ["MEJ-101"])

    def test_unknown_duplicate_or_wrong_project_roster_fails_closed(self):
        rows = [issue("MEJ-100")]
        for keys in (["MEJ-102"], ["MEJ-100", "MEJ-100"], ["JFM-100"]):
            with self.assertRaisesRegex(JiraError, "JIRA_MEJ_ROSTER_INVALID"):
                preview_mej_selective_carry(rows, keys)

    def test_bad_inventory_and_missing_type_refuse_without_calling_provider(self):
        for rows in ([issue("MEJ-100"), issue("MEJ-100")], [{"key": "MEJ-100", "fields": {}}], [{"fields": {}}]):
            with self.assertRaisesRegex(JiraError, "JIRA_MEJ_INVENTORY_INVALID"):
                preview_mej_selective_carry(rows, ["MEJ-100"])

    def test_empty_approved_roster_can_only_pass_with_no_standard_work(self):
        self.assertEqual(preview_mej_selective_carry([issue("MEJ-200", "Sub-task", "MEJ-999")], []), ([], ["MEJ-200"]))
        with self.assertRaisesRegex(JiraError, "JIRA_MEJ_BACKLOG_UNVERIFIED"):
            preview_mej_selective_carry([issue("MEJ-100")], [])


if __name__ == "__main__":
    unittest.main()
