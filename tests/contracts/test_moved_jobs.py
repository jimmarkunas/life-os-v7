"""The workflow facts that keep them safe."""

import pathlib
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover
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
            for needed in (
                "needs.gate.outputs.late == 'true'",
                "needs.gate.outputs.tick == 'true'",
                "needs.gate.outputs.pipeline == 'true'",
                f"needs.gate.outputs.due_{name} == 'true'",
                "needs.gate.result != 'failure'",
            ):
                self.assertIn(needed, condition, name)
            self.assertNotIn("inputs.", condition, name)

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
            self.assertIn(f"::error title={name}::", steps[-1]["run"])  # the job ends red when its stage failed

    def test_hourly_no_longer_runs_either_after_a_tick_and_keeps_only_the_manual_cloud_backlog(self):
        steps = self.hourly["jobs"]["finish"]["steps"]
        self.assertFalse([s for s in steps if s.get("id") == "network_surface"])
        backlog = next(s for s in steps if s.get("id") == "fit_cloud_backlog")
        self.assertIn("inputs.cloud_backlog != ''", backlog["if"])
        self.assertIn("needs.prep.outputs.pipeline != 'true'", backlog["if"])
        self.assertNotIn("V7_CLOUD_FIT_SCHEDULED", backlog["if"])
        lanes = next(s for s in steps if s.get("id") == "lanes")
        self.assertNotIn("network_surface", lanes["run"])
        self.assertIn("fit_cloud_backlog", lanes["run"])  # the manual dispatch is still a named lane

    def test_the_cloud_fit_batch_keeps_its_switch_and_its_55_minute_end(self):
        step = next(s for s in self.domains["jobs"]["fit_cloud_backlog"]["steps"] if s.get("id") == "fit_cloud_backlog")
        self.assertIn("env.V7_CLOUD_FIT_SCHEDULED && env.V7_CLOUD_FIT_SCHEDULED != '0'", step["if"])
        self.assertIn("to_55", step["run"])
        self.assertIn("58 * 60", step["run"])
        self.assertEqual(self.domains["jobs"]["fit_cloud_backlog"]["timeout-minutes"], 58)
        self.assertEqual(step["env"]["V7_CLOUD_FIT_BACKLOG_PAGE"], "")  # a tick never narrows to a pilot page


class MovedJobsKeepTheEnvironmentTheirStageReads(unittest.TestCase):
    """both jobs failed with CONFIG_MISSING at once (run 45726238990)."""

    WORKFLOW_LEVEL = {
        "HIRING_PIPELINE_PAGE_ID",
        "NOTION_JOB_LEDGER_DATA_SOURCE_ID",
        "V7_FIT_GATE",
    }  # hourly.yml's own `env:` before change 4
    OLD = {
        "network_surface": {
            "FIT_PROFILE_EXTRA_JSON",
            "FIT_PROFILE_JSON",
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "JOBRIGHT_EMAIL",
            "JOBRIGHT_PASSWORD",
            "LIFEOS_ACQ_DB_NAME",
            "LIFEOS_ACQ_DB_PASSWORD",
            "LIFEOS_ACQ_DB_USER",
            "LIFEOS_ACQ_SSH_HOST",
            "LIFEOS_ACQ_SSH_JUMP_HOST",
            "LIFEOS_ACQ_SSH_JUMP_KEY",
            "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS",
            "LIFEOS_ACQ_SSH_KNOWN_HOSTS",
            "LIFEOS_ACQ_SSH_PRIVATE_KEY",
            "LIFEOS_ACQ_SSH_USER",
            "NETWORK_SURFACE_SKIP",
            "NOTION_API_TOKEN",
            "OPEN_JOBS_CONTACT",
            "TINYFISH_API_KEY",
            "V7_CLOSE_BASE_URL",
            "V7_RUNTIME_CONFIG_SOURCE_ID",
        },
        "fit_cloud_backlog": {
            "CLOUD_BACKLOG",
            "FIT_PROFILE_EXTRA_JSON",
            "FIT_PROFILE_JSON",
            "IMESSAGE_ALERTS_HOST",
            "LIFEOS_ACQ_DB_NAME",
            "LIFEOS_ACQ_DB_PASSWORD",
            "LIFEOS_ACQ_DB_USER",
            "LIFEOS_ACQ_SSH_HOST",
            "LIFEOS_ACQ_SSH_JUMP_HOST",
            "LIFEOS_ACQ_SSH_JUMP_KEY",
            "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS",
            "LIFEOS_ACQ_SSH_KNOWN_HOSTS",
            "LIFEOS_ACQ_SSH_PRIVATE_KEY",
            "LIFEOS_ACQ_SSH_USER",
            "NOTION_API_TOKEN",
            "V7_CLOUD_FIT_BACKLOG_BUDGET",
            "V7_CLOUD_FIT_BACKLOG_MAX",
            "V7_CLOUD_FIT_BACKLOG_PAGE",
            "V7_CLOUD_FIT_PARALLEL",
            "V7_CLOUD_FIT_PROFILE_REF",
            "V7_RUNTIME_CONFIG_SOURCE_ID",
        },
        "sync_seen": {
            "FIT_PROFILE_EXTRA_JSON",
            "FIT_PROFILE_JSON",
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "JOBRIGHT_EMAIL",
            "JOBRIGHT_PASSWORD",
            "LIFEOS_ACQ_DB_NAME",
            "LIFEOS_ACQ_DB_PASSWORD",
            "LIFEOS_ACQ_DB_USER",
            "LIFEOS_ACQ_SSH_HOST",
            "LIFEOS_ACQ_SSH_JUMP_HOST",
            "LIFEOS_ACQ_SSH_JUMP_KEY",
            "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS",
            "LIFEOS_ACQ_SSH_KNOWN_HOSTS",
            "LIFEOS_ACQ_SSH_PRIVATE_KEY",
            "LIFEOS_ACQ_SSH_USER",
            "NOTION_API_TOKEN",
            "OPEN_JOBS_CONTACT",
            "TINYFISH_API_KEY",
            "V7_RUNTIME_CONFIG_SOURCE_ID",
        },
        "purge": {
            "FIT_PROFILE_EXTRA_JSON",
            "FIT_PROFILE_JSON",
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "JOBRIGHT_EMAIL",
            "JOBRIGHT_PASSWORD",
            "LIFEOS_ACQ_DB_NAME",
            "LIFEOS_ACQ_DB_PASSWORD",
            "LIFEOS_ACQ_DB_USER",
            "LIFEOS_ACQ_SSH_HOST",
            "LIFEOS_ACQ_SSH_JUMP_HOST",
            "LIFEOS_ACQ_SSH_JUMP_KEY",
            "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS",
            "LIFEOS_ACQ_SSH_KNOWN_HOSTS",
            "LIFEOS_ACQ_SSH_PRIVATE_KEY",
            "LIFEOS_ACQ_SSH_USER",
            "NOTION_API_TOKEN",
            "NTFY_TOPIC",
            "OPEN_JOBS_CONTACT",
            "PURGE_FORCE",
            "TINYFISH_API_KEY",
            "V7_RUNTIME_CONFIG_SOURCE_ID",
        },
    }
    JOB = {
        "network_surface": "network_surface",
        "fit_cloud_backlog": "fit_cloud_backlog",
        "sync_seen": "last_seen_purge",
        "purge": "last_seen_purge",
    }
    DROPPED = {
        "network_surface": {
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "OPEN_JOBS_CONTACT",
        },
        "fit_cloud_backlog": set(),
        "sync_seen": {
            "FIT_PROFILE_EXTRA_JSON",
            "FIT_PROFILE_JSON",
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "JOBRIGHT_EMAIL",
            "JOBRIGHT_PASSWORD",
            "OPEN_JOBS_CONTACT",
            "TINYFISH_API_KEY",
        },
        "purge": {
            "FIT_PROFILE_EXTRA_JSON",
            "FIT_PROFILE_JSON",
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "JOBRIGHT_EMAIL",
            "JOBRIGHT_PASSWORD",
            "OPEN_JOBS_CONTACT",
            "TINYFISH_API_KEY",
        },
    }
    MODULES = {
        "network_surface": ["lifeos/sources/network/network_surface.py"],
        "sync_seen": [
            "lifeos/jobs/repost.py",
            "lifeos/jobs/store.py",
            "lifeos/jobs/ledger.py",
            "lifeos/platform/notion_client.py",
            "lifeos/platform/db.py",
        ],
        "purge": [
            "lifeos/jobs/retention.py",
            "lifeos/jobs/hiring_pipeline.py",
            "lifeos/jobs/lanes.py",
            "lifeos/jobs/progression.py",
            "lifeos/jobs/tombstone.py",
            "lifeos/jobs/ledger.py",
            "lifeos/jobs/store.py",
            "lifeos/platform/alerts.py",
            "lifeos/platform/db.py",
        ],
    }

    @classmethod
    def setUpClass(cls):
        cls.domains = yaml.safe_load((ROOT / "domains.yml").read_text())

    def step(self, stage):
        return next(s for s in self.domains["jobs"][self.JOB[stage]]["steps"] if s.get("id") == stage)

    def test_every_moved_step_declares_every_name_its_old_step_had_and_the_three_workflow_level_names(self):
        for stage in self.OLD:
            declared = set(self.step(stage).get("env") or {})
            self.assertEqual(sorted((self.OLD[stage] - self.DROPPED[stage]) - declared), [], stage)
            self.assertEqual(
                sorted(self.WORKFLOW_LEVEL - declared), [], stage
            )  # the omission that failed run 45726238990

    def test_a_dropped_name_is_never_read_by_the_stage(self):
        for stage, names in self.DROPPED.items():
            text = " ".join((ROOT.parents[1] / path).read_text() for path in self.MODULES.get(stage, []))
            self.assertEqual(sorted(n for n in names if n in text), [], stage)

    def test_the_last_seen_and_purge_job_runs_after_a_tick_that_ran_its_pipeline_only_and_keeps_both_steps_red_on_failure(
        self,
    ):
        job = self.domains["jobs"]["last_seen_purge"]
        for needed in (
            "needs.gate.outputs.late == 'true'",
            "needs.gate.outputs.tick == 'true'",
            "needs.gate.outputs.pipeline == 'true'",
            "needs.gate.outputs.due_last_seen_purge == 'true'",
            "needs.gate.result != 'failure'",
        ):
            self.assertIn(needed, job["if"])
        self.assertNotIn("inputs.", job["if"])
        ids = [s.get("id") for s in job["steps"] if s.get("id") in ("sync_seen", "purge")]
        self.assertEqual(ids, ["sync_seen", "purge"])
        for stage in ("sync_seen", "purge"):
            self.assertTrue(self.step(stage).get("continue-on-error"))
        self.assertIn("::error title=last_seen_purge::", job["steps"][-1]["run"])
        purge = self.step("purge")["run"]
        for line in ("purge_done", "purge_failed", "exit 1", "not_due"):
            self.assertIn(line, purge)  # the purge keeps its own pushes and exit code
        for name in ("report", "chain"):
            self.assertIn("last_seen_purge", self.domains["jobs"][name]["needs"])
        self.assertIn(
            "last_seen_purge=${{ needs.last_seen_purge.result }}",
            " ".join(str(s.get("run", "")) for s in self.domains["jobs"]["report"]["steps"]),
        )

    def test_hourly_keeps_the_two_steps_for_a_manual_maintenance_dispatch_only(self):
        hourly = yaml.safe_load((ROOT / "hourly.yml").read_text())
        steps = {s.get("id"): s for s in hourly["jobs"]["finish"]["steps"]}
        for stage in ("sync_seen", "purge"):
            condition = steps[stage]["if"]
            self.assertIn("inputs.maintenance", condition)
            self.assertIn("needs.prep.outputs.pipeline != 'true'", condition)
            self.assertNotIn("pipeline == 'true'", condition)
        for ident in ("alerts", "lanes"):
            self.assertIn(ident, steps)  # they report the finish lanes and stay
        self.assertIn("sync_seen=${{ steps.sync_seen.outcome }}", steps["lanes"]["run"])
