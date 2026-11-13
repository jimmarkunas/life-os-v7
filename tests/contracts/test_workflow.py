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
        job = data["jobs"]["report"]                                                   # Nov 3-4 2026: prep (unit tests) failed for 21 hours and the report job was skipped, so no push
        self.assertEqual(job["if"], "${{ always() && (needs.prep.result == 'success' || (needs.prep.result == 'failure' && (github.event_name == 'schedule' || (inputs.tick && (inputs.cloud_backlog == '' || inputs.cloud_backlog == '0'))))) }}")   # LIFE-328: an automatic run's failed prep pushes; a gate-cancelled prep is 'cancelled' and pushes nothing
        self.assertIn("prep", job["needs"])
        self.assertIn("prep=${{ needs.prep.result }}", report["run"])                # a failed prep is named in the push

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
            self.assertIn("github.event_name == 'workflow_dispatch' && !inputs.tick && !(inputs.quick || inputs.benchmark)", data["jobs"][lane]["if"], lane)   # a quick proof or a benchmark (LIFE-577) runs the tick path instead
        finish = data["jobs"]["finish"]
        self.assertIn("vars.V7_PIPELINE == 'true'", finish["if"])
        steps = finish["steps"]
        step = next(s for s in steps if s.get("id") == "tick")
        self.assertEqual(step["if"], "${{ env.PIPELINE == 'true' || (github.event_name == 'workflow_dispatch' && (inputs.quick || inputs.benchmark)) }}")   # a manual quick run (or a benchmark, LIFE-577) proves exactly what a tick runs
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
        self.assertIn("needs.gate.outputs.due_jira == 'true'", condition)                # LIFE-604 item 11 / LIFE-609: the trigger and the job's due time are decided once, in the gate job (tests/jobs/test_domains_events.py evaluates it)
        self.assertNotIn("|| inputs.jira != 'none'", condition)

    def test_domain_jobs_left_hourly_and_run_after_a_tick_from_domains(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / ".github/workflows"
        hourly = yaml.safe_load((root / "hourly.yml").read_text())
        domains = yaml.safe_load((root / "domains.yml").read_text())
        for name in ("jira", "outlook", "bills", "agenda", "amazon"):
            self.assertNotIn(name, hourly["jobs"], name)
            self.assertIn(f"needs.gate.outputs.due_{name} == 'true'", domains["jobs"][name]["if"], name)
        self.assertNotIn("jira", hourly[True]["workflow_dispatch"]["inputs"])
        self.assertEqual(domains[True]["workflow_run"]["workflows"], ["hourly"])
        self.assertIn("completed", domains[True]["workflow_run"]["types"])
        self.assertIn("!= 'cancelled'", domains["jobs"]["gate"]["if"])                 # a tick the gate cancelled starts nothing (the gate job decides this once)
        self.assertIn("display_title == 'tick'", domains["jobs"]["gate"]["if"])        # nor does a manual hourly run
        for name in ("agenda", "bills", "imessage", "hiring"):                          # LIFE-613: their writes are checked against their own blocks before they are sent, so none waits for another card
            self.assertEqual(domains["jobs"][name]["needs"], ["gate"], name)
            self.assertIn("always()", domains["jobs"][name]["if"], name)

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
        self.assertEqual(re.findall(r"uses:\s*(\S*bws-load)", text), ["./.github/actions/bws-load"])   # the one local loader (LIFE-631); the reviewed v3.0.1 commit and its one retry (D209) live in that action, pinned by the loader rules below
        mappings = re.findall(r"^\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s*>\s*\w+)\s*$", text, re.M)
        self.assertEqual(sorted(mappings), sorted(["08245c07-728f-438e-940b-b4db015a6967 > GMAIL_OAUTH_CLIENT_SECRET", "672a44e9-235f-4121-a99d-b4db015a6987 > GMAIL_OAUTH_REFRESH_TOKEN",
                                                   "46fe195b-82e2-416e-8272-b4db015eea12 > NOTION_AMAZON_TOKEN"]))
        self.assertEqual(len(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)), 3)                          # each once: the retry reuses the load's block by YAML alias (LIFE-541)
        self.assertLess(text.index("bws-load"), text.index("python -m lifeos.run"))             # loaded before the Amazon command
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
        self.assertEqual((stage["options"], stage["default"]), (["scale-up-holds", "scale-up-links", "scale-up-boards", "scale-up-exclusions", "scale-up-exclusion-review", "scale-up-register", "jobs-backlog", "alerts-open", "newsletter-trace", "notion-sandbox-access", "notion-sandbox-outreach-proof", "interview-ui-probe"], "scale-up-holds"))   # alerts-open arrived on main in #216 (read-only diagnostic); newsletter-trace is LIFE-64's Gmail-reading job; notion-sandbox-access is LIFE-10's Notion-reading job
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
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", text))), ["GMAIL_OAUTH_CLIENT_ID", "LIFEOS_ACQ_SSH_PORT", "LIFEOS_BWS_RUNTIME_TOKEN", "LIFEOS_NOTION_IDS", "NOTION_JOB_LEDGER_DATA_SOURCE_ID"])      # LIFE-121: the report job reads the Ledger's data source id to find its page; the Client ID is the trace job's only Gmail GitHub secret
        for name in moved:
            self.assertNotIn(f"secrets.{name}", text)                                                      # no direct copy, so no fallback
        self.assertEqual(re.findall(r"uses:\s*(\S*bws-load)", text), ["./.github/actions/bws-load"] * 5)   # the one local loader, once per job (LIFE-631)
        head, rest = text.split("\n  # LIFE-64:")                                                           # the database-only diagnostics, then the one job that reads Gmail,
        trace_text, sandbox_text = rest.split("\n  # LIFE-10:")                                              # then the one job that reads Notion
        sandbox_text, ui_text = sandbox_text.split("\n  # LIFE-88:")                                        # then LIFE-88's UI 3c probe, the one job with the Interview token
        ui_text, report_text = ui_text.split("\n  # LIFE-121:")                                            # then LIFE-121's private reports: the database values and the Jobs Notion token, the one job that writes the report page
        uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        gmail = {"GMAIL_OAUTH_CLIENT_SECRET": "08245c07-728f-438e-940b-b4db015a6967", "GMAIL_OAUTH_REFRESH_TOKEN": "672a44e9-235f-4121-a99d-b4db015a6987"}
        self.assertEqual(sorted(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", head, re.M)), sorted([f"{uuid_} > {name}" for name, uuid_ in moved.items()]))
        self.assertEqual(sorted(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", trace_text, re.M)), sorted([f"{uuid_} > {name}" for name, uuid_ in {**moved, **gmail}.items()]))
        self.assertEqual((len(re.findall(uuid, head)), len(re.findall(uuid, trace_text))), (7, 9))                      # each once: the retry reuses the load's block by YAML alias (LIFE-541; D209 one retry)
        self.assertNotIn("GMAIL", head)                                                                    # no Gmail value or secret in the database-only job
        self.assertEqual(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", sandbox_text, re.M), ["53dc3d04-dbb2-45fe-8185-b4db015eedcb > NOTION_API_TOKEN"])   # LIFE-10: the Jobs Notion token alone (the retry is its alias)
        self.assertEqual(len(re.findall(uuid, sandbox_text)), 1)
        self.assertNotIn("NOTION", head + trace_text)                                                      # no Notion value reaches the database or Gmail diagnostics
        for word in ("LIFEOS_ACQ", "GMAIL", "inputs.message_ref", "secrets.NOTION"):
            self.assertNotIn(word, sandbox_text)                                                           # nothing else reaches the Notion job, and it takes no target input
        self.assertLess(text.index("bws-load"), text.index("python -m lifeos.run"))             # loaded before the diagnostic command
        trace = diag["jobs"]["trace"]
        sandbox = diag["jobs"]["sandbox"]
        self.assertEqual((job["if"], trace["if"], sandbox["if"]),
                         ("${{ inputs.stage != 'scale-up-exclusion-review' && inputs.stage != 'scale-up-register' && inputs.stage != 'jobs-backlog' && inputs.stage != 'newsletter-trace' && inputs.stage != 'notion-sandbox-access' && inputs.stage != 'notion-sandbox-outreach-proof' && inputs.stage != 'interview-ui-probe' }}",
                          "${{ inputs.stage == 'newsletter-trace' }}",
                          "${{ inputs.stage == 'notion-sandbox-access' || inputs.stage == 'notion-sandbox-outreach-proof' }}"))   # exactly one of the three jobs runs
        self.assertEqual(sandbox["timeout-minutes"], 5)
        token = "${{ steps.bws_sandbox.outputs.NOTION_API_TOKEN }}"
        read, proof = sandbox["steps"][-2:]
        self.assertEqual((read["if"], read["run"], read["env"]), ("${{ inputs.stage == 'notion-sandbox-access' }}", "python -m lifeos.run notion-sandbox-access --limit 0", {"NOTION_API_TOKEN": token, "LIFEOS_NOTION_IDS": "${{ secrets.LIFEOS_NOTION_IDS }}"}))
        self.assertEqual((proof["if"], proof["run"], proof["env"]), ("${{ inputs.stage == 'notion-sandbox-outreach-proof' }}", "python -m lifeos.run notion-sandbox-outreach-proof --limit 0 --live",
                                                                    {"NOTION_API_TOKEN": token, "PROOF_CONFIRM": "${{ inputs.confirm }}", "LIFEOS_NOTION_IDS": "${{ secrets.LIFEOS_NOTION_IDS }}"}))   # the only write step; the stage itself refuses without SYNTHETIC
        self.assertEqual([s.get("env") for s in sandbox["steps"][:-2]], [None] * (len(sandbox["steps"]) - 2))   # only the two stage steps receive the token
        self.assertIs(next(s for s in sandbox["steps"] if s.get("id") == "bws_sandbox")["with"]["set_env"], False)                                       # a masked step output, never exported job-wide
        report = diag["jobs"]["report"]                                                                    # LIFE-121: private reports to one Notion page; a dry run unless confirm = REPORT
        self.assertEqual((report["if"], report["timeout-minutes"]), ("${{ inputs.stage == 'scale-up-exclusion-review' || inputs.stage == 'scale-up-register' || inputs.stage == 'jobs-backlog' }}", 10))
        self.assertEqual(sorted(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", report_text, re.M)), sorted([f"{uuid_} > {name}" for name, uuid_ in {**moved, "NOTION_API_TOKEN": "53dc3d04-dbb2-45fe-8185-b4db015eedcb"}.items()]))
        self.assertEqual(report["steps"][-1]["env"], {"LIFEOS_ACQ_SSH_PORT": "${{ secrets.LIFEOS_ACQ_SSH_PORT }}", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "${{ secrets.NOTION_JOB_LEDGER_DATA_SOURCE_ID }}",
                                                      "STAGE": "${{ inputs.stage }}", "REPORT_CONFIRM": "${{ inputs.confirm }}"})
        self.assertEqual(report["steps"][-1]["run"].count("--live"), 1)                                    # the only write path, behind the typed REPORT confirmation
        self.assertIn('[ "$REPORT_CONFIRM" = "REPORT" ]', report["steps"][-1]["run"])
        self.assertNotIn("GMAIL", report_text)
        ui = diag["jobs"]["interview_ui"]                                                                  # LIFE-88: the one job that holds the Interview token
        self.assertEqual((ui["if"], ui["timeout-minutes"]), ("${{ inputs.stage == 'interview-ui-probe' }}", 5))
        self.assertEqual(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", ui_text, re.M), ["092b44ae-32cb-453b-8cc2-b4db015ef571 > NOTION_INTERVIEW_TOKEN"])
        for word in ("LIFEOS_ACQ", "GMAIL", "inputs.message_ref", "secrets.NOTION", "NOTION_API_TOKEN"):
            self.assertNotIn(word, ui_text)                                                                # nothing else reaches the probe, and it takes no target input
        self.assertNotIn("NOTION_INTERVIEW_TOKEN", head + trace_text + sandbox_text)
        probe = ui["steps"][-1]
        self.assertEqual((probe["run"], probe["env"]), ("python -m lifeos.run interview-ui-probe --limit 0 --live", {
            "NOTION_INTERVIEW_TOKEN": "${{ steps.bws_interview.outputs.NOTION_INTERVIEW_TOKEN }}",
            "PROBE_CONFIRM": "${{ inputs.confirm }}", "LIFEOS_NOTION_IDS": "${{ secrets.LIFEOS_NOTION_IDS }}"}))                                                    # the stage itself refuses without QA
        self.assertEqual([s.get("env") for s in ui["steps"][:-1]], [None] * (len(ui["steps"]) - 1))
        self.assertEqual(trace["timeout-minutes"], 10)
        self.assertEqual(trace["steps"][-1]["run"], 'python -m lifeos.run "$STAGE"')
        self.assertEqual(trace["steps"][-1]["env"], {"LIFEOS_ACQ_SSH_PORT": "${{ secrets.LIFEOS_ACQ_SSH_PORT }}", "GMAIL_OAUTH_CLIENT_ID": "${{ secrets.GMAIL_OAUTH_CLIENT_ID }}", "STAGE": "${{ inputs.stage }}", "ALERTS_MESSAGE_REF": "${{ inputs.message_ref }}"})


@unittest.skipIf(yaml is None, "PyYAML not installed")
class PullRequestTestsWorkflow(unittest.TestCase):
    """pr-tests.yml is the secret-free pull request check (and, since Oct 25 2026, the check of every merge to main): read-only, no secret, no loader, no database, the one accepted test command and the 45 s / 90 s suite limits (90 since Jim, Nov 9 2026)."""
    path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pr-tests.yml"

    def test_it_runs_on_pull_requests_and_main_only_read_only_and_holds_no_secret(self):
        text, doc = self.path.read_text(), yaml.safe_load(self.path.read_text())
        self.assertEqual(list(doc[True]), ["pull_request", "push"])
        self.assertEqual(doc[True]["push"], {"branches": ["main"]})                  # main only: a merge gives the Edge release a tested commit; no other branch, tag, schedule or dispatch
        self.assertEqual(doc["permissions"], {"contents": "read"})
        for word in ("secrets.", "bitwarden", "LIFEOS_", "NOTION_", "GMAIL_", "--live"):
            self.assertNotIn(word, text)
        self.assertEqual([j for j in doc["jobs"]], ["unit-tests"])

    def evaluate(self, expression, context):
        """Just enough of the Actions expression language for this file: ${{ a || b }}, == and string quotes. Unknown names read as empty, as in Actions."""
        import re

        def value(token):
            token = token.strip()
            if token.startswith("'"):
                return token.strip("'")
            node = context
            for part in token.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            return node or ""

        def one(match):
            body = match.group(1)
            if "==" in body:
                left, right = body.split("==")
                return str(value(left) == value(right)).lower()
            for token in body.split("||"):
                if value(token):
                    return str(value(token))
            return ""
        return re.sub(r"\$\{\{(.*?)\}\}", one, str(expression))

    def test_a_merge_to_main_never_cancels_the_previous_merges_check_but_a_pull_request_push_still_replaces_its_older_one(self):
        """LIFE-604 item 9: two quick merges used to share one group (the branch name) and the second cancelled the first, so the first merge had no verdict (2 of the last 25 main runs)."""
        concurrency = yaml.safe_load(self.path.read_text())["concurrency"]

        def settings(name, **context):
            ctx = {"github": {"event_name": name, **context}}
            return self.evaluate(concurrency["group"], ctx), self.evaluate(concurrency["cancel-in-progress"], ctx)
        first = settings("push", ref="refs/heads/main", sha="aaa", event={})
        second = settings("push", ref="refs/heads/main", sha="bbb", event={})
        self.assertNotEqual(first[0], second[0])                                       # two merges, two groups: neither waits for or replaces the other
        self.assertEqual((first[1], second[1]), ("false", "false"))
        pr_one = settings("pull_request", ref="refs/pull/7/merge", sha="ccc", event={"pull_request": {"number": 7}})
        pr_two = settings("pull_request", ref="refs/pull/7/merge", sha="ddd", event={"pull_request": {"number": 7}})
        self.assertEqual((pr_one[0], pr_one[1]), (pr_two[0], "true"))                  # a newer push to the same pull request replaces the older check
        other_pr = settings("pull_request", ref="refs/pull/8/merge", sha="eee", event={"pull_request": {"number": 8}})
        self.assertNotEqual(pr_one[0], other_pr[0])

    def test_it_reuses_the_shared_python_action_and_runs_the_accepted_command_with_the_runtime_limits(self):
        doc = yaml.safe_load(self.path.read_text())
        steps = doc["jobs"]["unit-tests"]["steps"]
        self.assertEqual([s.get("uses") for s in steps[:2]], ["actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09", "./.github/actions/python"])
        run = [s["run"] for s in steps if "Unit tests" in s.get("name", "")][0]
        self.assertIn("python -m tests.shard 1 2", run)                      # Jim, Oct 26 2026: two parallel shards of whole test modules; tests/contracts/test_shards.py proves every test runs in exactly one
        self.assertIn("python -m tests.shard 2 2", run)
        self.assertNotIn("unittest discover", run)
        self.assertRegex(run, r'-ge 120 \]; then .*exit 1')
        self.assertRegex(run, r'-ge 90 \]; then echo "::warning::')

    def test_it_also_runs_the_concord_room_plugin_tests_after_the_python_suite_with_no_secret_or_install(self):
        """Jim, Nov 8 2026: the Concord Room Orca plugin is production, so its node:test file runs in this same required check (the named test file, not the directory form, which fails on Node 24)."""
        doc = yaml.safe_load(self.path.read_text())
        steps = doc["jobs"]["unit-tests"]["steps"]
        names = [s.get("name", "") for s in steps]
        python_suite = next(i for i, n in enumerate(names) if "Unit tests" in n)
        room = next(i for i, n in enumerate(names) if "Concord Room plugin tests" in n)
        self.assertGreater(room, python_suite)                                       # the 45 s / 90 s limits above stay about the Python suite alone
        self.assertEqual(steps[room]["run"], "node --test tools/orca-concord-room/test/worker.test.mjs")
        self.assertEqual(steps[room - 1]["uses"], "actions/setup-node@a0853c24544627f65ddf259abe73b1d18a591444")
        self.assertEqual(steps[room - 1]["with"], {"node-version": "24"})
        self.assertTrue(pathlib.Path(__file__).resolve().parents[2].joinpath("tools/orca-concord-room/test/worker.test.mjs").is_file())


class AmazonRetentionOffTheChainTests(unittest.TestCase):
    """LIFE-571: Amazon retention (about 240 s) no longer sits inside the amazon job, which is on the serial page-writer chain. Nothing else about it changed."""
    @classmethod
    def setUpClass(cls):
        try:
            cls.jobs = yaml.safe_load(pathlib.Path(__file__).resolve().parents[2].joinpath(".github/workflows/domains.yml").read_text())["jobs"]
        except NameError:
            raise unittest.SkipTest("pyyaml unavailable") from None

    def test_retention_is_its_own_job_that_starts_after_the_amazon_job_and_nothing_waits_for_it_but_the_report(self):
        retention = self.jobs["amazon_retention"]
        self.assertEqual(retention["needs"], ["amazon", "gate"])
        self.assertEqual(retention["timeout-minutes"], 15)
        self.assertEqual(retention["if"].replace("due_amazon_retention", "due_amazon"), self.jobs["amazon"]["if"])      # runs for exactly the same triggers as the amazon job (each reads its own due output, LIFE-609)
        waiting = sorted(name for name, job in self.jobs.items() if "amazon_retention" in (job.get("needs") or []))
        self.assertEqual(waiting, ["bench_report", "report"])                            # no page writer waits for it (the benchmark summary job and the failure-push job only watch)

    def test_the_amazon_job_no_longer_runs_retention_and_the_command_is_unchanged_in_the_new_job(self):
        self.assertNotIn("aretain", [s.get("id") for s in self.jobs["amazon"]["steps"]])
        step = next(s for s in self.jobs["amazon_retention"]["steps"] if s.get("id") == "aretain")
        self.assertEqual(step["run"], 'python -m lifeos.run amazon-retention --limit 200 $([ "$RETENTION_LIVE" = "true" ] && echo --live)')
        self.assertEqual(step["env"]["RETENTION_LIVE"], "true")
        self.assertIs(step["continue-on-error"], True)                                    # a failure is a warning only, as before
        self.assertNotIn("always()", step.get("if", ""))                                  # the job's own steps run in order; the job-level if decides the triggers

    def test_a_retention_failure_is_named_in_the_report_and_warns_only(self):
        report = self.jobs["report"]["steps"][-1]["run"]
        self.assertIn("amazon_retention=${{ needs.amazon_retention.result }}", report)
        warn = next(s for s in self.jobs["amazon_retention"]["steps"] if "warning" in s.get("name", ""))
        self.assertEqual(warn["if"], "${{ always() && steps.aretain.outcome == 'failure' }}")


class UnitTestGateTests(unittest.TestCase):
    """LIFE-570: prep skips the unit tests only through the fail-closed gate; the cheap freshness check and every other step are unchanged."""
    @classmethod
    def setUpClass(cls):
        try:
            cls.doc = yaml.safe_load(pathlib.Path(__file__).resolve().parents[2].joinpath(".github/workflows/hourly.yml").read_text())
        except NameError:
            raise unittest.SkipTest("pyyaml unavailable") from None

    def steps(self):
        return {s.get("name", ""): s for s in self.doc["jobs"]["prep"]["steps"]}

    def test_the_gate_runs_first_and_only_it_can_skip_the_tests(self):
        steps = self.doc["jobs"]["prep"]["steps"]
        names = [s.get("name", "") for s in steps]
        gate = next(i for i, n in enumerate(names) if n.startswith("Unit-test gate"))
        tests = next(i for i, n in enumerate(names) if n == "Unit tests")
        self.assertLess(gate, tests)
        self.assertEqual(steps[gate]["id"], "testgate")
        self.assertEqual(steps[gate]["run"], "python -m lifeos.platform.testgate")
        self.assertEqual(steps[tests]["if"], "${{ steps.testgate.outputs.skip != 'true' }}")            # anything but the gate's own "true" runs the tests
        self.assertEqual(steps[tests]["run"], "python -m unittest discover -s tests -t . -v")
        self.assertEqual(steps[tests]["env"], {"LIFEOS_TEST_INDEX_FRESH": "1"})

    def test_the_freshness_check_still_runs_when_the_suite_is_skipped(self):
        step = next(s for n, s in self.steps().items() if n.startswith("Test index freshness"))
        self.assertEqual(step["if"], "${{ steps.testgate.outputs.skip == 'true' }}")
        self.assertEqual((step["env"], step["run"]), ({"LIFEOS_TEST_INDEX_FRESH": "1"}, "python -m unittest tests.test_testindex.CommittedIndexIsFresh -v"))

    def test_the_gate_reads_the_token_the_job_already_has_and_pr_tests_is_untouched(self):
        gate = next(s for n, s in self.steps().items() if n.startswith("Unit-test gate"))
        self.assertEqual(gate["env"], {"GITHUB_TOKEN": "${{ github.token }}"})
        self.assertEqual(self.doc["jobs"]["prep"]["permissions"].get("actions"), "write")
        pr = pathlib.Path(__file__).resolve().parents[2].joinpath(".github/workflows/pr-tests.yml").read_text()
        self.assertIn("unit-tests:", pr)                                                              # the required check name is unchanged

    def test_chromium_is_cached_on_the_browser_lock_and_the_browser_step_is_its_own_timed_step(self):
        """LIFE-604 item 13: ONE shared composite action installs the browser; the finish job and the manual Jobright job both use it."""
        import yaml
        root = pathlib.Path(__file__).resolve().parents[2]
        steps = self.doc["jobs"]["finish"]["steps"]
        ids = [s.get("id") for s in steps]
        browser = next(s for s in steps if s.get("id") == "browser")
        tick = next(s for s in steps if s.get("id") == "tick")
        self.assertEqual(browser["uses"], "./.github/actions/browser")
        self.assertEqual(browser["with"], {"cache_bust": "${{ vars.BROWSER_CACHE_BUST }}"})           # LIFE-573: one variable forces a cold-cache run
        self.assertLess(ids.index("browser"), ids.index("tick"))                                      # its own step, before the tick, so its seconds show in the run log
        self.assertEqual(browser["if"], tick["if"])
        self.assertNotIn("playwright", tick["run"])                                                   # the tick step no longer installs anything
        self.assertNotIn("pip install", tick["run"])
        jobright = self.doc["jobs"]["jobright"]["steps"]
        shared = next(s for s in jobright if s.get("id") == "jobright_browser")
        self.assertEqual((shared["uses"], shared["with"]), (browser["uses"], browser["with"]))        # both jobs run the same step
        resolve = next(s for s in jobright if s.get("id") == "jobright")
        self.assertLess([s.get("id") for s in jobright].index("jobright_browser"), [s.get("id") for s in jobright].index("jobright"))
        self.assertNotIn("playwright install", resolve["run"])                                        # no second install path left in the workflow
        self.assertNotIn("playwright install", (root / ".github/workflows/hourly.yml").read_text())
        action = yaml.safe_load((root / ".github/actions/browser/action.yml").read_text())
        inner = action["runs"]["steps"]
        inner_ids = [s.get("id") for s in inner]
        cache = inner[inner_ids.index("chromium_cache")]
        self.assertEqual((cache["uses"], cache["with"]["path"]), ("actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830", "~/.cache/ms-playwright"))
        self.assertIn("hashFiles('requirements-browser.lock')", cache["with"]["key"])
        self.assertIn("inputs.cache_bust", cache["with"]["key"])
        libs = inner[inner_ids.index("libs_cache")]                                                   # LIFE-576: the system libraries are cached next to Chromium
        self.assertEqual((libs["uses"], libs["with"]["path"]), ("actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830", "~/apt-debs"))
        self.assertIn("steps.runner_image.outputs.version", libs["with"]["key"])                      # a restored copy can never meet a different runner image
        self.assertIn("hashFiles('requirements-browser.lock')", libs["with"]["key"])
        self.assertLess(inner_ids.index("runner_image"), inner_ids.index("libs_cache"))
        self.assertLess(inner_ids.index("libs_cache"), inner_ids.index("browser"))
        inner_browser = inner[inner_ids.index("browser")]
        self.assertEqual(inner_browser["env"], {"CHROMIUM_CACHE_HIT": "${{ steps.chromium_cache.outputs.cache-hit }}", "LIBS_CACHE_HIT": "${{ steps.libs_cache.outputs.cache-hit }}"})
        run = inner_browser["run"]
        self.assertIn("pip install --require-hashes -r requirements-browser.lock", run)               # integrity unchanged
        self.assertNotIn("while ", run)                                                               # bounded: three tries, never an unbounded loop
        self.assertNotIn("until ", run)
        self.assertNotIn("continue-on-error", inner_browser)                                          # a used-up retry fails the run
        self.assertLess(run.index("BROWSER_INSTALL_SKIPPED_APT"), run.index("--with-deps"))


class BrowserStepBehaviourTests(unittest.TestCase):
    """LIFE-573: the real browser step script, run in bash with fake python/timeout/sleep, shows what a hit, a miss, a retry and a used-up retry do."""
    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest("pyyaml unavailable") from None
        path = pathlib.Path(__file__).resolve().parents[2] / ".github/actions/browser/action.yml"          # LIFE-604 item 13: the one shared step
        steps = yaml.safe_load(path.read_text())["runs"]["steps"]
        cls.script = next(s for s in steps if s.get("id") == "browser")["run"]

    def run_step(self, hit, fail_first, libs_hit="false", saved=None, dpkg_fails=False, download_fails=False):
        """saved: None = no saved libraries, else the list of file names in the restored folder (a manifest is written with them)."""
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp, "calls.log")
            if saved is not None:
                debs = pathlib.Path(tmp, "apt-debs")
                debs.mkdir()
                debs.joinpath("manifest.txt").write_text("libx=1\n")
                for name in saved:
                    debs.joinpath(name).write_text("deb")
            harness = (
                "set -eo pipefail\n"
                f'LOG="{log}"; FAILS={fail_first}; COUNT=0\n'
                'pip(){ echo "pip $*" >> "$LOG"; }\n'
                'timeout(){ shift; "$@"; }\n'
                'sleep(){ echo "sleep $1" >> "$LOG"; }\n'
                f'sudo(){{ "$@"; }}\ndpkg(){{ echo "dpkg $*" >> "$LOG"; {"return 1" if dpkg_fails else "return 0"}; }}\n'
                'dpkg-query(){ if [ -e "$HOME/after" ]; then printf "libx=1\\nliby=2\\n"; else printf "liby=2\\n"; touch "$HOME/after"; fi; }\n'
                f'apt-get(){{ echo "apt-get $*" >> "$LOG"; {"return 100" if download_fails else "touch libx.deb"}; }}\n'
                'python(){ echo "python $*" >> "$LOG"; case "$*" in *--with-deps*) COUNT=$((COUNT+1)); '
                '[ "$COUNT" -le "$FAILS" ] && { echo "apt could not resolve the mirror" >&2; return 100; };; esac; return 0; }\n'
                + self.script)
            done = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                                  env={"CHROMIUM_CACHE_HIT": hit, "LIBS_CACHE_HIT": libs_hit, "HOME": tmp, "PATH": "/usr/bin:/bin"})
            self.debs_left = sorted(p.name for p in pathlib.Path(tmp, "apt-debs").glob("*")) if pathlib.Path(tmp, "apt-debs").exists() else None
            return done.returncode, done.stdout, log.read_text().splitlines()

    def test_a_cache_hit_runs_no_apt_command(self):
        code, out, calls = self.run_step("true", 99)
        self.assertEqual(code, 0)
        self.assertIn("BROWSER_INSTALL_SKIPPED_APT", out)
        self.assertIn("BROWSER_INSTALL_PATH=cache-hit", out)
        self.assertFalse([c for c in calls if "--with-deps" in c or c.startswith(("apt-get", "dpkg"))])

    def test_a_cold_run_installs_with_apt_once_and_saves_the_libraries(self):
        code, out, calls = self.run_step("false", 0)
        self.assertEqual((code, len([c for c in calls if "--with-deps" in c])), (0, 1))
        self.assertIn("BROWSER_INSTALL_PATH=apt-cold", out)
        self.assertIn("BROWSER_LIBS_SAVED: 1 packages", out)
        self.assertEqual(self.debs_left, ["libx.deb", "manifest.txt"])
        self.assertIn("apt-get download libx=1", calls)                                              # only the packages the install added

    def test_a_failed_apt_download_is_retried_with_backoff_and_then_succeeds(self):
        code, out, calls = self.run_step("false", 2)
        self.assertEqual(code, 0)
        self.assertEqual(len([c for c in calls if "--with-deps" in c]), 3)
        self.assertEqual([c for c in calls if c.startswith("sleep")], ["sleep 20", "sleep 40"])
        self.assertIn("BROWSER_INSTALL_ATTEMPT_1_FAILED", out)
        self.assertIn("BROWSER_INSTALL_ATTEMPT_2_FAILED", out)

    def test_three_failed_tries_fail_the_step_after_exactly_three_attempts(self):
        code, out, calls = self.run_step("false", 99)
        self.assertNotEqual(code, 0)
        self.assertEqual(len([c for c in calls if "--with-deps" in c]), 3)
        self.assertIn("BROWSER_INSTALL_FAILED", out)

    def test_saved_libraries_install_without_any_apt_command(self):
        code, out, calls = self.run_step("false", 99, libs_hit="true", saved=["libx.deb"])
        self.assertEqual(code, 0)
        self.assertIn("BROWSER_INSTALL_PATH=libs-cache", out)
        self.assertFalse([c for c in calls if "--with-deps" in c or c.startswith("apt-get")])
        self.assertTrue([c for c in calls if c.startswith("dpkg -i")])
        self.assertIn("python -m playwright install chromium", calls)

    def test_an_invalid_saved_copy_falls_back_to_apt_and_passes(self):
        code, out, calls = self.run_step("false", 0, libs_hit="true", saved=["libx.deb"], dpkg_fails=True)
        self.assertEqual(code, 0)
        self.assertIn("BROWSER_LIBS_CACHE_FAILED", out)
        self.assertIn("BROWSER_INSTALL_PATH=apt-fallback", out)
        self.assertEqual(len([c for c in calls if "--with-deps" in c]), 1)

    def test_a_hit_with_no_manifest_is_treated_as_missing(self):
        code, out, calls = self.run_step("false", 0, libs_hit="true", saved=None)
        self.assertEqual(code, 0)
        self.assertIn("BROWSER_INSTALL_PATH=apt-fallback", out)

    def test_a_fallback_that_also_fails_three_times_still_fails_no_harder_than_before(self):
        code, out, _ = self.run_step("false", 99, libs_hit="true", saved=["libx.deb"], dpkg_fails=True)
        self.assertNotEqual(code, 0)
        self.assertIn("BROWSER_INSTALL_FAILED", out)

    def test_a_failed_save_never_fails_the_step(self):
        code, out, _ = self.run_step("false", 0, download_fails=True)
        self.assertEqual(code, 0)
        self.assertIn("BROWSER_LIBS_SAVE_FAILED", out)
        self.assertIsNone(self.debs_left)                                                             # nothing half-saved is left to be cached


class FastPathFirstTests(unittest.TestCase):
    """D206 (LIFE-76 PR 1): the Ledger never waits on bulk discovery, and a quick proof run reads few boards."""
    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest("PyYAML not installed") from None
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
    "granola": DB | {"LIFEOS_ACQ_SSH_PORT", "NOTION_INTERVIEW_TOKEN", "HIRING_PIPELINE_PAGE_ID", "IMESSAGE_ALERTS_HOST", "NOTION_JOB_LEDGER_DATA_SOURCE_ID"},   # LIFE-360: the readers take opportunities from Ledger rows first;   # LIFE-122: the automatic Granola bridge; the Tailscale OAuth pair goes only to the pinned join action (see TAILSCALE_WITH)
    "report": {"NTFY_TOPIC"},   # the failure-push job: the phone topic and nothing else
    "gate": set(), "bench_report": set(),   # LIFE-577 / LIFE-604 item 11: the gate and summary jobs hold no secret (the run's own token only)
    "imessage": DB | {"LIFEOS_ACQ_SSH_PORT", "NTFY_TOPIC", "IMESSAGE_ALERTS_HOST", "NOTION_JIRA_TOKEN", "AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID",
                  "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID"},   # LIFE-34 Communications card (Notion writer + region anchors). LIFE-31: the Tailscale OAuth pair goes only to the pinned join action (see TAILSCALE_WITH)
    "jira": DB | {"JIRA_API_TOKEN", "JIRA_BASE_URL", "JIRA_BOARDS", "JIRA_CARD_BLOCK_ID", "JIRA_CARD_PROJECTS", "JIRA_EMAIL",
                  "JIRA_GTV_CONTEXT_URL", "JIRA_GTV_EPIC", "JIRA_SITE_URL", "NOTION_JIRA_TOKEN", "NTFY_TOPIC",
                  "MEGIBOW_BLOCK_ID"},                                       # JFM-221: the Jim & Matt dashboard reads Jim's MegIBOW week from that table
    "outlook": DB | {"GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "NTFY_TOPIC", "OUTLOOK_CLIENT_ID"},
    "bills": DB | {"BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_BILLS_DATA_SOURCE_ID", "NOTION_BILLS_TOKEN", "NOTION_JIRA_TOKEN"},
    "agenda": DB | {"CALENDAR_CARD_BLOCK_ID", "GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN"},
    "attention": DB | {"GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_JIRA_TOKEN", "NTFY_TOPIC", "OUTLOOK_CLIENT_ID"},
    "megibow": DB | {"GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "MEGIBOW_BLOCK_ID", "MEGIBOW_EXCLUDED_DOMAINS", "MEGIBOW_REVIEW_DB_ID", "NOTION_JIRA_TOKEN", "NTFY_TOPIC", "OUTLOOK_CLIENT_ID"},
    "dcc": {"ATTENTION_SYNC_OUTCOME", "AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "DCC_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "MAIL_ALERTS_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN"},
    "recruiters": DB | {"GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_RECRUITERS_DATA_SOURCE_ID", "NOTION_RECRUITERS_TOKEN", "OUTLOOK_CLIENT_ID",
                     "NOTION_API_TOKEN", "NOTION_JOB_LEDGER_DATA_SOURCE_ID", "V7_CLOSE_BASE_URL", "RECRUITERS_SCAN", "RECRUITERS_SCAN_AT",      # LIFE-610: step outputs, not secrets
                     "GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "HIRING_PIPELINE_PAGE_ID", "NOTION_INTERVIEW_TOKEN",
                     "HIRING_TABLE_BLOCK_ID", "NOTION_JIRA_TOKEN"},   # LIFE-20: the Recruiter Outreach step (the Daily Report writer and the pinned Hiring Pipeline table it is anchored on); LIFE-10: the job-page outreach step reads and writes Job Ledger pages; LIFE-88: the Interview OS step
    "mail": DB | {"AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET",
                  "GMAIL_OAUTH_REFRESH_TOKEN", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "MAIL_ALERTS_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN", "NTFY_TOPIC"},
    "hiring": DB | {"GCAL_CALENDAR_ID", "GCAL_SERVICE_ACCOUNT_JSON", "HIRING_CARD_BLOCK_ID", "HIRING_PIPELINE_PAGE_ID", "HIRING_STATUS_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_API_TOKEN",
                    "NOTION_INTERVIEW_TOKEN", "NOTION_JIRA_TOKEN", "NOTION_JOB_LEDGER_DATA_SOURCE_ID", "V7_CLOSE_BASE_URL"},
    "amazon": {"AMAZON_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET",
               "GMAIL_OAUTH_REFRESH_TOKEN", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_AMAZON_DATA_SOURCE_ID", "NOTION_AMAZON_TOKEN", "NOTION_JIRA_TOKEN"},
    "amazon_retention": {"GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_AMAZON_DATA_SOURCE_ID", "NOTION_AMAZON_TOKEN"},   # LIFE-571: retention in its own job
}
for _job in ("jira", "attention", "megibow", "dcc", "recruiters", "granola"):      # LIFE-338: the ids secret reaches only the steps that run Notion-id code
    DOMAIN_SECRETS[_job] = DOMAIN_SECRETS[_job] | {"LIFEOS_NOTION_IDS"}


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
        self.assertEqual(self.doc["jobs"]["granola"]["concurrency"], writer)                  # LIFE-122: the automatic Granola run is also an Interview OS writer: it takes the same turn
        self.assertEqual([name for name, job in self.doc["jobs"].items() if "concurrency" in job], ["recruiters", "granola"])

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
            if name in ("report", "gate", "bench_report"):                                                   # the failure-push job (see the next test) is the alarm, not a domain stage; the benchmark jobs (LIFE-577) only check and summarise
                continue
            work = [s for s in job["steps"] if s.get("id") and not str(s.get("uses", "")).endswith("bws-load")]   # the Bitwarden loader is deliberately fail-closed: a failed load fails the job before its command
            self.assertTrue(work and all(s.get("continue-on-error") for s in work), name)

    def test_the_report_job_pushes_the_phone_only_for_a_failed_tick_triggered_run_and_writes_nothing(self):
        job = self.doc["jobs"]["report"]
        self.assertEqual(set(job["needs"]), set(self.doc["jobs"]) - {"report", "bench_report"})                                    # it watches every domain job and the gate (whose `tick` output it reads)
        condition = job["if"]
        for part in ("always()", "needs.gate.outputs.tick == 'true'", "contains(needs.*.result, 'failure')"):
            self.assertIn(part, condition)
        (step,) = [s for s in job["steps"] if "run" in s]
        self.assertIn("lifeos.platform.alerts push --key domain_jobs_failed", step["run"])
        self.assertFalse(step.get("continue-on-error"))                                                           # a push the provider refuses turns the job red
        self.assertEqual(sorted(re.findall(r"needs\.(\w+)\.result", step["run"])), sorted(n for n in job["needs"] if n != "gate"))         # every domain job is named in the failed list (the gate is not a domain job)


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
# Least privilege: the credentials each job may load from Bitwarden (a conscious decision; domains.yml jobs are held to DOMAIN_SECRETS instead). Adding one is the only workflow edit that needs a test edit.
LOADED = {
    "branches": {"report": {"NOTION_JIRA_TOKEN"}},
    "agenda": {"agenda": DB | {"GCAL_SERVICE_ACCOUNT_JSON", "NOTION_JIRA_TOKEN"}},
    "bills": {"bills": DB | {"NOTION_BILLS_TOKEN"}},
    "calendar": {"calendar": DB | {"GCAL_SERVICE_ACCOUNT_JSON"}},
    "hourly": {"prep": DB | JUMP | TS | HRL, "jobright": DB | JUMP | TS | HRL, "linkedin": DB | JUMP | TS | HRL, "lensa": DB | JUMP | TS | HRL, "finish": DB | JUMP | TS | HRL | {"NTFY_TOPIC", "TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "IMESSAGE_ALERTS_HOST"}, "interview": {"NOTION_INTERVIEW_TOKEN"}, "report": {"NTFY_TOPIC"}},
    "network": {"network": DB | JUMP | TS | {"NOTION_API_TOKEN"}},
    "outlook": {"outlook": DB},
    "profile-sync": {"sync": DB},
    "granola-auto": {"auto": DB - {"LIFEOS_ACQ_SSH_PORT"} | {"NOTION_INTERVIEW_TOKEN", "TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "IMESSAGE_ALERTS_HOST"}},
    "granola": {"granola": {"NOTION_INTERVIEW_TOKEN", "TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "IMESSAGE_ALERTS_HOST"}},
    "manual-job": {"manual": DB | {"NOTION_INTERVIEW_TOKEN"}},
    "hermes-stage": {"hermes": {"JIRA_API_TOKEN", "JIRA_EMAIL"}},
    "readiness-live": {"readiness": {"JIRA_API_TOKEN", "JIRA_EMAIL"}},
    "interview-proof": {"proof": {"NOTION_INTERVIEW_TOKEN"}},
    "recruiters": {"recruiters": DB | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_RECRUITERS_TOKEN"}},
    "watchdog": {"watch": {"NTFY_TOPIC"}},
    "safety": {"safety": DB | {"FIT_PROFILE_EXTRA_JSON", "FIT_PROFILE_JSON", "NTFY_TOPIC"}},
    "amazon": {"amazon": {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_AMAZON_TOKEN"}},
    "fit-edge-test": {"fit_edge": {"TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "IMESSAGE_ALERTS_HOST"}},
    "diag": {"diag": DB, "trace": DB | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN"}, "sandbox": {"NOTION_API_TOKEN"}, "interview_ui": {"NOTION_INTERVIEW_TOKEN"}, "report": DB | {"NOTION_API_TOKEN"}},
}
PILOTS = ("watchdog", "safety", "amazon", "diag")
SECRET_FREE = ("pr-tests",)         # workflows that read no secret at all, so they have no loader to pin (their own test below pins that)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class BitwardenConsumerTests(unittest.TestCase):
    """LIFE-644 step 2: rules, not pins. Every workflow reads credentials through the pinned Bitwarden loader and nothing else, whatever steps, commands, timeouts or triggers it has.
    A test edit is needed only when a credential is added to a job (LOADED, DOMAIN_SECRETS) or created (UUIDS)."""
    ACTION = "./.github/actions/bws-load"                                  # LIFE-631: the one local loader; the pinned third-party action and its one retry live inside it (rules at the end of this class)
    PINNED = "./.github/actions/sm-action"
    OUTPUT = re.compile(r"\$\{\{ steps\.(\w+)\.outputs\.(\w+) \}\}")   # a loaded value, read from the loader's output
    PAUSE = "sleep $((30 + RANDOM % 45))"

    @classmethod
    def setUpClass(cls):
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        cls.text = {p.stem: p.read_text() for p in sorted(root.glob("*.yml")) if p.stem not in SECRET_FREE}
        cls.doc = {n: yaml.safe_load(t) for n, t in cls.text.items()}

    def loaders(self, name, job):
        return [(i, s) for i, s in enumerate(self.doc[name]["jobs"][job]["steps"]) if s.get("uses") == self.ACTION]

    def jobs_with_loaders(self, name):
        return [job for job in self.doc[name]["jobs"] if self.loaders(name, job)]

    def loaded(self, name, job):
        return {m.group(2) for m in re.finditer(r"^([0-9a-f-]{36}) > (\w+)$", self.loaders(name, job)[0][1]["with"]["secrets"], re.M)}

    def test_every_loader_is_the_local_action_with_the_exact_ids_and_nothing_that_could_bypass_it(self):
        for name, doc in self.doc.items():
            text, jobs = self.text[name], self.jobs_with_loaders(name)
            self.assertTrue(jobs, name)                                                                         # every workflow that reads a credential loads it through the one local loader
            self.assertIn("LIFEOS_BWS_RUNTIME_TOKEN", re.findall(r"secrets\.(\w+)", text), name)
            self.assertFalse([n for n in UUIDS if re.search(rf"secrets\.{n}\b", text)], name)                    # no migrated value is read from GitHub any more
            self.assertEqual(re.findall(r"uses:\s*(\S*bws-load)", text), [self.ACTION] * len(jobs), name)       # one loader step per job: it holds the load and its one retry
            self.assertNotIn("bitwarden/", text, name)                                                           # the third-party action is named only inside the local action
            if name != "domains":
                self.assertEqual(set(jobs), set(LOADED[name]), name)
            for job in jobs:
                ((at, loader),) = self.loaders(name, job)
                steps, names = doc["jobs"][job]["steps"], self.loaded(name, job)
                self.assertTrue(names and names <= set(UUIDS), (name, job))
                self.assertTrue(names <= DOMAIN_SECRETS[job] | TS if name == "domains" else names == LOADED[name][job], (name, job, sorted(names)))
                self.assertEqual(steps[0]["uses"], "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09", (name, job))
                self.assertEqual(loader["with"]["access_token"], "${{ secrets.LIFEOS_BWS_RUNTIME_TOKEN }}", (name, job))
                self.assertEqual(sorted(re.findall(r"^([0-9a-f-]{36}) > (\w+)$", loader["with"]["secrets"], re.M)), sorted((UUIDS[n], n) for n in names), (name, job))
                self.assertEqual(len(re.findall(r" > ", loader["with"]["secrets"])), len(names), (name, job))
                self.assertFalse({"if", "env", "continue-on-error"} & set(loader), (name, job))                  # no bypass, no fallback source, and a failed load (after its one retry) fails the job
                self.assertTrue({"access_token", "secrets"} <= set(loader["with"]) <= {"access_token", "secrets", "set_env"}, (name, job))
                if name not in PILOTS:                                                                             # every workflow after the four accepted pilots is cut over
                    self.assertEqual(loader["id"], f"bws_{job}", (name, job))                                      # deterministic, unique id
                    self.assertIs(loader["with"]["set_env"], False, (name, job))                                  # nothing is exported job-wide
                    self.assertEqual(set(loader), {"id", "name", "uses", "with"}, (name, job))

    def test_the_local_loader_is_the_pinned_action_once_plus_its_one_retry_and_no_fallback(self):
        """LIFE-631: D209 (Bitwarden's 429 / 5xx gets one retry after a jittered pause, and a failed retry fails the job) is written once, here, instead of in 45 jobs."""
        root = pathlib.Path(__file__).resolve().parents[2] / ".github"
        action = yaml.safe_load((root / "actions" / "bws-load" / "action.yml").read_text())
        first, pause, retry = action["runs"]["steps"]
        failed = "${{ steps.first.outcome == 'failure' }}"
        passed = {"access_token": "${{ inputs.access_token }}", "secrets": "${{ inputs.secrets }}", "set_env": "${{ inputs.set_env }}"}
        self.assertEqual(action["runs"]["using"], "composite")
        self.assertEqual(first, {"id": "first", "continue-on-error": True, "uses": self.PINNED, "with": passed})
        self.assertEqual(pause, {"if": failed, "shell": "bash", "run": self.PAUSE})                              # a jittered pause, only when the first load failed
        self.assertEqual(retry, {"id": "retry", "if": failed, "uses": self.PINNED, "with": passed})            # the same pinned action and ids; no continue-on-error and no env: a used-up retry fails the job
        needed = {n for text in self.text.values() for n in re.findall(r"^\s+[0-9a-f-]{36} > (\w+)$", text, re.M)}
        self.assertTrue(needed <= set(action["outputs"]), sorted(needed - set(action["outputs"])))                # every loaded name has an output
        for key, spec in action["outputs"].items():
            self.assertEqual(spec["value"], "${{ steps.first.outputs.%s || steps.retry.outputs.%s }}" % (key, key), key)   # the first load's value, else its one retry's
        for path in sorted(root.rglob("*.y*ml")):
            if path != root / "actions" / "bws-load" / "action.yml":
                self.assertNotRegex(path.read_text(), r"uses:\s*bitwarden/", path.name)                           # the third-party action is used nowhere else

    def test_only_steps_that_run_a_command_receive_a_loaded_value_and_the_loader_carries_exactly_what_they_use(self):
        for name, doc in self.doc.items():
            if name in PILOTS:
                continue
            self.assertFalse([k for k, v in (doc.get("env") or {}).items() if k in UUIDS], name)                  # nothing at workflow level
            for job, body in doc["jobs"].items():
                self.assertFalse([k for k, v in (body.get("env") or {}).items() if k in UUIDS and v != ""], (name, job))   # nothing at job level (the Interview job's blanks stay)
                found = self.loaders(name, job)
                if not found:                                                           # D244: a job with no loader may receive no migrated value at all
                    self.assertFalse([k for step in body["steps"] for k in (step.get("env") or {}) if k in UUIDS], (name, job))
                    self.assertFalse([v for step in body["steps"] for v in (step.get("with") or {}).values() if "steps.bws_" in str(v)], (name, job))
                    continue
                ((at, loader),) = found
                seen = set()
                for i, step in enumerate(body["steps"]):
                    label = step.get("name") or step.get("uses")
                    got = set()
                    for key, value in (step.get("env") or {}).items():
                        if key in UUIDS:
                            m = self.OUTPUT.fullmatch(str(value).strip())
                            self.assertTrue(m and m.group(2) == key and m.group(1) == loader["id"], (name, job, label, key))
                            got.add(key)
                            self.assertGreater(i, at, (name, job, label))                                          # the loader precedes every step that consumes its outputs
                    if step is loader:
                        continue
                    passed = {k: self.OUTPUT.fullmatch(str(v).strip()) for k, v in (step.get("with") or {}).items()}
                    passed = {k: m for k, m in passed.items() if m}
                    if step.get("uses") == TAILSCALE_ACTION:              # LIFE-31: the pinned join gets exactly its two OAuth values, from this job's loader
                        self.assertEqual({k: (m.group(1), m.group(2)) for k, m in passed.items()}, {k: (loader["id"], n) for k, n in TAILSCALE_WITH.items()}, (name, job))
                        self.assertGreater(i, at, (name, job, label))
                        seen |= set(TAILSCALE_WITH.values())
                    else:
                        self.assertEqual(passed, {}, (name, job, label))                                           # no other action receives a loader output
                    if "run" not in step or "uses" in step:
                        self.assertEqual(got, set(), (name, job, label))                                           # checkout, setup, actions and installs never get a migrated value
                    seen |= got
                self.assertEqual(seen, self.loaded(name, job), (name, job))                  # the loader carries exactly what its consumers use

    def test_every_workflow_is_read_only_queues_its_runs_and_bounds_every_job(self):
        for name, doc in self.doc.items():
            self.assertTrue({"pull_request_target", "issue_comment"}.isdisjoint(doc[True]), name)                 # no trigger that runs with secrets on a stranger's input
            self.assertEqual({k: v for k, v in doc["permissions"].items() if v != "read"}, {"issues": "write"} if name == "watchdog" else {}, name)
            self.assertTrue(doc.get("concurrency") or name == "watchdog", name)                                     # runs queue; only the read-only watchdog has no group
            self.assertIs((doc.get("concurrency") or {}).get("cancel-in-progress", False), False, name)           # and a waiting run is never cancelled by the next
            for job, body in doc["jobs"].items():
                self.assertIsInstance(body.get("timeout-minutes"), int, (name, job))                                # every job is bounded
            self.assertFalse(re.findall(r"run:[^\n]*\$\{\{ *secrets\.", self.text[name]), name)                    # a secret is never pasted into a command line

    def test_a_stopped_granola_bridge_fails_the_job_so_the_report_job_pushes_the_phone(self):
        """LIFE-312: the bridge step keeps continue-on-error so the warning prints, then a final step fails the job; the report job reads `failure` from `needs`."""
        job = self.doc["domains"]["jobs"]["granola"]
        steps = job["steps"]
        bridge = next(s for s in steps if s.get("id") == "gran")
        self.assertIs(bridge.get("continue-on-error"), True)
        self.assertNotIn("continue-on-error", job)                                                                  # the job itself is not allowed to swallow the failure
        last = steps[-1]
        self.assertEqual((last["run"], last["if"]), ("exit 1", "${{ always() && steps.gran.outcome == 'failure' }}"))
        self.assertIn("Granola auto warning", steps[-2]["name"])                                                    # the warning still prints first
        self.assertIn("granola", self.doc["domains"]["jobs"]["report"]["needs"])                                    # and the report job watches this job


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
        self.assertEqual(re.findall(r"uses:\s*(\S*bws-load)", self.text), ["./.github/actions/bws-load"])

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


class ConcurrencyHasNoSwitch(unittest.TestCase):
    """LIFE-604 approval 5: TICK_CONCURRENCY (and its benchmark twin) are gone from the workflow and the code; the list of settings has no row for them."""
    def test_no_workflow_or_code_file_reads_the_switch(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        for path in list((root / ".github/workflows").glob("*.yml")) + [root / "lifeos/jobs/tick.py", root / "lifeos/platform/bench.py", root / "lifeos/platform/credential_list.py"]:
            self.assertNotIn("TICK_CONCURRENCY", path.read_text(), path.name)
            self.assertNotIn("BENCH_TICK_CONCURRENCY", path.read_text(), path.name)
