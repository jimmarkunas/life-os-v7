"""every workflow job has a measured `timeout-minutes` and docs/platform/timeouts.md lists them all (the per-stage
budgets are checked in.
"""

import json
import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = {p.name: yaml.safe_load(p.read_text()) for p in sorted((ROOT / ".github" / "workflows").glob("*.yml"))}
TIMEOUTS = json.loads((ROOT / "lifeos" / "platform" / "timeouts.json").read_text())
DOC = (ROOT / "docs" / "platform" / "timeouts.md").read_text()


def jobs():
    return {
        f"{wf}::{name}": job
        for wf, doc in WORKFLOWS.items()
        for name, job in (doc.get("jobs") or {}).items()
        if "uses" not in job  # a job that calls a reusable workflow has no runner and no timeout of its own: the called job does
    }


class WorkflowJobTimeouts(unittest.TestCase):
    def test_every_job_has_a_whole_number_timeout_and_a_row_in_the_table(self):
        found = jobs()
        for key, job in found.items():
            self.assertIsInstance(job.get("timeout-minutes"), int, key)
        self.assertEqual(
            sorted(found),
            sorted(TIMEOUTS["jobs"]),
            "a job was added or removed: regenerate lifeos/platform/timeouts.json and the doc",
        )

    def test_a_job_has_its_target_or_is_listed_as_pending_with_the_value_it_still_has(self):
        pending = set(TIMEOUTS["pending"])
        for key, job in jobs().items():
            row = TIMEOUTS["jobs"][key]
            self.assertLessEqual(row["target"], row["current"], key)  # a timeout is only ever tightened
            if key in pending:
                self.assertEqual(
                    job["timeout-minutes"], row["current"], f"{key} is pending: it must still have its old value"
                )
                self.assertGreater(
                    row["current"],
                    row["target"],
                    f"{key} is pending but already at its target: remove it from `pending`",
                )
            else:
                self.assertEqual(job["timeout-minutes"], row["target"], key)

    def test_no_target_is_under_the_five_minute_floor_unless_the_job_already_was(self):
        for key, row in TIMEOUTS["jobs"].items():
            self.assertTrue(row["target"] >= 5 or row["target"] == row["current"], key)

    def test_no_job_is_set_under_ten_minutes_if_it_has_ever_run_longer_than_that(self):
        """The ticket's stop rule: a target under 10 minutes for a job whose slowest measured run is over 10 minutes
        needs Jim.
        """
        for key, row in TIMEOUTS["jobs"].items():
            if row["slowest_seconds"] is not None and row["target"] < 10:
                self.assertLessEqual(row["slowest_seconds"], 600, key)

    def test_a_target_never_breaks_the_bitwarden_helper_rule(self):
        """tests/contracts/test_bws_helper.py: the helper's retry never takes more than a third of the job's limit: 3 x
        its wait must fit in the TARGET too, so.
        """
        for key, job in jobs().items():
            for step in job.get("steps") or []:
                if step.get("uses") == "./.github/actions/bws-helper":
                    self.assertLessEqual(
                        int(step["with"]["wait_seconds"]) * 3, TIMEOUTS["jobs"][key]["target"] * 60, key
                    )

    def test_jobs_at_their_limit_are_named_for_jim_in_the_doc(self):
        for key in TIMEOUTS["at_limit"]:
            self.assertIn(f"`{key}`", DOC)


class DocListsEverything(unittest.TestCase):
    def test_every_job_is_in_the_doc_and_no_row_says_none(self):
        """Every stage's row is checked against the manifest in tests/contracts/test_stages.py."""
        for key in TIMEOUTS["jobs"]:
            workflow, job = key.split("::")
            self.assertIn(f"| `{workflow}` | `{job}` |", DOC, key)
        self.assertNotIn("| none |", DOC.lower())


if __name__ == "__main__":
    unittest.main()
