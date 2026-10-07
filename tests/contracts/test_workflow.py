import hashlib
import pathlib
import re
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

    def test_diag_reads_its_seven_database_and_ssh_values_from_bitwarden_through_the_pinned_action(self):
        """EDGE-1.3 Phase B: the read-only diagnostic keeps its manual contract; only the SSH port is still a GitHub secret besides the Bitwarden bootstrap token."""
        import re
        from pathlib import Path
        text = (Path(__file__).resolve().parents[2] / ".github/workflows/diag.yml").read_text()
        diag = yaml.safe_load(text)
        moved = {"LIFEOS_ACQ_DB_NAME": "d71bbaed-6a6c-49b5-8a1f-b4db015ecfda", "LIFEOS_ACQ_DB_PASSWORD": "00d82e0d-be10-4c8c-bdbe-b4db015ed389", "LIFEOS_ACQ_DB_USER": "9422b842-3bb0-4153-bb8d-b4db015ed746",
                 "LIFEOS_ACQ_SSH_HOST": "6f1c86f6-c48f-4e77-b65d-b4db015edb15", "LIFEOS_ACQ_SSH_KNOWN_HOSTS": "b65dd2fc-2573-4bda-9e80-b4db015edec6",
                 "LIFEOS_ACQ_SSH_PRIVATE_KEY": "7ada9cea-8711-4b37-bef9-b4db015ee298", "LIFEOS_ACQ_SSH_USER": "6b3e12b9-1987-4073-bda2-b4db015ee653"}
        stage = diag[True]["workflow_dispatch"]["inputs"]["stage"]
        job = diag["jobs"]["diag"]
        self.assertEqual(list(diag[True]), ["workflow_dispatch"])                                           # manual only: no schedule, workflow_run, push or repository_dispatch
        self.assertNotRegex(text, r"(?m)^\s*(schedule|workflow_run|repository_dispatch|push|pull_request\w*):")
        self.assertEqual((stage["options"], stage["default"]), (["scale-up-holds", "scale-up-links", "alerts-open"], "scale-up-holds"))   # alerts-open arrived on main in #216 (read-only diagnostic)
        self.assertEqual(diag["permissions"], {"contents": "read"})
        self.assertEqual(diag["concurrency"], {"group": "life-os-v7-diag", "cancel-in-progress": False})
        self.assertEqual(job["timeout-minutes"], 10)
        self.assertEqual(job["steps"][-1]["run"], 'python -m lifeos.run "$STAGE"')
        self.assertEqual(job["steps"][-1]["env"], {"LIFEOS_ACQ_SSH_PORT": "${{ secrets.LIFEOS_ACQ_SSH_PORT }}", "STAGE": "${{ inputs.stage }}"})
        self.assertNotIn("--live", text)
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", text))), ["LIFEOS_ACQ_SSH_PORT", "LIFEOS_BWS_RUNTIME_TOKEN"])
        for name in moved:
            self.assertNotIn(f"secrets.{name}", text)                                                      # no direct copy, so no fallback
        self.assertEqual(re.findall(r"uses:\s*(bitwarden/\S+)", text), ["bitwarden/sm-action@1238aae8fc64b212641190a9227c8a734ab1a793"])   # the reviewed v3.0.1 commit
        mappings = re.findall(r"^\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s*>\s*\w+)\s*$", text, re.M)
        self.assertEqual(sorted(mappings), sorted(f"{uuid} > {name}" for name, uuid in moved.items()))
        self.assertEqual(len(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)), 7)
        self.assertLess(text.index("bitwarden/sm-action"), text.index("python -m lifeos.run"))             # loaded before the diagnostic command


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
        self.assertEqual(sorted(shared), ["GMAIL_OAUTH_CLIENT_ID", "HIRING_PIPELINE_PAGE_ID", "LIFEOS_ACQ_SSH_PORT", "NOTION_JOB_LEDGER_DATA_SOURCE_ID"])   # EDGE-1.3: the migrated values left the workflow level (each job loads its own)
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


DB = {f"LIFEOS_ACQ_{n}" for n in ("DB_NAME", "DB_PASSWORD", "DB_USER", "SSH_HOST", "SSH_KNOWN_HOSTS", "SSH_PRIVATE_KEY", "SSH_USER")}
HRL = {"FIT_PROFILE_EXTRA_JSON", "FIT_PROFILE_JSON", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "JOBRIGHT_EMAIL", "JOBRIGHT_PASSWORD", "NOTION_API_TOKEN", "OPEN_JOBS_CONTACT", "TINYFISH_API_KEY"}
# The 25 values verified value-for-value in Bitwarden by migration run 37532718917 (name -> secret id). Ids are not credentials.
UUIDS = {
    "FIT_PROFILE_EXTRA_JSON": "3e264d79-3af4-4f2a-a86a-b4db015a68de",
    "FIT_PROFILE_JSON": "31a07198-aa35-450f-913c-b4db015a690d",
    "GCAL_SERVICE_ACCOUNT_JSON": "e697c607-f798-4528-9967-b4db015a6940",
    "GMAIL_OAUTH_CLIENT_SECRET": "08245c07-728f-438e-940b-b4db015a6967",
    "GMAIL_OAUTH_REFRESH_TOKEN": "672a44e9-235f-4121-a99d-b4db015a6987",
    "JIRA_API_TOKEN": "3fa6f4f4-bd6d-45e7-8f2c-b4db015ec0c3",
    "JIRA_EMAIL": "e6466367-9a55-4899-ae1c-b4db015ec49f",
    "JOBRIGHT_EMAIL": "f207f236-5ede-43c1-bd45-b4db015ec857",
    "JOBRIGHT_PASSWORD": "187ac5b0-200b-4800-973f-b4db015ecc0a",
    "LIFEOS_ACQ_DB_NAME": "d71bbaed-6a6c-49b5-8a1f-b4db015ecfda",
    "LIFEOS_ACQ_DB_PASSWORD": "00d82e0d-be10-4c8c-bdbe-b4db015ed389",
    "LIFEOS_ACQ_DB_USER": "9422b842-3bb0-4153-bb8d-b4db015ed746",
    "LIFEOS_ACQ_SSH_HOST": "6f1c86f6-c48f-4e77-b65d-b4db015edb15",
    "LIFEOS_ACQ_SSH_KNOWN_HOSTS": "b65dd2fc-2573-4bda-9e80-b4db015edec6",
    "LIFEOS_ACQ_SSH_PRIVATE_KEY": "7ada9cea-8711-4b37-bef9-b4db015ee298",
    "LIFEOS_ACQ_SSH_USER": "6b3e12b9-1987-4073-bda2-b4db015ee653",
    "NOTION_AMAZON_TOKEN": "46fe195b-82e2-416e-8272-b4db015eea12",
    "NOTION_API_TOKEN": "53dc3d04-dbb2-45fe-8185-b4db015eedcb",
    "NOTION_BILLS_TOKEN": "815268ba-fa49-4461-afb3-b4db015ef189",
    "NOTION_INTERVIEW_TOKEN": "092b44ae-32cb-453b-8cc2-b4db015ef571",
    "NOTION_JIRA_TOKEN": "53f47cb9-f6bb-4cde-87f4-b4db015ef928",
    "NOTION_RECRUITERS_TOKEN": "b7b921fa-3f62-478a-a4f5-b4db015efcf7",
    "NTFY_TOPIC": "83c553e9-23a6-45aa-9006-b4db015f00ac",
    "OPEN_JOBS_CONTACT": "7b62e076-8a46-4228-9977-b4db015f0463",
    "TINYFISH_API_KEY": "f45f29e2-acbc-4be7-9749-b4db015f0817",
}
# Every V7 workflow that consumes a migrated value reads it through its own pinned Bitwarden loader step (EDGE-1.3). Per workflow: the loader's names per job, the remaining direct
# GitHub secret refs, and the unchanged trigger/permission/concurrency/timeout facts plus a fingerprint of every run command (so a credential-source change cannot alter behaviour).
CONSUMERS = {
    "agenda": dict(
        jobs={"agenda": DB | {"GCAL_SERVICE_ACCOUNT_JSON", "NOTION_JIRA_TOKEN"}},
        direct=['CALENDAR_CARD_BLOCK_ID', 'GCAL_CALENDAR_ID', 'JIRA_CARD_BLOCK_ID', 'LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-agenda', 'cancel-in-progress': False},
        timeouts={'agenda': 15}, digest="608cf99f1ebfe13d"),
    "bills": dict(
        jobs={"bills": DB | {"NOTION_BILLS_TOKEN"}},
        direct=['LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'NOTION_BILLS_DATA_SOURCE_ID'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-bills', 'cancel-in-progress': False},
        timeouts={'bills': 15}, digest="978e9cf2d9bfba82"),
    "calendar": dict(
        jobs={"calendar": DB | {"GCAL_SERVICE_ACCOUNT_JSON"}},
        direct=['GCAL_CALENDAR_ID', 'LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'OUTLOOK_CLIENT_ID'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-calendar', 'cancel-in-progress': False},
        timeouts={'calendar': 15}, digest="58ec7d506d5fd3d5"),
    "domains": dict(
        jobs={"jira": DB | {"JIRA_API_TOKEN", "JIRA_EMAIL", "NOTION_JIRA_TOKEN"}, "outlook": DB | {"GCAL_SERVICE_ACCOUNT_JSON"}, "bills": DB | {"NOTION_BILLS_TOKEN", "NOTION_JIRA_TOKEN"}, "agenda": DB | {"GCAL_SERVICE_ACCOUNT_JSON", "NOTION_JIRA_TOKEN"}, "amazon": {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_AMAZON_TOKEN", "NOTION_JIRA_TOKEN"}, "mail": DB | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"}, "hiring": DB | {"GCAL_SERVICE_ACCOUNT_JSON", "NOTION_API_TOKEN", "NOTION_INTERVIEW_TOKEN", "NOTION_JIRA_TOKEN"}, "attention": DB | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"}, "megibow": DB | {"GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"}},
        direct=['AMAZON_CARD_BLOCK_ID', 'ATTENTION_CARD_BLOCK_ID', 'BILLS_CARD_BLOCK_ID', 'CALENDAR_CARD_BLOCK_ID', 'GCAL_CALENDAR_ID', 'GMAIL_OAUTH_CLIENT_ID', 'HIRING_CARD_BLOCK_ID', 'HIRING_PIPELINE_PAGE_ID', 'HIRING_STATUS_BLOCK_ID', 'HIRING_TABLE_BLOCK_ID', 'JIRA_BASE_URL', 'JIRA_BOARDS', 'JIRA_CARD_BLOCK_ID', 'JIRA_CARD_PROJECTS', 'JIRA_GTV_CONTEXT_URL', 'JIRA_GTV_EPIC', 'JIRA_SITE_URL', 'LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'MAIL_ALERTS_CARD_BLOCK_ID', 'MEGIBOW_BLOCK_ID', 'MEGIBOW_EXCLUDED_DOMAINS', 'MEGIBOW_REVIEW_DB_ID', 'NOTION_AMAZON_DATA_SOURCE_ID', 'NOTION_BILLS_DATA_SOURCE_ID', 'NOTION_JOB_LEDGER_DATA_SOURCE_ID', 'OUTLOOK_CLIENT_ID'],
        on=['workflow_dispatch', 'workflow_run'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-domains', 'cancel-in-progress': False},
        timeouts={'jira': 15, 'outlook': 10, 'bills': 15, 'agenda': 15, 'amazon': 15, 'mail': 10, 'hiring': 15, 'attention': 15, 'megibow': 20}, digest="3037f37a81cbe71f"),
    "hourly": dict(
        jobs={"prep": DB | HRL, "jobright": DB | HRL, "linkedin": DB | HRL, "lensa": DB | HRL, "dice": DB | HRL, "finish": DB | HRL | {"NTFY_TOPIC"}, "interview": {"NOTION_INTERVIEW_TOKEN"}, "report": {"NTFY_TOPIC"}},
        direct=['GMAIL_OAUTH_CLIENT_ID', 'HIRING_PIPELINE_PAGE_ID', 'INTERVIEW_ACCEPTANCE_JSON', 'LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'NOTION_JOB_LEDGER_DATA_SOURCE_ID'],
        on=['schedule', 'workflow_dispatch'], crons=['7 * * * *', '27 * * * *', '47 * * * *'], perms={'contents': 'read'}, conc={'group': "life-os-v7-hourly-${{ (github.event_name == 'schedule' || inputs.tick) && 'tick' || 'manual' }}", 'cancel-in-progress': False},
        timeouts={'prep': 45, 'jobright': 40, 'linkedin': 40, 'lensa': 40, 'dice': 8, 'finish': 40, 'interview': 15, 'report': 5}, digest="6519361f93e0b85f"),
    "network": dict(
        jobs={"network": DB | {"NOTION_API_TOKEN"}},
        direct=['LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'NETWORK_HANDOFF_PAGE_ID', 'NOTION_JOB_LEDGER_DATA_SOURCE_ID'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-network', 'cancel-in-progress': False},
        timeouts={'network': 30}, digest="5863cc2d727cbdd8"),
    "outlook": dict(
        jobs={"outlook": DB},
        direct=['LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'OUTLOOK_CLIENT_ID'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-outlook', 'cancel-in-progress': False},
        timeouts={'outlook': 20}, digest="24b93048ee6bec2c"),
    "recruiters": dict(
        jobs={"recruiters": DB | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_RECRUITERS_TOKEN"}},
        direct=['GMAIL_OAUTH_CLIENT_ID', 'LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'NOTION_RECRUITERS_DATA_SOURCE_ID', 'OUTLOOK_CLIENT_ID'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-domains', 'cancel-in-progress': False},
        timeouts={'recruiters': 20}, digest="e6de13777b39b125"),
    "watchdog": dict(
        jobs={"watch": {"NTFY_TOPIC"}},
        direct=['GITHUB_TOKEN', 'LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['schedule', 'workflow_dispatch'], crons=['17 * * * *', '47 * * * *'], perms={'contents': 'read', 'actions': 'read', 'issues': 'write'}, conc=None,
        timeouts={'watch': 3}, digest="2fc9f408876a9e05"),
    "safety": dict(
        jobs={"safety": DB | {"FIT_PROFILE_EXTRA_JSON", "FIT_PROFILE_JSON", "NTFY_TOPIC"}},
        direct=['LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['schedule', 'workflow_dispatch'], crons=['17 13 * * 1'], perms={'contents': 'read'}, conc={'group': 'life-os-v7-safety', 'cancel-in-progress': False},
        timeouts={'safety': 20}, digest="1ffe1e9866e0f88c"),
    "amazon": dict(
        jobs={"amazon": {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_AMAZON_TOKEN"}},
        direct=['GMAIL_OAUTH_CLIENT_ID', 'LIFEOS_BWS_RUNTIME_TOKEN', 'NOTION_AMAZON_DATA_SOURCE_ID'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': "${{ inputs.stage == 'orders' && 'life-os-v7-domains' || 'life-os-v7-amazon-orders' }}", 'cancel-in-progress': False},
        timeouts={'amazon': 330}, digest="b31adadc25d75d0f"),
    "diag": dict(
        jobs={"diag": DB},
        direct=['LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-diag', 'cancel-in-progress': False},
        timeouts={'diag': 10}, digest="e806649a7910dd09"),
}


@unittest.skipIf(yaml is None, "PyYAML not installed")
class BitwardenConsumerTests(unittest.TestCase):
    ACTION = "bitwarden/sm-action@1238aae8fc64b212641190a9227c8a734ab1a793"

    def test_each_consumer_loads_exactly_its_migrated_values_through_the_pinned_loader_before_anything_else(self):
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        for name, want in CONSUMERS.items():
            text = (root / f"{name}.yml").read_text()
            doc = yaml.safe_load(text)
            self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", text))), want["direct"], name)             # non-migrated refs + the Bitwarden bootstrap token, nothing else
            self.assertIn("LIFEOS_BWS_RUNTIME_TOKEN", want["direct"], name)
            self.assertFalse([n for n in UUIDS if re.search(rf"secrets\.{n}\b", text)], name)                   # no migrated value is read from GitHub any more
            self.assertEqual(re.findall(r"uses:\s*(bitwarden/\S+)", text), [self.ACTION] * len(want["jobs"]), name)
            self.assertTrue(set(want["jobs"]) <= set(doc["jobs"]), name)
            for job, names in want["jobs"].items():
                steps = doc["jobs"][job]["steps"]
                at = next(i for i, st in enumerate(steps) if str(st.get("uses", "")).startswith("bitwarden/"))
                loader = steps[at]
                self.assertEqual(steps[0]["uses"], "actions/checkout@v5", (name, job))
                self.assertTrue(at <= 2 and all("run" not in st and "env" not in st for st in steps[:at]), (name, job))   # only setup steps precede it: before every consumer
                self.assertEqual(loader["uses"], self.ACTION, (name, job))
                self.assertEqual(loader["with"]["access_token"], "${{ secrets.LIFEOS_BWS_RUNTIME_TOKEN }}", (name, job))
                self.assertEqual(sorted(re.findall(r"^([0-9a-f-]{36}) > (\w+)$", loader["with"]["secrets"], re.M)), sorted((UUIDS[n], n) for n in names), (name, job))
                self.assertEqual(len(re.findall(r" > ", loader["with"]["secrets"])), len(names), (name, job))
                self.assertFalse({"id", "if", "continue-on-error", "env"} & set(loader), (name, job))              # no bypass, no retry, no fallback source: a failed load fails the job
                self.assertEqual(set(loader), {"name", "uses", "with"}, (name, job))

    def test_the_credential_source_change_altered_no_trigger_schedule_permission_concurrency_timeout_or_command(self):
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        for name, want in CONSUMERS.items():
            doc = yaml.safe_load((root / f"{name}.yml").read_text())
            on = doc[True]
            runs = [s["run"] for j in doc["jobs"].values() for s in j["steps"] if "run" in s]
            self.assertEqual(sorted(on), want["on"], name)
            self.assertEqual([c["cron"] for c in on.get("schedule", [])], want["crons"], name)
            self.assertEqual((doc.get("permissions"), doc.get("concurrency")), (want["perms"], want["conc"]), name)
            self.assertEqual({jn: j.get("timeout-minutes") for jn, j in doc["jobs"].items()}, want["timeouts"], name)
            self.assertEqual(hashlib.sha256("\n--\n".join(runs).encode()).hexdigest()[:16], want["digest"], name)   # live/dry-run switches and stage commands included

    def test_only_the_twelve_known_workflows_exist_and_the_four_accepted_pilots_are_in_the_table(self):
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        self.assertEqual(sorted(p.stem for p in root.glob("*.yml")), sorted(CONSUMERS))
        for pilot in ("watchdog", "safety", "amazon", "diag"):
            self.assertIn(pilot, CONSUMERS)
