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

    def test_a_failed_publish_step_is_named_by_the_lane_list_and_does_not_stop_the_later_stages(self):
        """D182: `lifeos.run publish` exits 1 when a page was not created; the step is continue-on-error, so the finish job still runs everything after it,
        the lanes step names `publish`, and the report job's existing push carries it. No alert path is added."""
        import os
        import subprocess
        import tempfile
        path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hourly.yml"
        data = yaml.safe_load(path.read_text())
        steps = data["jobs"]["finish"]["steps"]
        index = next(i for i, step in enumerate(steps) if step.get("id") == "publish")
        publish = steps[index]
        self.assertTrue(publish.get("continue-on-error"))
        self.assertIn("python -m lifeos.run publish", publish["run"])
        self.assertNotIn("|| true", publish["run"])                                    # nothing swallows the exit code
        for later in steps[index + 1:]:
            self.assertNotIn("steps.publish", str(later.get("if", "")), later.get("name"))      # a failed publish never skips a later stage
        lanes = next(step for step in steps if step.get("id") == "lanes")
        self.assertEqual(lanes.get("if"), "${{ always() }}")
        self.assertEqual(data["jobs"]["finish"]["outputs"]["failed"], "${{ steps.lanes.outputs.failed }}")

        def lane_result(failed_step):
            script = re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", lambda m: "failure" if m.group(1) == f"steps.{failed_step}.outcome" else "success", lanes["run"])
            with tempfile.TemporaryDirectory() as directory:
                output = pathlib.Path(directory, "out")
                done = subprocess.run(["bash", "-e", "-c", script], env={**os.environ, "GITHUB_OUTPUT": str(output)}, capture_output=True, text=True, check=False)
                return done.returncode, done.stdout.strip(), next(line for line in output.read_text().splitlines() if line.startswith("failed="))   # LIFE-41: codes= is its own line

        self.assertEqual(lane_result("publish"), (1, "FAILED STAGES: publish", "failed=publish"))
        self.assertEqual(lane_result("none"), (0, "", "failed="))

        report = next(step for step in data["jobs"]["report"]["steps"] if "Resolver lanes" in step.get("name", ""))
        self.assertIn("finish", data["jobs"]["report"]["needs"])
        self.assertEqual(report["env"]["STAGES"], "${{ needs.finish.outputs.failed }}")
        self.assertIn("alerts push --key run_failed", report["run"])
        self.assertIn("stages: $STAGES", report["run"])
        self.assertFalse(report.get("continue-on-error"))                              # a push the provider refuses turns the job red

    def test_a_tick_runs_the_whole_chain_in_one_job_and_the_lanes_are_manual_only(self):
        """D208 (LIFE-76 PR 4b): a pipeline tick runs lifeos.jobs.tick in the finish job (one credential load, one store tunnel); the
        separate lane jobs run only on a manual run; Dice has no job of its own (the tick resolves it); a parked tick starts no Jobs job."""
        from pathlib import Path
        from lifeos.jobs import tick
        path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hourly.yml"
        data = yaml.safe_load(path.read_text())
        self.assertNotIn("resolve_dice", data[True]["workflow_dispatch"]["inputs"])
        self.assertNotIn("dice", data["jobs"])
        self.assertIn("resolve-dice", [s.name for s in tick.PLAN])
        for lane in ("jobright", "linkedin", "lensa"):
            self.assertIn("github.event_name == 'workflow_dispatch' && !inputs.tick && !inputs.quick", data["jobs"][lane]["if"], lane)   # a quick proof runs the tick path instead
        finish = data["jobs"]["finish"]
        self.assertIn("vars.V7_PIPELINE == 'true'", finish["if"])
        steps = finish["steps"]
        step = next(s for s in steps if s.get("id") == "tick")
        self.assertEqual(step["if"], "${{ env.PIPELINE == 'true' || (github.event_name == 'workflow_dispatch' && inputs.quick) }}")   # a manual quick run proves exactly what a tick runs
        self.assertIn("${{ inputs.quick && '--quick' || '' }}", step["run"])
        self.assertTrue(step.get("continue-on-error"))
        self.assertIn("python -m lifeos.jobs.tick", step["run"])
        self.assertIn("tick=${{ steps.tick.outcome }}", next(s for s in steps if s.get("id") == "lanes")["run"])
        for s in steps:                                                           # no stage the tick runs runs a second time on a tick
            if s.get("id") in ("web", "enrich", "fit", "audit", "publish", "holds", "finalize", "boards", "web_scale_up", "titlewatch", "sponsors"):
                self.assertNotIn("PIPELINE", s["if"], s["id"])

    def test_only_a_tick_or_a_domains_dispatch_starts_the_jira_job(self):
        """D212: after a manual (or cancelled) hourly run the workflow_run event has no inputs, and `inputs.jira != 'none'` was true for an empty input, so every manual
        hourly run also wrote the Jira snapshot and card. Only a completed tick or a domains dispatch that asks for Jira starts it now."""
        from pathlib import Path
        condition = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/domains.yml").read_text())["jobs"]["jira"]["if"]
        self.assertIn("(github.event_name == 'workflow_dispatch' && inputs.jira != 'none')", condition)
        self.assertIn("github.event.workflow_run.display_title == 'tick'", condition)
        self.assertNotIn("|| inputs.jira != 'none'", condition)

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
        self.assertEqual(set(domains["jobs"]["imessage"]["needs"]), {"jira", "agenda", "bills", "amazon", "attention"})   # its cards prove these regions unchanged: never beside their writers
        self.assertIn("always()", domains["jobs"]["imessage"]["if"])                   # a failed writer never stops the alerts

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
        self.assertIn("|| 'life-os-v7-domains') }}", domains["concurrency"]["group"])       # the domain jobs hold this group when they really run ...
        self.assertIn("'life-os-v7-domains-idle'", domains["concurrency"]["group"])         # ... a run that skips every job gets its own ...
        self.assertIn("'life-os-v7-domains-manual'", domains["concurrency"]["group"])       # ... and so does a manual run, so neither can replace a waiting real run
        self.assertIn("'life-os-v7-domains' ||", group)                                    # orders: the very group the real domain runs hold
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
        self.assertEqual(re.findall(r"uses:\s*(bitwarden/\S+)", text), ["bitwarden/sm-action@1238aae8fc64b212641190a9227c8a734ab1a793"] * 2)   # the reviewed v3.0.1 commit: the load and its one retry (D209)
        mappings = re.findall(r"^\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s*>\s*\w+)\s*$", text, re.M)
        self.assertEqual(sorted(mappings), sorted(2 * ["08245c07-728f-438e-940b-b4db015a6967 > GMAIL_OAUTH_CLIENT_SECRET", "672a44e9-235f-4121-a99d-b4db015a6987 > GMAIL_OAUTH_REFRESH_TOKEN",
                                                   "46fe195b-82e2-416e-8272-b4db015eea12 > NOTION_AMAZON_TOKEN"]))
        self.assertEqual(len(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)), 6)
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
        self.assertEqual((stage["options"], stage["default"]), (["scale-up-holds", "scale-up-links", "scale-up-boards", "scale-up-exclusions", "scale-up-exclusion-review", "scale-up-register", "alerts-open", "newsletter-trace", "notion-sandbox-access", "notion-sandbox-outreach-proof", "interview-ui-probe"], "scale-up-holds"))   # alerts-open arrived on main in #216 (read-only diagnostic); newsletter-trace is LIFE-64's Gmail-reading job; notion-sandbox-access is LIFE-10's Notion-reading job
        self.assertEqual(diag["permissions"], {"contents": "read"})
        self.assertEqual(diag["concurrency"], {"group": "life-os-v7-diag", "cancel-in-progress": False})
        self.assertEqual(job["timeout-minutes"], 10)
        self.assertEqual(job["steps"][-1]["run"], 'python -m lifeos.run "$STAGE"')
        self.assertEqual(job["steps"][-1]["env"], {"LIFEOS_ACQ_SSH_PORT": "${{ secrets.LIFEOS_ACQ_SSH_PORT }}", "STAGE": "${{ inputs.stage }}", "ALERTS_MESSAGE_REF": "${{ inputs.message_ref }}"})
        message = diag[True]["workflow_dispatch"]["inputs"]["message_ref"]                                  # LIFE-59: the optional opaque (SHA-256) message selector for alerts-open; an env value, never inlined in the command, and never a raw Gmail id
        self.assertEqual((message["type"], message["default"]), ("string", ""))
        self.assertNotIn("--live", text.split("\n  # LIFE-10:")[0])                                         # the database and Gmail diagnostics never write
        confirm = diag[True]["workflow_dispatch"]["inputs"]["confirm"]                                      # LIFE-10: the proof's typed confirmation, an env value, never inlined in the command
        self.assertEqual((confirm["type"], confirm["default"]), ("string", ""))
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", text))), ["GMAIL_OAUTH_CLIENT_ID", "LIFEOS_ACQ_SSH_PORT", "LIFEOS_BWS_RUNTIME_TOKEN"])      # the Client ID is the trace job's only Gmail GitHub secret
        for name in moved:
            self.assertNotIn(f"secrets.{name}", text)                                                      # no direct copy, so no fallback
        self.assertEqual(re.findall(r"uses:\s*(bitwarden/\S+)", text), ["bitwarden/sm-action@1238aae8fc64b212641190a9227c8a734ab1a793"] * 8)   # the reviewed v3.0.1 commit, once per job plus its one retry (D209)
        head, rest = text.split("\n  # LIFE-64:")                                                           # the database-only diagnostics, then the one job that reads Gmail,
        trace_text, sandbox_text = rest.split("\n  # LIFE-10:")                                              # then the one job that reads Notion
        sandbox_text, ui_text = sandbox_text.split("\n  # LIFE-88:")                                        # then LIFE-88's UI 3c probe, the one job with the Interview token
        uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        gmail = {"GMAIL_OAUTH_CLIENT_SECRET": "08245c07-728f-438e-940b-b4db015a6967", "GMAIL_OAUTH_REFRESH_TOKEN": "672a44e9-235f-4121-a99d-b4db015a6987"}
        self.assertEqual(sorted(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", head, re.M)), sorted([f"{uuid_} > {name}" for name, uuid_ in moved.items()] * 2))
        self.assertEqual(sorted(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", trace_text, re.M)), sorted([f"{uuid_} > {name}" for name, uuid_ in {**moved, **gmail}.items()] * 2))
        self.assertEqual((len(re.findall(uuid, head)), len(re.findall(uuid, trace_text))), (14, 18))                    # each in the load and its identical retry (D209)
        self.assertNotIn("GMAIL", head)                                                                    # no Gmail value or secret in the database-only job
        self.assertEqual(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", sandbox_text, re.M), ["53dc3d04-dbb2-45fe-8185-b4db015eedcb > NOTION_API_TOKEN"] * 2)   # LIFE-10: the Jobs Notion token alone, in the load and its retry
        self.assertEqual(len(re.findall(uuid, sandbox_text)), 2)
        self.assertNotIn("NOTION", head + trace_text)                                                      # no Notion value reaches the database or Gmail diagnostics
        for word in ("LIFEOS_ACQ", "GMAIL", "inputs.message_ref", "secrets.NOTION"):
            self.assertNotIn(word, sandbox_text)                                                           # nothing else reaches the Notion job, and it takes no target input
        self.assertLess(text.index("bitwarden/sm-action"), text.index("python -m lifeos.run"))             # loaded before the diagnostic command
        trace = diag["jobs"]["trace"]
        sandbox = diag["jobs"]["sandbox"]
        self.assertEqual((job["if"], trace["if"], sandbox["if"]),
                         ("${{ inputs.stage != 'newsletter-trace' && inputs.stage != 'notion-sandbox-access' && inputs.stage != 'notion-sandbox-outreach-proof' && inputs.stage != 'interview-ui-probe' }}",
                          "${{ inputs.stage == 'newsletter-trace' }}",
                          "${{ inputs.stage == 'notion-sandbox-access' || inputs.stage == 'notion-sandbox-outreach-proof' }}"))   # exactly one of the three jobs runs
        self.assertEqual(sandbox["timeout-minutes"], 5)
        token = "${{ steps.bws_sandbox.outputs.NOTION_API_TOKEN || steps.bws_sandbox_retry.outputs.NOTION_API_TOKEN }}"
        read, proof = sandbox["steps"][-2:]
        self.assertEqual((read["if"], read["run"], read["env"]), ("${{ inputs.stage == 'notion-sandbox-access' }}", "python -m lifeos.run notion-sandbox-access --limit 0", {"NOTION_API_TOKEN": token}))
        self.assertEqual((proof["if"], proof["run"], proof["env"]), ("${{ inputs.stage == 'notion-sandbox-outreach-proof' }}", "python -m lifeos.run notion-sandbox-outreach-proof --limit 0 --live",
                                                                    {"NOTION_API_TOKEN": token, "PROOF_CONFIRM": "${{ inputs.confirm }}"}))   # the only write step; the stage itself refuses without SYNTHETIC
        self.assertEqual([s.get("env") for s in sandbox["steps"][:-2]], [None] * (len(sandbox["steps"]) - 2))   # only the two stage steps receive the token
        self.assertIs(sandbox["steps"][2]["with"]["set_env"], False)                                       # a masked step output, never exported job-wide
        ui = diag["jobs"]["interview_ui"]                                                                  # LIFE-88: the one job that holds the Interview token
        self.assertEqual((ui["if"], ui["timeout-minutes"]), ("${{ inputs.stage == 'interview-ui-probe' }}", 5))
        self.assertEqual(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", ui_text, re.M), ["092b44ae-32cb-453b-8cc2-b4db015ef571 > NOTION_INTERVIEW_TOKEN"] * 2)
        for word in ("LIFEOS_ACQ", "GMAIL", "inputs.message_ref", "secrets.NOTION", "NOTION_API_TOKEN"):
            self.assertNotIn(word, ui_text)                                                                # nothing else reaches the probe, and it takes no target input
        self.assertNotIn("NOTION_INTERVIEW_TOKEN", head + trace_text + sandbox_text)
        probe = ui["steps"][-1]
        self.assertEqual((probe["run"], probe["env"]), ("python -m lifeos.run interview-ui-probe --limit 0 --live", {
            "NOTION_INTERVIEW_TOKEN": "${{ steps.bws_interview.outputs.NOTION_INTERVIEW_TOKEN || steps.bws_interview_retry.outputs.NOTION_INTERVIEW_TOKEN }}",
            "PROBE_CONFIRM": "${{ inputs.confirm }}"}))                                                    # the stage itself refuses without QA
        self.assertEqual([s.get("env") for s in ui["steps"][:-1]], [None] * (len(ui["steps"]) - 1))
        self.assertEqual(trace["timeout-minutes"], 10)
        self.assertEqual(trace["steps"][-1]["run"], 'python -m lifeos.run "$STAGE"')
        self.assertEqual(trace["steps"][-1]["env"], {"LIFEOS_ACQ_SSH_PORT": "${{ secrets.LIFEOS_ACQ_SSH_PORT }}", "GMAIL_OAUTH_CLIENT_ID": "${{ secrets.GMAIL_OAUTH_CLIENT_ID }}", "STAGE": "${{ inputs.stage }}", "ALERTS_MESSAGE_REF": "${{ inputs.message_ref }}"})


@unittest.skipIf(yaml is None, "PyYAML not installed")
class PullRequestTestsWorkflow(unittest.TestCase):
    """pr-tests.yml is the secret-free pull request check (and, since Oct 25 2026, the check of every merge to main): read-only, no secret, no loader, no database, the one accepted test command and the 45 s / 75 s suite limits (75 since Jim, Oct 25 2026)."""
    path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pr-tests.yml"

    def test_it_runs_on_pull_requests_and_main_only_read_only_and_holds_no_secret(self):
        text, doc = self.path.read_text(), yaml.safe_load(self.path.read_text())
        self.assertEqual(list(doc[True]), ["pull_request", "push"])
        self.assertEqual(doc[True]["push"], {"branches": ["main"]})                  # main only: a merge gives the Edge release a tested commit; no other branch, tag, schedule or dispatch
        self.assertEqual(doc["permissions"], {"contents": "read"})
        for word in ("secrets.", "bitwarden", "LIFEOS_", "NOTION_", "GMAIL_", "--live"):
            self.assertNotIn(word, text)
        self.assertEqual([j for j in doc["jobs"]], ["unit-tests"])

    def test_it_reuses_the_shared_python_action_and_runs_the_accepted_command_with_the_runtime_limits(self):
        doc = yaml.safe_load(self.path.read_text())
        steps = doc["jobs"]["unit-tests"]["steps"]
        self.assertEqual([s.get("uses") for s in steps[:2]], ["actions/checkout@v5", "./.github/actions/python"])
        run = [s["run"] for s in steps if "Unit tests" in s.get("name", "")][0]
        self.assertIn("python -m tests.shard 1 2", run)                      # Jim, Oct 26 2026: two parallel shards of whole test modules; tests/contracts/test_shards.py proves every test runs in exactly one
        self.assertIn("python -m tests.shard 2 2", run)
        self.assertNotIn("unittest discover", run)
        self.assertRegex(run, r'-ge 75 \]; then .*exit 1')
        self.assertRegex(run, r'-ge 45 \]; then echo "::warning::')


@unittest.skipIf(yaml is None, "PyYAML not installed")
class LaneGuardWorkflow(unittest.TestCase):
    """LIFE-78: the lane guard runs from TRUSTED default-branch code and reads the pull request only as data through the API; it can hold no secret and no write permission."""
    path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "lane-guard.yml"

    def test_it_is_pull_request_target_read_only_and_secret_free(self):
        text, doc = self.path.read_text(), yaml.safe_load(self.path.read_text())
        self.assertEqual(list(doc[True]), ["pull_request_target"])
        self.assertEqual(doc["permissions"], {"contents": "read", "pull-requests": "read"})
        for word in ("secrets.", "bitwarden", "LIFEOS_", "NOTION_", "GMAIL_", "--live"):
            self.assertNotIn(word, text)
        self.assertEqual(list(doc["jobs"]), ["lane-guard"])        # and (above) no schedule or dispatch trigger

    def test_it_never_checks_out_or_runs_the_pull_requests_code(self):
        """The tampering test: a ref on the checkout, a PR-controlled value in a command, or any other script than the guard on the default branch would let a PR change what judges it."""
        text, steps = self.path.read_text(), yaml.safe_load(self.path.read_text())["jobs"]["lane-guard"]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
        self.assertEqual(checkout["with"], {"persist-credentials": False})                    # no `ref:`, no `repository:`: the default branch only
        for risky in ("head.sha", "head.ref", "head.repo", "github.event.pull_request.body", "github.event.pull_request.title", "github.head_ref", "pull/"):
            self.assertNotIn(risky, text)
        runs = [s["run"] for s in steps if "run" in s]
        self.assertEqual([r.split("  ")[0] for r in runs], ["python3 -I lifeos/lane_guard.py"])
        self.assertEqual([s["uses"] for s in steps if "uses" in s], ["actions/checkout@v5"])          # nothing installed: stdlib only
        self.assertEqual(next(s for s in steps if "run" in s)["env"], {"GITHUB_TOKEN": "${{ github.token }}"})

    def test_pr_tests_no_longer_carries_or_runs_the_guard(self):
        self.assertNotIn("lane_guard", (self.path.parent / "pr-tests.yml").read_text())


class FastPathFirstTests(unittest.TestCase):
    """D206 (LIFE-76 PR 1): the Ledger never waits on bulk discovery, and a quick proof run reads few boards."""
    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest("PyYAML not installed")
        root = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows"
        cls.hourly = yaml.safe_load((root / "hourly.yml").read_text())

    def test_bulk_discovery_runs_after_publish_and_finalize(self):
        ids = [s.get("id") for s in self.hourly["jobs"]["finish"]["steps"]]
        fast = [ids.index(i) for i in ("enrich", "fit", "audit", "publish", "holds", "finalize")]
        bulk = [ids.index(i) for i in ("boards", "web_scale_up", "titlewatch", "sponsors")]
        self.assertEqual(fast, sorted(fast))
        self.assertLess(max(fast), min(bulk))

    def test_quick_caps_the_boards_read_and_a_tick_keeps_the_full_cap(self):
        inputs = self.hourly[True]["workflow_dispatch"]["inputs"]
        self.assertEqual((inputs["quick"]["type"], inputs["quick"]["default"]), ("boolean", False))
        step = next(s for s in self.hourly["jobs"]["finish"]["steps"] if s.get("id") == "boards")
        self.assertIn("--limit ${{ inputs.quick && '50' || '600' }}", step["run"])        # a schedule event has no inputs, so a tick reads the full 600


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
        self.assertEqual(sorted(shared), ["GMAIL_OAUTH_CLIENT_ID", "HIRING_PIPELINE_PAGE_ID", "LIFEOS_ACQ_SSH_PORT", "NOTION_JOB_LEDGER_DATA_SOURCE_ID"])   # EDGE-1.3: migrated values are no longer workflow level
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
      "LIFEOS_ACQ_SSH_PORT", "LIFEOS_ACQ_SSH_PRIVATE_KEY", "LIFEOS_ACQ_SSH_USER", "LIFEOS_ACQ_SSH_JUMP_KEY"}     # LIFE-85: the forward-only Oracle relay key, beside the store's own
DOMAIN_SECRETS = {         # the only secrets each domain job may see; adding one here is a conscious decision
    "report": {"NTFY_TOPIC"},   # the failure-push job: the phone topic and nothing else
    "imessage": DB | {"LIFEOS_ACQ_SSH_PORT", "NTFY_TOPIC", "IMESSAGE_ALERTS_HOST", "NOTION_JIRA_TOKEN", "AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID",
                  "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID"},   # LIFE-34 Communications card (Notion writer + region anchors). LIFE-31: the Tailscale OAuth pair goes only to the pinned join action (see TAILSCALE_WITH)
    "jira": DB | {"JIRA_API_TOKEN", "JIRA_BASE_URL", "JIRA_BOARDS", "JIRA_CARD_BLOCK_ID", "JIRA_CARD_PROJECTS", "JIRA_EMAIL",
                  "JIRA_GTV_CONTEXT_URL", "JIRA_GTV_EPIC", "JIRA_SITE_URL", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"},
    "outlook": DB | {"GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "NTFY_TOPIC", "OUTLOOK_CLIENT_ID"},
    "bills": DB | {"BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_BILLS_DATA_SOURCE_ID", "NOTION_BILLS_TOKEN", "NOTION_JIRA_TOKEN"},
    "agenda": DB | {"CALENDAR_CARD_BLOCK_ID", "GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN"},
    "attention": DB | {"GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC", "OUTLOOK_CLIENT_ID"},
    "megibow": DB | {"GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "MEGIBOW_BLOCK_ID", "MEGIBOW_EXCLUDED_DOMAINS", "MEGIBOW_REVIEW_DB_ID", "NOTION_JIRA_TOKEN", "NTFY_TOPIC", "OUTLOOK_CLIENT_ID"},
    "dcc": {"ATTENTION_SYNC_OUTCOME", "AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "DCC_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "MAIL_ALERTS_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN"},
    "recruiters": DB | {"GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_RECRUITERS_DATA_SOURCE_ID", "NOTION_RECRUITERS_TOKEN", "OUTLOOK_CLIENT_ID",
                     "NOTION_API_TOKEN", "NOTION_JOB_LEDGER_DATA_SOURCE_ID", "V7_CLOSE_BASE_URL",
                     "GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "HIRING_PIPELINE_PAGE_ID", "NOTION_INTERVIEW_TOKEN",
                     "HIRING_TABLE_BLOCK_ID", "NOTION_JIRA_TOKEN"},   # LIFE-20: the Recruiter Outreach step (the Daily Report writer and the pinned Hiring Pipeline table it is anchored on); LIFE-10: the job-page outreach step reads and writes Job Ledger pages; LIFE-88: the Interview OS step
    "mail": DB | {"AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET",
                  "GMAIL_OAUTH_REFRESH_TOKEN", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "MAIL_ALERTS_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"},
    "hiring": DB | {"GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "HIRING_CARD_BLOCK_ID", "HIRING_PIPELINE_PAGE_ID", "HIRING_STATUS_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_API_TOKEN",
                    "NOTION_INTERVIEW_TOKEN", "NOTION_JIRA_TOKEN", "NOTION_JOB_LEDGER_DATA_SOURCE_ID", "V7_CLOSE_BASE_URL"},
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

    def test_manual_runs_never_share_a_slot_with_after_tick_runs_and_the_interview_writer_takes_turns(self):
        group = self.doc["concurrency"]["group"]                                           # Oct 21 2026: a string of manual runs replaced the waiting after-tick runs and the Daily Report header went stale
        self.assertIn("workflow_run", group)
        self.assertIn("'life-os-v7-domains-manual'", group)
        self.assertNotEqual(group.count("life-os-v7-domains'"), 0)                           # the after-tick runs keep the group the manual Amazon and Recruiters workflows queue behind
        self.assertIs(self.doc["concurrency"]["cancel-in-progress"], False)
        writer = self.doc["jobs"]["recruiters"]["concurrency"]                              # the one writer of the opportunity pages: after-tick and manual runs take turns
        self.assertEqual(writer, {"group": "life-os-v7-interview-writer", "cancel-in-progress": False})
        self.assertEqual([name for name, job in self.doc["jobs"].items() if "concurrency" in job], ["recruiters"])

    def test_only_five_steps_read_the_close_origin_from_its_own_secret(self):
        """D198 S3 / LIFE-78 / LIFE-20 / E05: the links exist only when the V7_CLOSE_BASE_URL secret is set; five steps pass it (Hiring Pipeline Close links, the job-page Outreach links, the Recruiter Outreach table's action links, the Interview writer's Mark Complete link, and hourly's network-surface step for the Mark as Sent link in a Network Leads block), from that secret, and nothing else names it."""
        seen = [(job, step.get("id")) for job, spec in self.doc["jobs"].items() for step in spec.get("steps", []) if "V7_CLOSE_BASE_URL" in str(step)]
        self.assertEqual(sorted(seen), [("hiring", "hiring"), ("recruiters", "interview"), ("recruiters", "jpoutreach"), ("recruiters", "routreach")])      # LIFE-20: the Recruiter Outreach table's action links; E05: Mark Complete
        for job, ident in seen:
            step = next(s for s in self.doc["jobs"][job]["steps"] if s.get("id") == ident)
            self.assertEqual(step["env"]["V7_CLOSE_BASE_URL"], "${{ secrets.V7_CLOSE_BASE_URL }}")
        for path in (pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.y*ml"):
            if path.name not in ("domains.yml", "hourly.yml"):
                self.assertNotIn("V7_CLOSE_BASE_URL", path.read_text(), path.name)
        hourly = yaml.safe_load((pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hourly.yml").read_text())
        named = [(job, step.get("id")) for job, spec in hourly["jobs"].items() for step in spec.get("steps", []) if "V7_CLOSE_BASE_URL" in str(step)]
        self.assertEqual(named, [("finish", "network_surface")])                                   # the Jobs pipeline's one reader: it never reaches the other finish steps
        self.assertEqual(next(s for s in hourly["jobs"]["finish"]["steps"] if s.get("id") == "network_surface")["env"]["V7_CLOSE_BASE_URL"], "${{ secrets.V7_CLOSE_BASE_URL }}")
        self.assertNotIn("V7_CLOSE_BASE_URL", str(hourly.get("env")))

    def test_the_calendar_mass_delete_brake_is_skipped_only_by_a_manual_input_and_that_run_touches_nothing_else(self):
        inputs = self.doc[True]["workflow_dispatch"]["inputs"]
        self.assertIs(inputs["allow_mass_delete"]["default"], False)                         # off unless a person ticks it for one run
        self.assertIs(inputs["calendar_sync"]["default"], False)
        job = self.doc["jobs"]["outlook"]
        self.assertIn("inputs.calendar_sync", job["if"])
        steps = {s.get("id"): s for s in job["steps"]}
        self.assertEqual(steps["calsync"]["env"]["CALENDAR_ALLOW_MASS_DELETE"], "${{ inputs.allow_mass_delete && 'true' || '' }}")      # a tick (no inputs) is always ''
        self.assertEqual(steps["outnews"]["if"], "${{ !inputs.calendar_sync || inputs.all }}")                         # a calendar-only run never files newsletters
        for name, other in self.doc["jobs"].items():
            if name != "outlook":
                self.assertNotIn("CALENDAR_ALLOW_MASS_DELETE", str(other))

    def test_each_job_sees_only_its_own_secrets(self):
        self.assertEqual(set(self.doc["jobs"]), set(DOMAIN_SECRETS))
        for name, job in self.doc["jobs"].items():
            used = {k for k, v in (job.get("env") or {}).items() if "secrets." in str(v) or ".outputs." in str(v)}
            for step in job["steps"]:
                used |= {k for k, v in (step.get("env") or {}).items() if "secrets." in str(v) or ".outputs." in str(v)}   # a migrated value counts when it arrives as a loader output
            self.assertLessEqual(used, DOMAIN_SECRETS[name], f"{name}: {sorted(used - DOMAIN_SECRETS[name])}")

    def test_a_failing_domain_step_is_a_warning_never_a_failed_run(self):
        for name, job in self.doc["jobs"].items():
            if name == "report":                                                                                   # the failure-push job (see the next test) is the alarm, not a domain stage
                continue
            work = [s for s in job["steps"] if s.get("id") and not str(s.get("uses", "")).startswith("bitwarden/")]   # the Bitwarden loader is deliberately fail-closed: a failed load fails the job before its command
            self.assertTrue(work and all(s.get("continue-on-error") for s in work), name)

    def test_the_report_job_pushes_the_phone_only_for_a_failed_tick_triggered_run_and_writes_nothing(self):
        job = self.doc["jobs"]["report"]
        self.assertEqual(set(job["needs"]), set(self.doc["jobs"]) - {"report"})                                    # it watches every domain job
        condition = job["if"]
        for part in ("always()", "github.event_name == 'workflow_run'", "display_title == 'tick'", "contains(needs.*.result, 'failure')"):
            self.assertIn(part, condition)
        (step,) = [s for s in job["steps"] if "run" in s and not s["run"].startswith("sleep ")]                   # not the Bitwarden retry pause (D209)
        self.assertIn("lifeos.platform.alerts push --key domain_jobs_failed", step["run"])
        self.assertFalse(step.get("continue-on-error"))                                                           # a push the provider refuses turns the job red
        self.assertEqual(sorted(re.findall(r"needs\.(\w+)\.result", step["run"])), sorted(job["needs"]))         # every domain job is named in the failed list


DB = {f"LIFEOS_ACQ_{n}" for n in ("DB_NAME", "DB_PASSWORD", "DB_USER", "SSH_HOST", "SSH_KNOWN_HOSTS", "SSH_PRIVATE_KEY", "SSH_USER")}
JUMP = {"LIFEOS_ACQ_SSH_JUMP_KEY"}                    # LIFE-85: every hourly / domains / network store step also gets the Oracle relay key
TS = {"TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET"}   # loaded by those store jobs for their tailnet join; only the pinned join action receives them (TAILSCALE_WITH)
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
    "DCC_CARD_BLOCK_ID": "b78d1476-663e-496d-9bc4-b4e1003b61b0",
    "NOTION_JIRA_TOKEN": "53f47cb9-f6bb-4cde-87f4-b4db015ef928",
    "NOTION_RECRUITERS_TOKEN": "b7b921fa-3f62-478a-a4f5-b4db015efcf7",
    "NTFY_TOPIC": "83c553e9-23a6-45aa-9006-b4db015f00ac",
    "OPEN_JOBS_CONTACT": "7b62e076-8a46-4228-9977-b4db015f0463",
    "TINYFISH_API_KEY": "f45f29e2-acbc-4be7-9749-b4db015f0817",
    "TAILSCALE_OAUTH_CLIENT_ID": "454e12df-637f-4f65-9ec1-b4e30052942f",        # COMM-1.4b (LIFE-31 comments 10138, 10139): record ids only, never values
    "TAILSCALE_OAUTH_CLIENT_SECRET": "e54f0a84-84c3-47d8-8b5b-b4e30052d6e9",
    "IMESSAGE_ALERTS_HOST": "6590208b-506d-4238-ab10-b4e30051b25f",
    "LIFEOS_ACQ_SSH_JUMP_KEY": "192db2d1-be66-4fca-b9b5-b4e900240507",        # LIFE-85 (Jim, Oct 19 2026): the v7-store-fwd relay key; id only, never the value
}
# The one action that may receive loader outputs through `with:` (LIFE-31): the pinned Tailscale join, and only its two OAuth inputs.
TAILSCALE_ACTION = "tailscale/github-action@d1b6cd204f8dceda5b3eaad7f1f767be390056cd"
TAILSCALE_WITH = {"oauth-client-id": "TAILSCALE_OAUTH_CLIENT_ID", "oauth-secret": "TAILSCALE_OAUTH_CLIENT_SECRET"}
# Every V7 workflow that consumes a migrated value reads it through a pinned Bitwarden loader step (EDGE-1.3). Per workflow: the loader's names per job, the remaining direct GitHub secret
# refs, and the unchanged trigger/permission/concurrency/timeout facts plus a fingerprint of every run command (so a credential-source change cannot alter behaviour).
CONSUMERS = {
    "branches": dict(                                                    # D244 (LIFE-81): archive on merge (narrow archive token only); weekly report after safety (Notion token only)
        jobs={"report": {"NOTION_JIRA_TOKEN"}},
        direct=['LIFEOS_BWS_RUNTIME_TOKEN', 'V7_ARCHIVE_TOKEN', 'WEEKLY_REVIEW_PAGE_ID'],
        on=['pull_request', 'workflow_dispatch', 'workflow_run'], crons=[], perms={'contents': 'read'}, conc={'group': "life-os-v7-branches-${{ github.event.pull_request.number || 'report' }}", 'cancel-in-progress': False},
        timeouts={'archive': 10, 'report': 10}, digest="63d922348f6d79c3"),
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
        jobs={"jira": DB | JUMP | TS | {"JIRA_API_TOKEN", "JIRA_EMAIL", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"}, "outlook": DB | JUMP | TS | {"GCAL_SERVICE_ACCOUNT_JSON", "NTFY_TOPIC"}, "bills": DB | JUMP | TS | {"NOTION_BILLS_TOKEN", "NOTION_JIRA_TOKEN"}, "agenda": DB | JUMP | TS | {"GCAL_SERVICE_ACCOUNT_JSON", "NOTION_JIRA_TOKEN"}, "amazon": {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_AMAZON_TOKEN", "NOTION_JIRA_TOKEN"}, "mail": DB | JUMP | TS | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"}, "hiring": DB | JUMP | TS | {"GCAL_SERVICE_ACCOUNT_JSON", "NOTION_API_TOKEN", "NOTION_INTERVIEW_TOKEN", "NOTION_JIRA_TOKEN"}, "attention": DB | JUMP | TS | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"}, "megibow": DB | JUMP | TS | {"GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"}, "recruiters": DB | JUMP | TS | {"GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_API_TOKEN", "NOTION_INTERVIEW_TOKEN", "NOTION_JIRA_TOKEN", "NOTION_RECRUITERS_TOKEN"}, "dcc": {"DCC_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN"}, "report": {"NTFY_TOPIC"}, "imessage": DB | JUMP | TS | {"NTFY_TOPIC", "TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "IMESSAGE_ALERTS_HOST", "NOTION_JIRA_TOKEN"}},
        direct=['AMAZON_CARD_BLOCK_ID', 'ATTENTION_CARD_BLOCK_ID', 'BILLS_CARD_BLOCK_ID', 'CALENDAR_CARD_BLOCK_ID', 'GCAL_CALENDAR_ID', 'GMAIL_OAUTH_CLIENT_ID', 'HIRING_CARD_BLOCK_ID', 'HIRING_PIPELINE_PAGE_ID', 'HIRING_STATUS_BLOCK_ID', 'HIRING_TABLE_BLOCK_ID', 'JIRA_BASE_URL', 'JIRA_BOARDS', 'JIRA_CARD_BLOCK_ID', 'JIRA_CARD_PROJECTS', 'JIRA_GTV_CONTEXT_URL', 'JIRA_GTV_EPIC', 'JIRA_SITE_URL', 'LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'MAIL_ALERTS_CARD_BLOCK_ID', 'MEGIBOW_BLOCK_ID', 'MEGIBOW_EXCLUDED_DOMAINS', 'MEGIBOW_REVIEW_DB_ID', 'NOTION_AMAZON_DATA_SOURCE_ID', 'NOTION_BILLS_DATA_SOURCE_ID', 'NOTION_JOB_LEDGER_DATA_SOURCE_ID', 'NOTION_RECRUITERS_DATA_SOURCE_ID', 'OUTLOOK_CLIENT_ID', 'V7_CLOSE_BASE_URL'],
        on=['workflow_dispatch', 'workflow_run'], crons=[], perms={'contents': 'read'}, conc={'group': "${{ (github.event_name == 'workflow_dispatch' && (inputs.refresh == 'hiring' || inputs.refresh == 'recruiters')) && 'life-os-v7-domains' || github.event_name != 'workflow_run' && 'life-os-v7-domains-manual' || ((github.event.workflow_run.conclusion == 'cancelled' || github.event.workflow_run.display_title != 'tick') && 'life-os-v7-domains-idle' || 'life-os-v7-domains') }}", 'cancel-in-progress': False},   # a run that will skip every job must not share the group that holds the one waiting real run
        timeouts={'jira': 15, 'outlook': 10, 'bills': 15, 'agenda': 15, 'amazon': 15, 'mail': 10, 'hiring': 15, 'attention': 15, 'megibow': 20, 'dcc': 10, 'recruiters': 30, 'imessage': 10, 'report': 5}, digest="825e04a468f2641d"),   # LIFE-20 added the recruiter-outreach command (the Daily Report's Recruiter Outreach table, after the Recruiters stage); LIFE-88 added the Interview OS stage command to the recruiters job (where the mail credentials are), and a live run showed the job's two mail reads plus the Notion writes need 30 minutes, not 20; LIFE-10 added the job-page-outreach command; D222 (withdrawn Oct 20 2026) removed the job-outreach and installer commands; LIFE-31 added the imessage-alerts stage command, its warning and the report job's imessage entry; LIFE-34 the imessage-comms command and its warning; the Oracle Cloud card (Oct 15 2026) the oracle-card command and its warning; LIFE-77 D228 the AI Capacity card step
    "hourly": dict(
        jobs={"prep": DB | JUMP | TS | HRL, "jobright": DB | JUMP | TS | HRL, "linkedin": DB | JUMP | TS | HRL, "lensa": DB | JUMP | TS | HRL, "finish": DB | JUMP | TS | HRL | {"NTFY_TOPIC", "TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "IMESSAGE_ALERTS_HOST"}, "interview": {"NOTION_INTERVIEW_TOKEN"}, "report": {"NTFY_TOPIC"}},
        direct=['GMAIL_OAUTH_CLIENT_ID', 'HIRING_PIPELINE_PAGE_ID', 'INTERVIEW_ACCEPTANCE_JSON', 'INTERVIEW_ACCEPTANCE_NEGATIVE_JSON', 'LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'NOTION_JOB_LEDGER_DATA_SOURCE_ID', 'V7_CLOSE_BASE_URL', 'V7_CLOUD_FIT_PROFILE_REF'],   # LIFE-41: the shadow's brief reference, masked (a variable is not)
        on=['schedule', 'workflow_dispatch'], crons=['7 * * * *', '27 * * * *', '47 * * * *'], perms={'contents': 'read'}, conc={'group': "life-os-v7-hourly-${{ (github.event_name == 'schedule' || inputs.tick) && 'tick' || 'manual' }}", 'cancel-in-progress': False},
        timeouts={'prep': 45, 'jobright': 40, 'linkedin': 40, 'lensa': 40, 'finish': 58, 'interview': 15, 'report': 5}, digest="ef06e5512c7197e4"),   # LIFE-76 (Oct 25 2026 drain): a manual enrich reads 450, not 150;   # D238: finish 58 min and the batch to 45 min, so a tick's batch fills the hour;   # D233 (LIFE-42): + the always-dry-run negative interview acceptance case;   # D208 (LIFE-76 PR 4b): a tick (and a manual quick proof, with --quick) runs python -m lifeos.jobs.tick in the finish job; the Dice job and its command are gone (the tick resolves Dice);   # D206 (LIFE-76 PR 1): bulk discovery (boards, Scale-Up, title watch, sponsors) moved after finalize; boards --limit follows the quick input;   # D202 review: + the read-only boards-preview command (D204 acceptance: over the 600-board sample); D203: + linkedin-search;   # D201 removed the resolve-lensa and openjobs commands; D202 added the boards command and its finish-loop entry; LIFE-41 the tick's batch is sized to the hour
    "network": dict(
        jobs={"network": DB | JUMP | TS | {"NOTION_API_TOKEN"}},
        direct=['LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'NETWORK_HANDOFF_PAGE_ID', 'NOTION_JOB_LEDGER_DATA_SOURCE_ID'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-network', 'cancel-in-progress': False},
        timeouts={'network': 30}, digest="5863cc2d727cbdd8"),
    "outlook": dict(
        jobs={"outlook": DB},
        direct=['LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'OUTLOOK_CLIENT_ID'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-outlook', 'cancel-in-progress': False},
        timeouts={'outlook': 20}, digest="24b93048ee6bec2c"),
    "profile-sync": dict(                                                # the Notion-approved Fit evidence block into the store; reads the store credentials from Bitwarden, writes nothing to it
        jobs={"sync": DB},
        direct=['LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'NOTION_FIT_SYNC_TOKEN'],
        on=['workflow_dispatch', 'workflow_run'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-profile-sync', 'cancel-in-progress': False},   # its own group: a waiting run in a shared group is cancelled by the next one, which would cancel a waiting domain run
        timeouts={'sync': 10}, digest="0b34447bfaff37cf"),
    "granola": dict(                                                     # LIFE-37 Step 2 (Jim, Oct 26 2026): ONE pinned Granola meeting from Oracle into its Interview round; dry run unless live; Interview token, Oracle host and the tailnet join only
        jobs={"granola": {"NOTION_INTERVIEW_TOKEN", "TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "IMESSAGE_ALERTS_HOST"}},
        direct=['HIRING_PIPELINE_PAGE_ID', 'LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-granola', 'cancel-in-progress': False},
        timeouts={'granola': 15}, digest="8e4c30f30ad70e52"),
    "manual-job": dict(                                                  # LIFE-37 (Jim, Oct 26 2026): ONE job Jim names by hand into Jobs OS and the Interview OS writer; dry run unless live; the store credentials and the Interview token only
        jobs={"manual": DB | {"NOTION_INTERVIEW_TOKEN"}},
        direct=['HIRING_PIPELINE_PAGE_ID', 'LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-interview-writer', 'cancel-in-progress': False},
        timeouts={'manual': 15}, digest="19cc00b03711f4e0"),
    "interview-proof": dict(                                             # LIFE-78: the synthetic Interview journey in a fresh QA sandbox; the Interview token only, its own queue
        jobs={"proof": {"NOTION_INTERVIEW_TOKEN"}},
        direct=['LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-interview-proof', 'cancel-in-progress': False},
        timeouts={'proof': 30}, digest="f1c02db4a2b8dad7"),
    "recruiters": dict(
        jobs={"recruiters": DB | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_RECRUITERS_TOKEN"}},
        direct=['GMAIL_OAUTH_CLIENT_ID', 'LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN', 'NOTION_RECRUITERS_DATA_SOURCE_ID', 'OUTLOOK_CLIENT_ID'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-domains', 'cancel-in-progress': False},
        timeouts={'recruiters': 20}, digest="e6de13777b39b125"),
    "watchdog": dict(
        jobs={"watch": {"NTFY_TOPIC"}},
        direct=['GITHUB_TOKEN', 'LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['schedule', 'workflow_dispatch'], crons=['17 * * * *', '47 * * * *'], perms={'contents': 'read', 'actions': 'read', 'issues': 'write'}, conc=None,
        timeouts={'watch': 3}, digest="4f6304d28137b230"),
    "safety": dict(
        jobs={"safety": DB | {"FIT_PROFILE_EXTRA_JSON", "FIT_PROFILE_JSON", "NTFY_TOPIC"}},
        direct=['LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['schedule', 'workflow_dispatch'], crons=['17 13 * * 1'], perms={'contents': 'read'}, conc={'group': 'life-os-v7-safety', 'cancel-in-progress': False},
        timeouts={'safety': 20}, digest="88fd04b0f310f158"),
    "amazon": dict(
        jobs={"amazon": {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_AMAZON_TOKEN"}},
        direct=['GMAIL_OAUTH_CLIENT_ID', 'LIFEOS_BWS_RUNTIME_TOKEN', 'NOTION_AMAZON_DATA_SOURCE_ID'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': "${{ inputs.stage == 'orders' && 'life-os-v7-domains' || 'life-os-v7-amazon-orders' }}", 'cancel-in-progress': False},
        timeouts={'amazon': 330}, digest="b31adadc25d75d0f"),
    "fit-edge-test": dict(
        jobs={"fit_edge": {"TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "IMESSAGE_ALERTS_HOST"}},                 # LIFE-60: the manual synthetic proof of Oracle's fit1 verb; the same ephemeral tag:v7-runner join the iMessage job uses
        direct=['LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-fit-edge', 'cancel-in-progress': False},
        timeouts={'fit_edge': 5}, digest="62c235d80f490685"),
    "diag": dict(
        jobs={"diag": DB, "trace": DB | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN"}, "sandbox": {"NOTION_API_TOKEN"}, "interview_ui": {"NOTION_INTERVIEW_TOKEN"}},   # LIFE-88: the UI 3c probe alone holds the Interview token; LIFE-64: the trace job alone reads Gmail (list and read only); LIFE-10: the sandbox job alone reads Notion (three fixed reads)
        direct=['GMAIL_OAUTH_CLIENT_ID', 'LIFEOS_ACQ_SSH_PORT', 'LIFEOS_BWS_RUNTIME_TOKEN'],
        on=['workflow_dispatch'], crons=[], perms={'contents': 'read'}, conc={'group': 'life-os-v7-diag', 'cancel-in-progress': False},
        timeouts={"diag": 10, "trace": 10, "sandbox": 5, "interview_ui": 5}, digest="19bbed28cb553299"),   # LIFE-88: + the interview-ui-probe command
}
# The eight workflows cut over after the pilots: the loader is exported to nothing (set_env: false) and each CONSUMER step receives, as a masked step output, exactly the migrated values it
# effectively received on main 659de1e (workflow-, job- and step-level env merged), except hourly `report`, which only pushes an alert and needs just the topic. Every step not listed
# here (checkout, setup, installs, tests, warnings, the loader itself) receives none, so exposure is the same as or narrower than before for every step.
CONSUMER_STEPS = {
    "branches": {
        "report": {
            'Repository hygiene report (stale branches; run summary, and the Weekly Review callout when live)': {"NOTION_JIRA_TOKEN"},
        },
    },
    "agenda": {
        "agenda": {
            'Agenda snapshot (read-only Google Calendar)': DB | {"GCAL_SERVICE_ACCOUNT_JSON"},
            'Agenda card (saved snapshot only)': DB | {"NOTION_JIRA_TOKEN"},
        },
    },
    "bills": {
        "bills": {
            'Bills snapshot (read-only; save only when live)': DB | {"NOTION_BILLS_TOKEN"},
        },
    },
    "calendar": {
        "calendar": {
            'Calendar sync (counts only)': DB | {"GCAL_SERVICE_ACCOUNT_JSON"},
        },
    },
    "domains": {
        "imessage": {
            'iMessage urgent alerts (metadata only from Oracle; private alert state; counts-only push)': DB | JUMP | {"NTFY_TOPIC", "IMESSAGE_ALERTS_HOST"},
            'Communications card, Messages row (7-day unread count and latest sender; counts only in the log; writes only when live)': {"IMESSAGE_ALERTS_HOST", "NOTION_JIRA_TOKEN"},
            'Oracle Cloud card (daily from 6 AM CT; numbers only in the log; writes only when live)': {"IMESSAGE_ALERTS_HOST", "NOTION_JIRA_TOKEN"},
            'AI Capacity card (readings only, no model run; numbers only in the log; writes only when live)': {"IMESSAGE_ALERTS_HOST", "NOTION_JIRA_TOKEN"},   # LIFE-77 D228: the same two as the Oracle Cloud card
        },
        "jira": {
            'Jira sprint rollover (scheduled; waits for the sprint to end and Monday 6 AM; live only when V7_LIVE)': {"JIRA_API_TOKEN", "JIRA_EMAIL"},
            'Jira rollover watch (pages the phone when the scheduled rollover has not worked for 3 hours; one private state row; counts only)': DB | JUMP | {"NTFY_TOPIC"},
            'Jira snapshot (counts only; saved to the private database only when live)': DB | JUMP | {"JIRA_API_TOKEN", "JIRA_EMAIL"},
            'Jira card (Daily Report; counts only; the page is written only when live)': DB | JUMP | {"JIRA_API_TOKEN", "JIRA_EMAIL", "NOTION_JIRA_TOKEN"},
            'Jira layout probe (read-only; counts only)': {"JIRA_API_TOKEN", "JIRA_EMAIL"},
            'Jira sprint rollover (dry run unless live; never before the sprint ends)': {"JIRA_API_TOKEN", "JIRA_EMAIL"},
        },
        "outlook": {
            'Outlook newsletters (job alerts to Jobs OS; counts only; writes only when live)': DB | JUMP,
            'Calendar sync (Outlook invites to Google; counts only; writes only when live; pages the phone when it has not worked for 3 hours)': DB | JUMP | {"GCAL_SERVICE_ACCOUNT_JSON", "NTFY_TOPIC"},
        },
        "bills": {
            'Bills Paid processor (before the snapshot; writes Notion only when live; counts only)': DB | JUMP | {"NOTION_BILLS_TOKEN"},
            'Bills snapshot (read-only; private snapshot saved only when live)': DB | JUMP | {"NOTION_BILLS_TOKEN"},
            'Bills card (saved snapshot only; writes only when live; a stale snapshot is shown as STALE, never as current)': DB | JUMP | {"NOTION_JIRA_TOKEN"},
        },
        "agenda": {
            'Agenda snapshot (read-only Google Calendar; private save only when live)': DB | JUMP | {"GCAL_SERVICE_ACCOUNT_JSON"},
            'Agenda card (saved snapshot only; writes only when live)': DB | JUMP | {"NOTION_JIRA_TOKEN"},
        },
        "amazon": {
            'Amazon Orders (Gmail to Notion; archive only after read-back)': {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_AMAZON_TOKEN"},
            'Amazon card (reads the canonical orders; writes only when live; a failed sync is shown as DEGRADED)': {"NOTION_AMAZON_TOKEN", "NOTION_JIRA_TOKEN"},
            'Amazon retention (D139; mail older than 30 days to the Trash once its order is recorded; dry run unless live; counts only)': {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_AMAZON_TOKEN"},
        },
        "mail": {
            'Physical Mail (Gmail read-only to the private state; saved with read-back)': DB | JUMP | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NTFY_TOPIC"},
            'Mail Alerts card (reads the stored state; writes only the Mail Alerts callout, with read-back; a failed ingest is shown as DEGRADED)': DB | JUMP | {"NOTION_JIRA_TOKEN"},
        },
        "hiring": {
            'Hiring Pipeline (Ledger, Notion pages and Calendar to the private state and the floating region; read-back and whole-page proof; a failed source is DEGRADED)': DB | JUMP | {"GCAL_SERVICE_ACCOUNT_JSON", "NOTION_API_TOKEN", "NOTION_INTERVIEW_TOKEN", "NOTION_JIRA_TOKEN"},
        },
        "attention": {
            'Attention (Gmail label and Outlook category to Notion; counts only; writes only when live; a failed source is DEGRADED)': DB | JUMP | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"},
        },
        "megibow": {
            'MegIBOW (Gmail, Outlook and calendars to the Notion table; counts only; writes only when live; a failed source is DEGRADED)': DB | JUMP | {"GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"},
        },
        "dcc": {
            'Attention card (status and suggested decisions above the interactive view; writes only when live; a failed sync is shown as DEGRADED)': {"NOTION_JIRA_TOKEN"},
            'Daily Command Center (rules over the cards\' first lines; writes only its own callout when live; every other region proven unchanged)': {"DCC_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN"},
        },
        "recruiters": {
            'Recruiters (read-only mail to the Recruiters data source; counts only)': DB | JUMP | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_RECRUITERS_TOKEN"},
            "Interview OS (LIFE-88; V7 is the only Interview OS writer - scheduling requests in Gmail and Outlook for jobs Jim applied to, accepted Calendar rounds, and the refresh of machine pages from the Ledger and each page's own identity; a dry run unless INTERVIEW_OS_WRITER is v7)": DB | JUMP | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "GCAL_SERVICE_ACCOUNT_JSON", "NOTION_INTERVIEW_TOKEN", "NOTION_API_TOKEN", "NOTION_RECRUITERS_TOKEN"},      # LIFE-78 E10: + the Recruiters token, read only, for recruiter threads; LIFE-88: the mail, Ledger, Calendar and Interview OS values the stage reads, step-scoped; the Jobs token writes a recruiter lead's Ledger row (E01b)
            "Recruiter Outreach (LIFE-20; the Daily Report's one Recruiter Outreach table from the Recruiters data source, after the Recruiters stage; writes only when live AND the repo variable RECRUITER_OUTREACH is on; read-back and whole-page proof)": {"NOTION_RECRUITERS_TOKEN", "NOTION_JIRA_TOKEN"},      # LIFE-20: reads the Recruiters data source, writes the Daily Report with its integration
            'Job-page outreach (LIFE-10; the outreach table on each Job Ledger job page from Mark as Sent; writes only when live AND the repo variable JOB_PAGE_OUTREACH is on; page proof and read-back)': {"NOTION_API_TOKEN", "NOTION_RECRUITERS_TOKEN"},
            'Hiring outcome mail (D190; read-only Gmail and Outlook for the companies in the Hiring Pipeline, rejection or offer wording only; due every 12 hours; counts only)': DB | JUMP | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN"},
        },
        "report": {
            'Domain jobs report here (the run is already red; this pushes the phone)': {"NTFY_TOPIC"},
        },
    },
    "hourly": {
        "prep": {
            'Gate - skip a scheduled or tick run when another tick started in the last 50 minutes (manual runs do not count)': DB | JUMP | HRL,
            'Stage 1 - sweep': DB | JUMP | HRL,
            'Step 3b - reconcile (repair, manual only)': DB | JUMP | HRL,
            'Step 2 - store check': DB | JUMP | HRL,
            'Stage - extract cards from mail': DB | JUMP | HRL,
        },
        "jobright": {
            'Step 5 - resolve Jobright jobs (free Chromium, saves when live)': DB | JUMP | HRL,
        },
        "linkedin": {
            'Step 5 - resolve LinkedIn jobs (no login, no browser)': DB | JUMP | HRL,
            'Step 5a - LinkedIn job search (guest, after the resolver; the same rows as the job alerts, D203)': DB | JUMP,
        },
        "lensa": {
            'Step 5f - Scale-Up sponsors with no job board - find their jobs by web search (the job keeps its id `lensa`; Lensa itself is retired, D201)': DB | JUMP | HRL,
        },
        "finish": {
            'Jobs tick - the whole chain in one process, fast path first, one store tunnel (LIFE-76 PR 4b, D208)': DB | JUMP | HRL | {"IMESSAGE_ALERTS_HOST"},   # D214: the shadow's Oracle host (unused while V7_CLOUD_FIT is off)
            'Step 5b - US Remote web acquisition (due ATS boards)': DB | JUMP | HRL,
            'Step 5b2 - Employer boards (the Open Jobs board list plus extras; due boards, D202)': DB | JUMP,
            'Employer boards acceptance preview (dispatch only, read-only, counts only, D202)': DB | JUMP | {'FIT_PROFILE_EXTRA_JSON', 'FIT_PROFILE_JSON'},
            'Step 5c - UK Scale-Up web acquisition (scheduled; due sponsor boards)': DB | JUMP | HRL,
            "Step 5d - Scale-Up title watch (roles a company's own site lists but its board listing does not carry; free search, found roles arrive as Review)": DB | JUMP | HRL,
            'Step 5e - UK sponsor register (weekly refresh; forced when dispatched)': DB | JUMP | HRL,
            'Step 6 - enrich RESOLVED jobs (final page - JD, date, liveness)': DB | JUMP | HRL,
            'Step 6a - fit scoring (deterministic, private profile)': DB | JUMP | HRL,
            "Step 6a2 - Cloud Fit backlog batch (LIFE-41 D219; manual, or after each tick when V7_CLOUD_FIT_SCHEDULED is set; one Oracle request at a time)": DB | JUMP | {"FIT_PROFILE_EXTRA_JSON", "FIT_PROFILE_JSON", "NOTION_API_TOKEN", "IMESSAGE_ALERTS_HOST"},   # D219: the store, the Ledger page read for human state, Oracle's host
            'Step 6b - precision audit (before publish)': DB | JUMP | HRL,
            'Step 7 - publish READY jobs to Notion': DB | JUMP | HRL,
            'Step 7a - exclude jobs that have sat on HOLD for 7 days (so their newsletter mail can close)': DB | JUMP | HRL,
            'Step 7b - close out newsletter mail whose jobs all have an outcome': DB | JUMP | HRL,
            'Step 7c - Network Leads blocks on Ledger pages (NET-2.3; 41 pages a run, rotating; a failure never fails the Jobs run but always warns)': DB | JUMP | HRL,
            'Fit review (dispatch only; a private Notion page, counts only in the log)': DB | JUMP | HRL,
            'Explain (dispatch only; report = company names; counts and short hashes only in the log, nothing written)': DB | JUMP | HRL,
            'Board check (dispatch only; report = board:<companies>; one private Notion page, counts only in the log)': DB | JUMP | HRL,
            'Funnel report (dispatch only, read-only, counts only)': DB | JUMP | HRL,
            'Probe - source shapes (dispatch only, counts only)': DB | JUMP | HRL,
            'Step 8 - Last Seen sync (daily)': DB | JUMP | HRL,
            'Step 8b - retention purge (about weekly and catching up (the stage decides when it is due); pushes its own result to the phone, counts and fixed codes only)': DB | JUMP | HRL | {"NTFY_TOPIC"},
            'Alerts - detect, dedupe and push to the phone (counts only; a failure here never fails the run)': DB | JUMP | HRL | {"NTFY_TOPIC"},
        },
        "interview": {
            'Interview shape probe (counts only, writes nothing)': {"NOTION_INTERVIEW_TOKEN"},
            'Interview stage (live only when Actually move mail is ticked)': {"NOTION_INTERVIEW_TOKEN"},
            'Interview private acceptance (manual dispatch only; case comes from the secret)': {"NOTION_INTERVIEW_TOKEN"},
            'Interview private acceptance - negative case (always a dry run; skipped when its secret is empty)': {"NOTION_INTERVIEW_TOKEN"},   # D233
        },
        "report": {
            'Resolver lanes and the finish job report here (the run fails if any lane failed)': {"NTFY_TOPIC"},
        },
    },
    "fit-edge-test": {
        "fit_edge": {
            'Synthetic Fit proof (writes nothing; synthetic content only in the log)': {"IMESSAGE_ALERTS_HOST"},
        },
    },
    "network": {
        "network": {
            'Network (reads the attached CSV through Notion; prints counts only)': DB | JUMP | {"NOTION_API_TOKEN"},
        },
    },
    "outlook": {
        "outlook": {
            'Outlook sign-in (live only)': DB,
            'Outlook newsletters (counts only; saves jobs only when the Save box is ticked)': DB,
            'Outlook probe (read-only; counts only)': DB,
        },
    },
    "profile-sync": {
        "sync": {
            'Fit profile sync (the approved Notion evidence block into the store; counts and one fixed code only)': DB,
        },
    },
    "granola": {
        "granola": {
            'Granola bridge (one pinned meeting; dry run unless live is ticked; counts and fixed codes only)': {"NOTION_INTERVIEW_TOKEN", "IMESSAGE_ALERTS_HOST"},
        },
    },
    "manual-job": {
        "manual": {
            'Manual job (one job into Jobs OS and the Interview OS writer; dry run unless live is ticked; counts and fixed codes only)': DB | {"NOTION_INTERVIEW_TOKEN"},
        },
    },
    "interview-proof": {
        "proof": {
            'Interview journey proof (synthetic mail, Calendar and Ledger; real Notion in a fresh QA sandbox only; refuses unless confirm is QA)': {"NOTION_INTERVIEW_TOKEN"},
        },
    },
    "recruiters": {
        "recruiters": {
            'Recruiters (read-only mail to the Recruiters data source; counts only)': DB | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_RECRUITERS_TOKEN"},
        },
    },
}
CUT_OVER = ("agenda", "bills", "branches", "calendar", "domains", "fit-edge-test", "granola", "hourly", "interview-proof", "manual-job", "network", "outlook", "profile-sync", "recruiters")
PILOTS = ("watchdog", "safety", "amazon", "diag")
SECRET_FREE = ("pr-tests", "lane-guard")         # workflows that read no secret at all, so they have no loader to pin (their own test below pins that)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class BitwardenConsumerTests(unittest.TestCase):
    ACTION = "bitwarden/sm-action@1238aae8fc64b212641190a9227c8a734ab1a793"
    OUTPUT = re.compile(r"\$\{\{ steps\.(\w+)\.outputs\.(\w+) \|\| steps\.(\w+)\.outputs\.(\w+) \}\}")   # D209: the load's output, else its one retry's
    PAUSE = "sleep $((30 + RANDOM % 45))"

    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        cls.text = {n: (cls.root / f"{n}.yml").read_text() for n in CONSUMERS}
        cls.doc = {n: yaml.safe_load(t) for n, t in cls.text.items()}

    def loaders(self, name, job):
        return [(i, s) for i, s in enumerate(self.doc[name]["jobs"][job]["steps"]) if str(s.get("uses", "")).startswith("bitwarden/")]

    def test_every_loader_is_the_pinned_action_with_the_exact_ids_and_nothing_that_could_bypass_it(self):
        for name, want in CONSUMERS.items():
            doc, text = self.doc[name], self.text[name]
            self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", text))), want["direct"], name)             # non-migrated refs + the Bitwarden bootstrap token, nothing else
            self.assertIn("LIFEOS_BWS_RUNTIME_TOKEN", want["direct"], name)
            self.assertFalse([n for n in UUIDS if re.search(rf"secrets\.{n}\b", text)], name)                    # no migrated value is read from GitHub any more
            self.assertEqual(re.findall(r"uses:\s*(bitwarden/\S+)", text), [self.ACTION] * 2 * len(want["jobs"]), name)   # each load and its one retry
            self.assertTrue(set(want["jobs"]) <= set(doc["jobs"]), name)
            for job, names in want["jobs"].items():
                (at, loader), (retry_at, retry) = self.loaders(name, job)
                steps = doc["jobs"][job]["steps"]
                self.assertEqual(steps[0]["uses"], "actions/checkout@v5", (name, job))
                self.assertEqual(loader["uses"], self.ACTION, (name, job))
                self.assertEqual(loader["with"]["access_token"], "${{ secrets.LIFEOS_BWS_RUNTIME_TOKEN }}", (name, job))
                self.assertEqual(sorted(re.findall(r"^([0-9a-f-]{36}) > (\w+)$", loader["with"]["secrets"], re.M)), sorted((UUIDS[n], n) for n in names), (name, job))
                self.assertEqual(len(re.findall(r" > ", loader["with"]["secrets"])), len(names), (name, job))
                self.assertFalse({"if", "env"} & set(loader), (name, job))                                       # no bypass, no fallback source
                self.assertIs(loader.get("continue-on-error"), True, (name, job))                                  # D209: Bitwarden's 429 / 5xx gets one retry ...
                failed = "${{ steps.%s.outcome == 'failure' }}" % loader["id"]
                self.assertEqual(retry_at, at + 2, (name, job))
                self.assertEqual(steps[at + 1], {"name": steps[at + 1]["name"], "if": failed, "run": self.PAUSE}, (name, job))   # ... after a jittered pause, only when the load failed
                self.assertEqual(retry["id"], loader["id"] + "_retry", (name, job))
                self.assertEqual(retry["if"], failed, (name, job))
                self.assertEqual((retry["uses"], retry["with"]), (loader["uses"], loader["with"]), (name, job))   # the same pinned action, the same ids
                self.assertFalse({"continue-on-error", "env"} & set(retry), (name, job))                           # ... and a failed retry fails the job: no fallback
                if name in CUT_OVER:
                    self.assertEqual(loader["id"], f"bws_{job}", (name, job))                                      # deterministic, unique id
                    self.assertIs(loader["with"]["set_env"], False, (name, job))                                  # nothing is exported job-wide
                    self.assertEqual(set(loader), {"id", "name", "continue-on-error", "uses", "with"}, (name, job))
                    self.assertEqual(set(retry), {"id", "name", "if", "uses", "with"}, (name, job))
                    self.assertEqual(set(loader["with"]), {"access_token", "set_env", "secrets"}, (name, job))

    def test_each_consumer_step_receives_exactly_its_pre_cutover_migrated_secrets_and_no_other_step_receives_any(self):
        for name in CUT_OVER:
            doc = self.doc[name]
            self.assertFalse([k for k, v in (doc.get("env") or {}).items() if k in UUIDS], name)                  # nothing at workflow level
            for job, body in doc["jobs"].items():
                self.assertFalse([k for k, v in (body.get("env") or {}).items() if k in UUIDS and v != ""], (name, job))   # nothing at job level (the Interview job's blanks stay)
                found = self.loaders(name, job)
                if not found:                                                           # D244: a job with no loader may receive no migrated value at all
                    self.assertNotIn(job, CONSUMERS[name]["jobs"], (name, job))
                    self.assertFalse([k for step in body["steps"] for k in (step.get("env") or {}) if k in UUIDS], (name, job))
                    self.assertFalse([v for step in body["steps"] for v in (step.get("with") or {}).values() if "steps.bws_" in str(v)], (name, job))
                    continue
                (at, loader), (_, retry) = found
                expected_all = CONSUMER_STEPS.get(name, {}).get(job, {})
                seen = set()
                for i, step in enumerate(body["steps"]):
                    label = step.get("name") or step.get("uses")
                    got = set()
                    for key, value in (step.get("env") or {}).items():
                        if key in UUIDS:
                            m = self.OUTPUT.fullmatch(str(value).strip())
                            self.assertTrue(m and m.group(2) == m.group(4) == key and (m.group(1), m.group(3)) == (loader["id"], retry["id"]), (name, job, label, key))
                            got.add(key)
                            self.assertGreater(i, at, (name, job, label))                                          # the loader precedes every step that consumes its outputs
                    if step is loader or step is retry:
                        continue
                    passed = {k: self.OUTPUT.fullmatch(str(v).strip()) for k, v in (step.get("with") or {}).items()}
                    passed = {k: m for k, m in passed.items() if m}
                    if step.get("uses") == TAILSCALE_ACTION:              # LIFE-31: the pinned join gets exactly its two OAuth values, from this job's loader
                        self.assertEqual({k: (m.group(1), m.group(3), m.group(2), m.group(4)) for k, m in passed.items()}, {k: (loader["id"], retry["id"], n, n) for k, n in TAILSCALE_WITH.items()}, (name, job))
                        self.assertGreater(i, at, (name, job, label))
                        seen |= set(TAILSCALE_WITH.values())
                    else:
                        self.assertEqual(passed, {}, (name, job, label))                                           # no other action receives a loader output
                    self.assertEqual(got, expected_all.get(label, set()), (name, job, label))
                    if "run" not in step or "uses" in step:
                        self.assertEqual(got, set(), (name, job, label))                                           # checkout, setup, actions and installs never get a migrated value
                    seen |= got
                self.assertEqual(seen, CONSUMERS[name]["jobs"][job], (name, job))            # the loader carries exactly what its consumers use

    def test_the_credential_source_change_altered_no_trigger_schedule_permission_concurrency_timeout_or_command(self):
        for name, want in CONSUMERS.items():
            doc = self.doc[name]
            on = doc[True]
            runs = [s["run"] for j in doc["jobs"].values() for s in j["steps"] if "run" in s and s["run"] != self.PAUSE]   # the D209 retry pause is pinned by the loader test
            self.assertEqual(sorted(on), want["on"], name)
            self.assertEqual([c["cron"] for c in on.get("schedule", [])], want["crons"], name)
            self.assertEqual((doc.get("permissions"), doc.get("concurrency")), (want["perms"], want["conc"]), name)
            self.assertEqual({jn: j.get("timeout-minutes") for jn, j in doc["jobs"].items()}, want["timeouts"], name)
            self.assertEqual(hashlib.sha256("\n--\n".join(runs).encode()).hexdigest()[:16], want["digest"], name)   # live/dry-run switches and stage commands included

    def test_only_the_known_workflows_exist_and_the_four_accepted_pilots_are_in_the_table(self):
        self.assertEqual(sorted(p.stem for p in self.root.glob("*.yml")), sorted(list(CONSUMERS) + list(SECRET_FREE)))
        self.assertEqual(sorted(CUT_OVER + PILOTS), sorted(CONSUMERS))


@unittest.skipIf(yaml is None, "PyYAML not installed")
class ProfileSyncWorkflowTests(unittest.TestCase):
    """The Fit profile sync reads the store credentials from Bitwarden like any store job, writes nothing to Bitwarden, and holds one Notion read token for one page."""

    def setUp(self):
        self.root = pathlib.Path(".github/workflows")
        self.text = (self.root / "profile-sync.yml").read_text()
        self.doc = yaml.safe_load(self.text)

    def test_nothing_in_it_can_write_to_bitwarden(self):
        self.assertNotIn("bws secret", self.text)
        self.assertNotIn("sdk-sm/releases", self.text)                         # no CLI is installed: the pinned read-only action is the only Bitwarden contact
        self.assertEqual(re.findall(r"uses:\s*(bitwarden/\S+)", self.text), ["bitwarden/sm-action@1238aae8fc64b212641190a9227c8a734ab1a793"] * 2)

    def test_only_it_can_see_the_notion_sync_token(self):
        for path in self.root.glob("*.yml"):
            if path.stem != "profile-sync":
                self.assertNotIn("NOTION_FIT_SYNC_TOKEN", path.read_text(), path.stem)

    def test_it_has_no_schedule_no_top_level_secret_and_runs_one_stage(self):
        on = self.doc.get("on") or self.doc.get(True)
        self.assertEqual(sorted(on), ["workflow_dispatch", "workflow_run"])
        self.assertNotIn("schedule", self.text.replace("No schedule of its own", ""))
        self.assertNotIn("env", self.doc)
        self.assertEqual(self.text.count("python -m lifeos.run"), 1)
        self.assertIn("python -m lifeos.run fit-profile-sync", self.text)

    def test_it_is_a_dry_run_unless_ticked_or_following_a_tick_with_the_variable_on(self):
        self.assertFalse(self.doc["on"]["workflow_dispatch"]["inputs"]["live"]["default"] if "on" in self.doc else self.doc[True]["workflow_dispatch"]["inputs"]["live"]["default"])
        self.assertIn("vars.FIT_PROFILE_SYNC == 'on'", self.text)
