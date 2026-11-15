"""LIFE-691: every workflow job has a measured `timeout-minutes`, every stage a budget, and docs/platform/timeouts.md lists them all.

`lifeos/platform/timeouts.json` holds the target for each job (twice the slowest of the last 100 runs, see the doc). Workflow files are protected, so the tighter values arrive in one separate
workflow pull request: until then the jobs in `pending` still have their old value, and this test keeps that list honest. The workflow pull request empties `pending`."""
import json
import pathlib
import unittest

import yaml

from lifeos import run
from lifeos.jobs import tick
from lifeos.platform import budget

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = {p.name: yaml.safe_load(p.read_text()) for p in sorted((ROOT / ".github" / "workflows").glob("*.yml"))}
TIMEOUTS = json.loads((ROOT / "lifeos" / "platform" / "timeouts.json").read_text())
BUDGETS = json.loads((ROOT / "lifeos" / "platform" / "stage_budgets.json").read_text())
DOC = (ROOT / "docs" / "platform" / "timeouts.md").read_text()


def jobs():
    return {f"{wf}::{name}": job for wf, doc in WORKFLOWS.items() for name, job in (doc.get("jobs") or {}).items()}


class WorkflowJobTimeouts(unittest.TestCase):
    def test_every_job_has_a_whole_number_timeout_and_a_row_in_the_table(self):
        found = jobs()
        for key, job in found.items():
            self.assertIsInstance(job.get("timeout-minutes"), int, key)
        self.assertEqual(sorted(found), sorted(TIMEOUTS["jobs"]), "a job was added or removed: regenerate lifeos/platform/timeouts.json and the doc")

    def test_a_job_has_its_target_or_is_listed_as_pending_with_the_value_it_still_has(self):
        pending = set(TIMEOUTS["pending"])
        for key, job in jobs().items():
            row = TIMEOUTS["jobs"][key]
            self.assertLessEqual(row["target"], row["current"], key)                         # a timeout is only ever tightened
            if key in pending:
                self.assertEqual(job["timeout-minutes"], row["current"], f"{key} is pending: it must still have its old value")
                self.assertGreater(row["current"], row["target"], f"{key} is pending but already at its target: remove it from `pending`")
            else:
                self.assertEqual(job["timeout-minutes"], row["target"], key)

    def test_no_target_is_under_the_five_minute_floor_unless_the_job_already_was(self):
        for key, row in TIMEOUTS["jobs"].items():
            self.assertTrue(row["target"] >= 5 or row["target"] == row["current"], key)

    def test_no_job_is_set_under_ten_minutes_if_it_has_ever_run_longer_than_that(self):
        """The ticket's stop rule: a target under 10 minutes for a job whose slowest measured run is over 10 minutes needs Jim."""
        for key, row in TIMEOUTS["jobs"].items():
            if row["slowest_seconds"] is not None and row["target"] < 10:
                self.assertLessEqual(row["slowest_seconds"], 600, key)

    def test_a_target_never_breaks_the_bitwarden_helper_rule(self):
        """tests/contracts/test_bws_helper.py: the helper's retry never takes more than a third of the job's limit: 3 x its wait must fit in the TARGET too, so applying the targets cannot break that test."""
        for key, job in jobs().items():
            for step in job.get("steps") or []:
                if step.get("uses") == "./.github/actions/bws-helper":
                    self.assertLessEqual(int(step["with"]["wait_seconds"]) * 3, TIMEOUTS["jobs"][key]["target"] * 60, key)

    def test_jobs_at_their_limit_are_named_for_jim_in_the_doc(self):
        for key in TIMEOUTS["at_limit"]:
            self.assertIn(f"`{key}`", DOC)


class DocListsEverything(unittest.TestCase):
    def test_every_job_and_every_stage_is_in_the_doc_and_no_row_says_none(self):
        for key in TIMEOUTS["jobs"]:
            workflow, job = key.split("::")
            self.assertIn(f"| `{workflow}` | `{job}` |", DOC, key)
        for stage in run.STAGES:
            self.assertIn(f"| `{stage}` |", DOC, stage)
        self.assertNotIn("| none |", DOC.lower())


class StageBudgets(unittest.TestCase):
    def test_every_stage_has_its_own_row_and_no_stage_relies_on_the_default(self):
        self.assertEqual(sorted(BUDGETS["stages"]), sorted(run.STAGES), "run python tools/regen.py")
        for stage, row in BUDGETS["stages"].items():
            self.assertIsInstance(row["seconds"], int, stage)
            self.assertTrue(60 <= row["seconds"] <= 7200, stage)
            self.assertIsInstance(row["required"], bool, stage)

    def test_the_jobs_tick_stages_use_the_ticks_own_budget_and_its_bulk_rule(self):
        for step in tick.PLAN:
            if step.name in BUDGETS["stages"]:
                self.assertEqual(BUDGETS["stages"][step.name]["seconds"], step.budget, step.name)
                self.assertEqual(BUDGETS["stages"][step.name]["required"], not step.bulk, step.name)

    def test_a_stage_with_a_workflow_step_is_required_exactly_when_a_step_lacks_continue_on_error(self):
        plan = {step.name for step in tick.PLAN}
        steps = {}
        for doc in WORKFLOWS.values():
            for job in (doc.get("jobs") or {}).values():
                for step in job.get("steps") or []:
                    run_text = step.get("run") or ""
                    for token in run_text.split("lifeos.run ")[1:]:
                        stage = token.split()[0] if token.split() else ""
                        steps.setdefault(stage, []).append(bool(step.get("continue-on-error")))
        for stage, flags in steps.items():
            if stage in BUDGETS["stages"] and stage not in plan:
                self.assertEqual(BUDGETS["stages"][stage]["required"], not all(flags), stage)

    def test_the_table_loads_through_the_module_and_unknown_stages_are_optional(self):
        self.assertEqual(budget.budget_for("agenda-card")[1], False)
        self.assertEqual(budget.budget_for("not-a-stage"), (BUDGETS["default_seconds"], False))

    def test_a_budget_never_exceeds_the_largest_limit_of_the_jobs_that_run_the_stage(self):
        limits = {}
        for doc in WORKFLOWS.values():
            for job in (doc.get("jobs") or {}).values():
                for step in job.get("steps") or []:
                    for token in (step.get("run") or "").split("lifeos.run ")[1:]:
                        stage = token.split()[0] if token.split() else ""
                        limits[stage] = max(limits.get(stage, 0), job["timeout-minutes"] * 60)
        for stage, limit in limits.items():
            if stage in BUDGETS["stages"] and stage not in {s.name for s in tick.PLAN}:
                self.assertLessEqual(BUDGETS["stages"][stage]["seconds"], limit, stage)


if __name__ == "__main__":
    unittest.main()
