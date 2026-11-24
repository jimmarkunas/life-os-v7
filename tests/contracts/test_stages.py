"""LIFE-768 / LIFE-769: generic checks that loop over the stage manifest (lifeos/platform/stages.json), in place of per-stage pins.

A stage is one manifest entry. These checks assert, for every entry: it is a real stage and every stage has an entry, its fields have the right shape, its budget is bounded and agrees with the workflow job that runs it and the Jobs tick plan,
its isolation status is true of its code, and every file rendered from the manifest (stage_budgets.json, the two doc tables) is exactly what the manifest says. Adding a stage adds no test."""
import importlib.util
import json
import pathlib
import unittest

import yaml

from lifeos import run
from lifeos.jobs import tick
from lifeos.platform import budget, stages
from tests.contracts import test_stage_isolation as isolation

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = stages.load()
ENTRIES = DATA["stages"]
STATUSES = {"helper", "guarded", "single", "exempt", "partial-red"}
WORKFLOWS = {p.name: yaml.safe_load(p.read_text()) for p in sorted((ROOT / ".github/workflows").glob("*.yml"))}
PLAN = {step.name: step for step in tick.PLAN}


def workflow_steps():
    """{stage: [(job limit in seconds, step has continue-on-error)]} from every `lifeos.run <stage>` step in the workflows."""
    found = {}
    for doc in WORKFLOWS.values():
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                for token in (step.get("run") or "").split("lifeos.run ")[1:]:
                    if token.split():
                        found.setdefault(token.split()[0], []).append((int(job.get("timeout-minutes", 0)) * 60, bool(step.get("continue-on-error"))))
    return found


def table_rows(path, heading):
    text = (ROOT / path).read_text().split(f"## {heading}", 1)[1].split("\n## ", 1)[0]
    return {line.split("|")[1].strip().strip("`"): line for line in text.splitlines() if line.startswith("| `")}


class ManifestShape(unittest.TestCase):
    def test_every_stage_has_exactly_one_entry_and_no_entry_is_stale(self):
        self.assertEqual(sorted(set(run.STAGES) - set(ENTRIES)), [], "a stage has no entry in lifeos/platform/stages.json: run python tools/regen.py (a loop with no try needs your verdict)")
        self.assertEqual(sorted(set(ENTRIES) - set(run.STAGES)), [], "an entry names a stage that no longer exists")

    def test_every_entry_has_the_same_fields_with_the_right_types_and_a_bounded_budget(self):
        for name, entry in ENTRIES.items():
            self.assertEqual(sorted(k for k in entry if k not in stages.OPTIONAL_KEYS), list(stages.ENTRY_KEYS), name)
            self.assertTrue(set(entry) - set(stages.ENTRY_KEYS) <= set(stages.OPTIONAL_KEYS), name)
            self.assertIsInstance(entry["seconds"], int, name)
            self.assertTrue(60 <= entry["seconds"] <= 7200, name)
            for flag in ("required", "exits_1"):
                self.assertIsInstance(entry[flag], bool, f"{name}: {flag}")
            for text in ("basis", "module", "why"):
                self.assertTrue(entry[text].strip(), f"{name}: {text} is empty")

    def test_every_status_is_known_none_aborts_and_an_exemption_says_why(self):
        for name, entry in ENTRIES.items():
            self.assertIn(entry["status"], STATUSES, name)
            self.assertNotIn("abort", entry["status"].lower(), name)
            if entry["status"] in ("exempt", "partial-red"):
                self.assertGreaterEqual(len(entry["why"]), 30, f"{name}: a {entry['status']} entry needs its reason")

    def test_the_default_for_an_unknown_stage_is_optional_and_a_known_stage_loads_through_the_module(self):
        self.assertEqual(budget.budget_for("not-a-stage"), (DATA["default_seconds"], False))
        for name, entry in ENTRIES.items():
            self.assertEqual(budget.budget_for(name), (entry["seconds"], entry["required"]), name)


class ManifestAgreesWithTheRunnerAndTheWorkflows(unittest.TestCase):
    def test_the_jobs_tick_stages_use_the_ticks_own_budget_and_its_bulk_rule(self):
        for name, step in PLAN.items():
            if name in ENTRIES:
                self.assertEqual((ENTRIES[name]["seconds"], ENTRIES[name]["required"]), (step.budget, not step.bulk), name)

    def test_a_stage_with_a_workflow_step_is_required_exactly_when_a_step_lacks_continue_on_error_and_never_exceeds_its_largest_job_limit(self):
        for name, found in workflow_steps().items():
            if name not in ENTRIES or name in PLAN:
                continue
            self.assertEqual(ENTRIES[name]["required"], not all(flag for _, flag in found), name)
            self.assertLessEqual(ENTRIES[name]["seconds"], max(limit for limit, _ in found), name)

    def test_the_partial_failure_column_matches_the_runner(self):
        for name, entry in ENTRIES.items():
            self.assertEqual(entry["exits_1"], name in run.PARTIAL_FAILURE, f"{name}: 'exits 1 on any failed item' disagrees with lifeos.run.PARTIAL_FAILURE")
            if entry["status"] == "partial-red":
                self.assertIn(name, run.PARTIAL_FAILURE, name)

    def test_a_lazy_stage_names_the_module_the_runner_loads(self):
        for name, entry in ENTRIES.items():
            target = getattr(run.STAGES.get(name), "target", None)
            if target:
                self.assertEqual("lifeos." + entry["module"], target[0], name)


class IsolationIsTrueOfTheCode(unittest.TestCase):
    def test_a_helper_stage_calls_the_helper_and_a_single_unit_stage_has_no_loop_over_external_items(self):
        for name, entry in ENTRIES.items():
            target = getattr(run.STAGES.get(name), "target", None)
            if not target:
                continue
            if entry["status"] == "helper":
                source = pathlib.Path(importlib.util.find_spec(target[0]).origin).read_text()
                self.assertIn("isolate.run_items", source, f"{name} is marked helper but does not call isolate.run_items")
            if entry["status"] == "single":
                self.assertEqual(isolation.loops(target[0]), {}, f"{name} is marked single but its module loops over items that call a client: mark it guarded or helper")


class RenderedFilesAreTheManifest(unittest.TestCase):
    def test_stage_budgets_json_is_exactly_what_the_manifest_renders(self):
        self.assertEqual((ROOT / "lifeos/platform/stage_budgets.json").read_text(), stages.budgets_text(DATA), "run python tools/regen.py")

    def test_the_two_doc_tables_hold_exactly_the_manifests_rows(self):
        for path, heading, wanted in (("docs/platform/timeouts.md", "Stage budgets", stages.timeouts_rows(DATA)), ("docs/platform/stage-isolation.md", "Every stage", stages.isolation_rows(DATA))):
            self.assertEqual(table_rows(path, heading), wanted, f"{path}: run python tools/regen.py")

    def test_the_manifest_file_is_in_the_canonical_form_regen_writes(self):
        self.assertEqual((ROOT / "lifeos/platform/stages.json").read_text(), stages.dump(json.loads((ROOT / "lifeos/platform/stages.json").read_text())))


if __name__ == "__main__":
    unittest.main()
