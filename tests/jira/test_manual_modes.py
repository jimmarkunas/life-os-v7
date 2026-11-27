"""AC10: the permanent manual runs go through the one existing entry point, `python -m lifeos.run jira-rollover
[--live]`, and each mode is pinned here.
"""
import unittest
from unittest.mock import patch

from lifeos import run as entry
from lifeos.jira import rollover
from tests.jira.test_jira import ENV, FakeJira, at, week

WRITES = {"create", "move", "closed", "active", "backlog"}


class Modes(unittest.TestCase):
    def client(self):
        return FakeJira(week(), [("A-1", 1, "To Do"), ("A-2", 1, "Done")])

    def test_synthetic_mode_never_builds_a_real_client(self):
        with patch("lifeos.platform.jira.Jira.from_env", side_effect=AssertionError("a real client was built")):
            result = rollover.run(1, False, environ=ENV, client=self.client(), now=at(5, 6), sleep=lambda s: None)
        self.assertEqual(result["failed"], 0)

    def test_dry_run_plans_and_writes_nothing(self):
        client = self.client()
        result = rollover.run(1, False, environ=ENV, client=client, now=at(5, 6), sleep=lambda s: None)
        self.assertEqual(client.log, [])
        self.assertEqual(result["writes"], 0)
        self.assertGreaterEqual(result["would_create"] + result["would_carry"], 1)

    def test_live_mode_is_the_only_one_that_writes(self):
        client = self.client()
        result = rollover.run(1, True, environ=ENV, client=client, now=at(5, 6), sleep=lambda s: None)
        self.assertTrue(WRITES & set(client.log))
        self.assertGreater(result["writes"], 0)

    def test_the_stage_name_and_flag_are_the_documented_entry_point(self):
        self.assertIn("jira-rollover", entry.STAGES)
        self.assertIn("jira-rollover-scheduled", entry.STAGES)
        with patch("lifeos.jira.rollover.run", return_value={"ok": 1}) as run:
            entry.STAGES["jira-rollover"](5, False)
            entry.STAGES["jira-rollover"](5, True)
        self.assertEqual([call.args[1] for call in run.call_args_list], [False, True])


if __name__ == "__main__":
    unittest.main()
