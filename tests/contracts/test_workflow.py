import pathlib
import unittest

try:
    import yaml
except ImportError:          # PyYAML is a dev convenience, not a runtime dependency
    yaml = None


@unittest.skipIf(yaml is None, "PyYAML not installed")
class WorkflowTests(unittest.TestCase):
    def test_every_workflow_file_parses_and_every_manual_one_declares_its_dispatch(self):
        """A workflow that is not valid YAML is silently not runnable (safety.yml shipped that way once)."""
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        for path in sorted(root.glob("*.y*ml")):
            data = yaml.safe_load(path.read_text())
            self.assertIn("jobs", data, path.name)
            self.assertTrue("on" in data or True in data, path.name)

    def test_safety_workflow_is_weekly_and_manual_with_its_stages(self):
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        data = yaml.safe_load((root / "safety.yml").read_text())
        self.assertEqual(sorted(data[True]), ["schedule", "workflow_dispatch"])         # Jim's weekly check (D142); a scheduled run has no input, so the stage defaults to all
        self.assertEqual(data[True]["schedule"], [{"cron": "17 13 * * 1"}])
        self.assertIn("|| 'all'", yaml.safe_load((root / "safety.yml").read_text())["jobs"]["safety"]["env"]["STAGE"])
        self.assertEqual(data[True]["workflow_dispatch"]["inputs"]["stage"]["options"], ["all", "sql-smoke", "fit-golden", "fit-capture"])

    def test_hourly_workflow_is_valid_yaml_with_dispatch_inputs(self):
        path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hourly.yml"
        data = yaml.safe_load(path.read_text())
        self.assertIn("workflow_dispatch", data[True])
        self.assertIn("live", data[True]["workflow_dispatch"]["inputs"])

    def test_dice_resolver_is_pipeline_only_and_adds_no_dispatch_input(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hourly.yml"
        data = yaml.safe_load(path.read_text())
        self.assertNotIn("resolve_dice", data[True]["workflow_dispatch"]["inputs"])
        job = data["jobs"]["dice"]
        step = next(step for step in job["steps"] if step.get("id") == "dice")
        self.assertIn("PIPELINE", step["if"])
        self.assertNotIn("inputs.", step["if"])

    def test_domain_jobs_left_hourly_and_run_after_a_tick_from_domains(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / ".github/workflows"
        hourly = yaml.safe_load((root / "hourly.yml").read_text())
        domains = yaml.safe_load((root / "domains.yml").read_text())
        for name in ("jira", "outlook", "bills", "agenda", "amazon"):
            self.assertNotIn(name, hourly["jobs"], name)
            self.assertIn("github.event_name == 'workflow_run'", domains["jobs"][name]["if"], name)
        self.assertNotIn("jira", hourly[True]["workflow_dispatch"]["inputs"])
        self.assertEqual(domains[True]["workflow_run"]["workflows"], ["hourly"])
        self.assertIn("completed", domains[True]["workflow_run"]["types"])
        self.assertIn("!= 'cancelled'", domains["jobs"]["agenda"]["if"])               # a tick the gate cancelled starts nothing
        self.assertIn("display_title == 'tick'", domains["jobs"]["agenda"]["if"])      # nor does a manual hourly run
        self.assertEqual(domains["jobs"]["agenda"]["needs"], "jira")                   # both write the Daily Report page: never at once
        self.assertIn("always()", domains["jobs"]["agenda"]["if"])                     # but a failed Jira job does not stop the Calendar card

    def test_bills_paid_runs_on_a_tick_before_the_snapshot_and_follows_the_live_flag(self):
        from pathlib import Path
        domains = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/domains.yml").read_text())
        steps = domains["jobs"]["bills"]["steps"]
        ids = [s.get("id") for s in steps]
        self.assertLess(ids.index("bpaid"), ids.index("bsnap"))                        # the snapshot sees the advanced dates
        step = steps[ids.index("bpaid")]
        self.assertIn("workflow_run", step["if"])
        self.assertIn("inputs.bills_paid", step["if"])
        self.assertIn('--live', step["run"])                                           # live only when LIVE (V7_LIVE on a tick, Actually write by hand)
        self.assertIs(domains[True]["workflow_dispatch"]["inputs"]["bills_paid"]["default"], False)

    def test_the_manual_amazon_orders_pass_queues_with_the_domain_jobs_and_maintenance_does_not(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        amazon = yaml.safe_load((root / ".github/workflows/amazon.yml").read_text())
        domains = yaml.safe_load((root / ".github/workflows/domains.yml").read_text())
        group = amazon["concurrency"]["group"]
        self.assertIn("inputs.stage == 'orders'", group)
        self.assertIn(f"'{domains['concurrency']['group']}'", group)                       # orders: the very group the domain jobs hold
        self.assertIn("'life-os-v7-amazon-orders'", group)                                  # backfill, retention, review-reset: their own
        self.assertIs(amazon["concurrency"]["cancel-in-progress"], False)
        self.assertEqual(group, "${{ inputs.stage == 'orders' && 'life-os-v7-domains' || 'life-os-v7-amazon-orders' }}")      # EDGE-1.3 left the queueing exactly as it was
        self.assertEqual(amazon["jobs"]["amazon"]["timeout-minutes"], 330)
        self.assertNotIn("schedule", amazon[True])

    def test_amazon_manual_workflow_is_dry_by_default_and_adds_no_hourly_input(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        hourly = yaml.safe_load((root / ".github/workflows/hourly.yml").read_text())
        manual = yaml.safe_load((root / ".github/workflows/amazon.yml").read_text())
        self.assertNotIn("amazon", hourly[True]["workflow_dispatch"]["inputs"])
        self.assertIs(manual[True]["workflow_dispatch"]["inputs"]["live"]["default"], False)

    def test_amazon_reads_its_three_migrated_credentials_from_bitwarden_through_the_pinned_action(self):
        """EDGE-1.3 Phase B: exactly the Gmail client secret, Gmail refresh token and Notion Amazon token come from Bitwarden; the client id and data-source id stay GitHub secrets."""
        import re
        from pathlib import Path
        text = (Path(__file__).resolve().parents[2] / ".github/workflows/amazon.yml").read_text()
        manual = yaml.safe_load(text)
        inputs = manual[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(list(manual[True]), ["workflow_dispatch"])                                       # manual only: no schedule, push or other trigger
        self.assertEqual(inputs["stage"]["options"], ["orders", "backfill", "retention", "review-reset", "census"])
        self.assertEqual((inputs["since"]["default"], inputs["live"]["default"]), ("2015-01-01", False))   # dry run stays the default
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", text))), ["GMAIL_OAUTH_CLIENT_ID", "LIFEOS_BWS_RUNTIME_TOKEN", "NOTION_AMAZON_DATA_SOURCE_ID"])
        for moved in ("GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_AMAZON_TOKEN"):
            self.assertNotIn(f"secrets.{moved}", text)                                                    # no direct copy, so no fallback
        self.assertEqual(re.findall(r"uses:\s*(bitwarden/\S+)", text), ["bitwarden/sm-action@1238aae8fc64b212641190a9227c8a734ab1a793"])   # the reviewed v3.0.1 commit
        mappings = re.findall(r"^\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s*>\s*\w+)\s*$", text, re.M)
        self.assertEqual(sorted(mappings), sorted(["08245c07-728f-438e-940b-b4db015a6967 > GMAIL_OAUTH_CLIENT_SECRET", "672a44e9-235f-4121-a99d-b4db015a6987 > GMAIL_OAUTH_REFRESH_TOKEN",
                                                   "46fe195b-82e2-416e-8272-b4db015eea12 > NOTION_AMAZON_TOKEN"]))
        self.assertEqual(len(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)), 3)
        self.assertLess(text.index("bitwarden/sm-action"), text.index("python -m lifeos.run"))             # loaded before the Amazon command
        self.assertIn("--live", text)                                                                      # the existing live switch is still the only way to write

    def test_amazon_since_reaches_only_the_backfill_stage_so_orders_keeps_its_normal_watermark_window(self):
        """Live run 37541352965: the 2015-01-01 default of the backfill-only `since` input reached stage=orders (AMAZON_SINCE overrides the watermark in lifeos/amazon/stage.py), so the
        normal pass listed the whole mailbox and stopped at the 200-message limit."""
        import re
        from pathlib import Path
        text = (Path(__file__).resolve().parents[2] / ".github/workflows/amazon.yml").read_text()
        step_env = yaml.safe_load(text)["jobs"]["amazon"]["steps"][-1]["env"]
        expression = step_env["AMAZON_SINCE"]
        self.assertEqual(expression, "${{ inputs.stage == 'backfill' && inputs.since || '' }}")
        self.assertEqual(len(re.findall(r"inputs\.since", text)), 1)                                       # the only use of the input
        stage_choices = yaml.safe_load(text)[True]["workflow_dispatch"]["inputs"]["stage"]["options"]
        gate = re.fullmatch(r"\$\{\{ inputs\.stage == '([a-z-]+)' && inputs\.since \|\| '' \}\}", expression).group(1)
        default_since = yaml.safe_load(text)[True]["workflow_dispatch"]["inputs"]["since"]["default"]
        passed = {stage: (default_since if stage == gate else "") for stage in stage_choices}                  # what each stage receives when the operator leaves `since` at its default
        self.assertEqual({stage for stage, value in passed.items() if value}, {"backfill"})
        self.assertEqual(passed["orders"], "")                                                              # the normal pass never receives the 2015-01-01 default
        self.assertEqual(yaml.safe_load(text)[True]["workflow_dispatch"]["inputs"]["since"]["default"], "2015-01-01")   # the backfill default is unchanged


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(yaml is None, "PyYAML not installed")
class IsolatedJobTests(unittest.TestCase):
    """Isolated OS jobs blank every workflow-level secret unless a step redeclares only what it needs.
    must be blanked in each of them or this fails (the isolation pattern is declared three times, so it is checked, not trusted)."""

    def test_no_workflow_level_secret_reaches_an_isolated_job(self):
        import yaml
        from pathlib import Path
        doc = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/hourly.yml").read_text())
        shared = {k: v for k, v in (doc.get("env") or {}).items() if "secrets." in str(v)}
        self.assertGreater(len(shared), 10)
        needs = {"interview": {"HIRING_PIPELINE_PAGE_ID"}}                  # the Interview job reads the Hiring Pipeline page by id
        for name in ("interview",):
            env = doc["jobs"][name].get("env") or {}
            leaked = sorted(k for k in shared if k not in needs.get(name, ()) and env.get(k) not in ("",))
            self.assertEqual(leaked, [], f"{name} inherits {leaked}")

    def test_python_setup_is_declared_once(self):
        from pathlib import Path
        for path in (Path(__file__).resolve().parents[2] / ".github/workflows").glob("*.yml"):
            self.assertNotIn("setup-python", path.read_text(), path.name)


DB = {"LIFEOS_ACQ_DB_NAME", "LIFEOS_ACQ_DB_PASSWORD", "LIFEOS_ACQ_DB_USER", "LIFEOS_ACQ_SSH_HOST", "LIFEOS_ACQ_SSH_KNOWN_HOSTS",
      "LIFEOS_ACQ_SSH_PORT", "LIFEOS_ACQ_SSH_PRIVATE_KEY", "LIFEOS_ACQ_SSH_USER"}
DOMAIN_SECRETS = {         # the only secrets each domain job may see; adding one here is a conscious decision
    "jira": DB | {"JIRA_API_TOKEN", "JIRA_BASE_URL", "JIRA_BOARDS", "JIRA_CARD_BLOCK_ID", "JIRA_CARD_PROJECTS", "JIRA_EMAIL",
                  "JIRA_GTV_CONTEXT_URL", "JIRA_GTV_EPIC", "JIRA_SITE_URL", "NOTION_JIRA_TOKEN"},
    "outlook": DB | {"GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "OUTLOOK_CLIENT_ID"},
    "bills": DB | {"BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_BILLS_DATA_SOURCE_ID", "NOTION_BILLS_TOKEN", "NOTION_JIRA_TOKEN"},
    "agenda": DB | {"CALENDAR_CARD_BLOCK_ID", "GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN"},
    "attention": DB | {"AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC", "OUTLOOK_CLIENT_ID"},
    "megibow": DB | {"GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "MEGIBOW_BLOCK_ID", "MEGIBOW_EXCLUDED_DOMAINS", "MEGIBOW_REVIEW_DB_ID", "NOTION_JIRA_TOKEN", "NTFY_TOPIC", "OUTLOOK_CLIENT_ID"},
    "mail": DB | {"AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET",
                  "GMAIL_OAUTH_REFRESH_TOKEN", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "MAIL_ALERTS_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"},
    "hiring": DB | {"GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "HIRING_CARD_BLOCK_ID", "HIRING_PIPELINE_PAGE_ID", "HIRING_STATUS_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_API_TOKEN",
                    "NOTION_INTERVIEW_TOKEN", "NOTION_JIRA_TOKEN", "NOTION_JOB_LEDGER_DATA_SOURCE_ID"},
    "amazon": {"AMAZON_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET",
               "GMAIL_OAUTH_REFRESH_TOKEN", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_AMAZON_DATA_SOURCE_ID", "NOTION_AMAZON_TOKEN", "NOTION_JIRA_TOKEN"},
}


@unittest.skipIf(yaml is None, "PyYAML not installed")
class DomainsWorkflowTests(unittest.TestCase):
    """domains.yml holds the single-provider jobs. It declares no secrets at the top, so isolation is structural: a job sees only what its own
    steps pass it, and never the Jobs pipeline's secrets."""

    @classmethod
    def setUpClass(cls):
        path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "domains.yml"
        cls.doc = yaml.safe_load(path.read_text())

    def test_no_secret_is_declared_at_the_top(self):
        self.assertEqual([k for k, v in (self.doc.get("env") or {}).items() if "secrets." in str(v)], [])

    def test_each_job_sees_only_its_own_secrets(self):
        self.assertEqual(set(self.doc["jobs"]), set(DOMAIN_SECRETS))
        for name, job in self.doc["jobs"].items():
            used = {k for k, v in (job.get("env") or {}).items() if "secrets." in str(v)}
            for step in job["steps"]:
                used |= {k for k, v in (step.get("env") or {}).items() if "secrets." in str(v)}
            self.assertLessEqual(used, DOMAIN_SECRETS[name], f"{name}: {sorted(used - DOMAIN_SECRETS[name])}")

    def test_a_failing_domain_step_is_a_warning_never_a_failed_run(self):
        for name, job in self.doc["jobs"].items():
            work = [s for s in job["steps"] if s.get("id")]
            self.assertTrue(work and all(s.get("continue-on-error") for s in work), name)
