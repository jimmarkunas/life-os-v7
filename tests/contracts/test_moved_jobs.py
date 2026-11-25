"""LIFE-856 change 4: Network Leads (Step 7c) and the scheduled Cloud Fit backlog (Step 6a2) moved out of hourly.yml's finish job into the after-tick domains run. The workflow facts that keep them safe."""
import pathlib
import unittest

try:
    import yaml
except ImportError:                                                    # pragma: no cover
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"


@unittest.skipIf(yaml is None, "PyYAML not installed")
class MovedJobs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.domains = yaml.safe_load((ROOT / "domains.yml").read_text())
        cls.hourly = yaml.safe_load((ROOT / "hourly.yml").read_text())

    def test_each_runs_only_after_a_tick_that_ran_its_pipeline_and_never_on_the_early_run(self):
        for name in ("network_surface", "fit_cloud_backlog"):
            condition = self.domains["jobs"][name]["if"]
            for needed in ("needs.gate.outputs.late == 'true'", "needs.gate.outputs.tick == 'true'", "needs.gate.outputs.pipeline == 'true'", f"needs.gate.outputs.due_{name} == 'true'", "needs.gate.result != 'failure'"):
                self.assertIn(needed, condition, name)
            self.assertNotIn("inputs.", condition, name)                                                  # no manual trigger, so no second run of the same tick

    def test_the_gate_computes_the_pipeline_output_from_the_ticks_finish_job_and_unknown_counts_as_ran(self):
        gate = self.domains["jobs"]["gate"]
        self.assertIn("steps.ran.outputs.ran != 'false'", gate["outputs"]["pipeline"])
        self.assertIn("workflow_run", gate["outputs"]["pipeline"])
        step = next(s for s in gate["steps"] if s.get("id") == "ran")
        self.assertTrue(step.get("continue-on-error"))
        self.assertEqual(step["run"], "python3 -m lifeos.platform.pipeline_ran")

    def test_a_failure_in_either_reaches_the_phone_through_the_report_and_the_chain_waits_for_them(self):
        for job in ("report", "chain"):
            for name in ("network_surface", "fit_cloud_backlog"):
                self.assertIn(name, self.domains["jobs"][job]["needs"], (job, name))
        text = " ".join(str(s.get("run", "")) for s in self.domains["jobs"]["report"]["steps"])
        for name in ("network_surface", "fit_cloud_backlog"):
            self.assertIn(f"{name}=${{{{ needs.{name}.result }}}}", text)
            steps = self.domains["jobs"][name]["steps"]
            self.assertTrue(any(s.get("continue-on-error") and s.get("id") == name for s in steps), name)
            self.assertIn(f"::error title={name}::", steps[-1]["run"])                                     # the job ends red when its stage failed

    def test_hourly_no_longer_runs_either_after_a_tick_and_keeps_only_the_manual_cloud_backlog(self):
        steps = self.hourly["jobs"]["finish"]["steps"]
        self.assertFalse([s for s in steps if s.get("id") == "network_surface"])
        backlog = next(s for s in steps if s.get("id") == "fit_cloud_backlog")
        self.assertIn("inputs.cloud_backlog != ''", backlog["if"])
        self.assertIn("needs.prep.outputs.pipeline != 'true'", backlog["if"])
        self.assertNotIn("V7_CLOUD_FIT_SCHEDULED", backlog["if"])
        lanes = next(s for s in steps if s.get("id") == "lanes")
        self.assertNotIn("network_surface", lanes["run"])
        self.assertIn("fit_cloud_backlog", lanes["run"])                                                  # the manual dispatch is still a named lane

    def test_the_cloud_fit_batch_keeps_its_switch_and_its_55_minute_end(self):
        step = next(s for s in self.domains["jobs"]["fit_cloud_backlog"]["steps"] if s.get("id") == "fit_cloud_backlog")
        self.assertIn("env.V7_CLOUD_FIT_SCHEDULED && env.V7_CLOUD_FIT_SCHEDULED != '0'", step["if"])
        self.assertIn("to_55", step["run"])
        self.assertIn("58 * 60", step["run"])
        self.assertEqual(self.domains["jobs"]["fit_cloud_backlog"]["timeout-minutes"], 58)
        self.assertEqual(step["env"]["V7_CLOUD_FIT_BACKLOG_PAGE"], "")                                    # a tick never narrows to a pilot page
