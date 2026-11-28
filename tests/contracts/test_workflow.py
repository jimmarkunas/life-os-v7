import pathlib
import re
import unittest
from tests import wftext  # noqa: E402

from lifeos.platform import due
import sys as _sys

_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "tools"))
import wfsets  # noqa: E402


try:
    import yaml
except ImportError:
    yaml = None


@unittest.skipIf(yaml is None, "PyYAML not installed")
class WorkflowTests(unittest.TestCase):
    def test_every_workflow_file_parses_and_every_manual_one_declares_its_dispatch(self):
        """A workflow that is not valid YAML is silently not runnable (safety.yml shipped that way once)."""
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        for path in sorted(root.glob("*.y*ml")):
            data = yaml.safe_load(wftext.read(path))
            self.assertIn("jobs", data, path.name)
            self.assertTrue("on" in data or True in data, path.name)

    def test_safety_workflow_is_weekly_and_manual_with_its_stages(self):
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        data = yaml.safe_load((root / "safety.yml").read_text())
        self.assertEqual(sorted(data[True]), ["schedule", "workflow_dispatch"])  # Jim's weekly check
        self.assertEqual(data[True]["schedule"], [{"cron": "17 13 * * 1"}])
        self.assertIn("|| 'all'", yaml.safe_load((root / "safety.yml").read_text())["jobs"]["safety"]["env"]["STAGE"])
        self.assertEqual(
            data[True]["workflow_dispatch"]["inputs"]["stage"]["options"],
            ["all", "sql-smoke", "fit-golden", "fit-capture"],
        )

    def test_hourly_workflow_is_valid_yaml_with_dispatch_inputs(self):
        path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hourly.yml"
        data = yaml.safe_load(wftext.read(path))
        self.assertIn("workflow_dispatch", data[True])
        self.assertIn("live", data[True]["workflow_dispatch"]["inputs"])

    def test_a_failed_publish_step_is_named_by_the_lane_list_and_does_not_stop_the_later_stages(self):
        """`lifeos.run publish` exits 1 when a page was not created"""
        import os
        import subprocess
        import tempfile

        path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hourly.yml"
        data = yaml.safe_load(wftext.read(path))
        steps = data["jobs"]["finish"]["steps"]
        index = next(i for i, step in enumerate(steps) if step.get("id") == "publish")
        publish = steps[index]
        self.assertTrue(publish.get("continue-on-error"))
        self.assertIn("python -m lifeos.run publish", publish["run"])
        self.assertNotIn("|| true", publish["run"])  # nothing swallows the exit code
        for later in steps[index + 1 :]:
            self.assertNotIn(
                "steps.publish", str(later.get("if", "")), later.get("name")
            )  # a failed publish never skips a later stage
        lanes = next(step for step in steps if step.get("id") == "lanes")
        self.assertEqual(lanes.get("if"), "${{ always() }}")
        self.assertEqual(data["jobs"]["finish"]["outputs"]["failed"], "${{ steps.lanes.outputs.failed }}")

        def lane_result(failed_step):
            script = re.sub(
                r"\$\{\{\s*([^}]+?)\s*\}\}",
                lambda m: "failure" if m.group(1) == f"steps.{failed_step}.outcome" else "success",
                lanes["run"],
            )
            with tempfile.TemporaryDirectory() as directory:
                output = pathlib.Path(directory, "out")
                done = subprocess.run(
                    ["bash", "-e", "-c", script],
                    env={**os.environ, "GITHUB_OUTPUT": str(output)},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return (
                    done.returncode,
                    done.stdout.strip(),
                    next(line for line in output.read_text().splitlines() if line.startswith("failed=")),
                )  # codes= is its own line

        self.assertEqual(lane_result("publish"), (1, "FAILED STAGES: publish", "failed=publish"))
        self.assertEqual(lane_result("none"), (0, "", "failed="))

        report = next(step for step in data["jobs"]["report"]["steps"] if "finish job report" in step.get("name", ""))
        self.assertIn("finish", data["jobs"]["report"]["needs"])
        self.assertEqual(report["env"]["STAGES"], "${{ needs.finish.outputs.failed }}")
        self.assertIn("alerts push --key run_failed", report["run"])
        self.assertIn("stages: $STAGES", report["run"])
        self.assertFalse(report.get("continue-on-error"))  # a push the provider refuses turns the job red
        job = data["jobs"]["report"]
        self.assertEqual(
            job["if"],
            "${{ always() && (needs.prep.result == 'success' || (needs.prep.result == 'failure' && (github.event_name == 'schedule' || (inputs.tick && (inputs.cloud_backlog == '' || inputs.cloud_backlog == '0'))))) }}",
        )  # an automatic run's failed prep pushes
        self.assertIn("prep", job["needs"])
        self.assertIn("prep=${{ needs.prep.result }}", report["run"])  # a failed prep is named in the push

    def test_a_tick_runs_the_whole_chain_in_one_job_and_the_lanes_are_manual_only(self):
        """a pipeline tick runs lifeos.jobs.tick in the finish job (one credential load, one store tunnel)"""
        from pathlib import Path
        from lifeos.jobs import tick

        path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hourly.yml"
        data = yaml.safe_load(wftext.read(path))
        self.assertNotIn("resolve_dice", data[True]["workflow_dispatch"]["inputs"])
        self.assertNotIn("dice", data["jobs"])
        self.assertIn("resolve-dice", [s.name for s in tick.PLAN])
        for lane in ("jobright", "linkedin", "lensa"):
            self.assertIn(
                "github.event_name == 'workflow_dispatch' && !inputs.tick && !inputs.quick",
                data["jobs"][lane]["if"],
                lane,
            )  # a quick proof runs the tick path instead
        finish = data["jobs"]["finish"]
        self.assertIn("needs.prep.outputs.pipeline == 'true'", finish["if"])
        steps = finish["steps"]
        step = next(s for s in steps if s.get("id") == "tick")
        self.assertEqual(
            step["if"],
            "${{ needs.prep.outputs.pipeline == 'true' || (github.event_name == 'workflow_dispatch' && inputs.quick) }}",
        )  # a manual quick run proves exactly what a tick runs
        self.assertIn("${{ inputs.quick && '--quick' || '' }}", step["run"])
        self.assertTrue(step.get("continue-on-error"))
        self.assertIn("python -m lifeos.jobs.tick", step["run"])
        self.assertIn("tick=${{ steps.tick.outcome }}", next(s for s in steps if s.get("id") == "lanes")["run"])
        for s in steps:
            if s.get("id") in (
                "web",
                "enrich",
                "fit",
                "audit",
                "publish",
                "holds",
                "finalize",
                "web_scale_up",
                "titlewatch",
                "sponsors",
            ):
                self.assertNotIn("PIPELINE", s["if"], s["id"])

    def test_only_a_tick_or_a_domains_dispatch_starts_the_jira_job(self):
        """after a manual (or cancelled) hourly run the workflow_run event has no inputs, and `inputs.jira != 'none'` was
        true for an empty input, so every.
        """
        from pathlib import Path

        condition = yaml.safe_load(wftext.logical("domains"))[
            "jobs"
        ]["jira"]["if"]
        self.assertIn("(github.event_name == 'workflow_dispatch' && inputs.jira != 'none')", condition)
        self.assertIn("needs.gate.outputs.due_jira == 'true'", condition)
        self.assertNotIn("|| inputs.jira != 'none'", condition)

    def test_domain_jobs_left_hourly_and_run_after_a_tick_from_domains(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / ".github/workflows"
        hourly = yaml.safe_load(wftext.logical("hourly"))
        domains = yaml.safe_load(wftext.logical("domains"))
        for name in ("jira", "outlook", "bills", "agenda", "amazon"):
            self.assertNotIn(name, hourly["jobs"], name)
            self.assertIn(f"needs.gate.outputs.due_{name} == 'true'", domains["jobs"][name]["if"], name)
        self.assertNotIn("jira", hourly[True]["workflow_dispatch"]["inputs"])
        self.assertEqual(domains[True]["workflow_run"]["workflows"], ["hourly"])
        self.assertIn("completed", domains[True]["workflow_run"]["types"])
        self.assertIn("!= 'cancelled'", domains["jobs"]["gate"]["if"])
        self.assertIn("display_title == 'tick'", domains["jobs"]["gate"]["if"])  # nor does a manual hourly run
        for name in ("agenda", "bills", "imessage", "hiring"):
            self.assertEqual(domains["jobs"][name]["needs"], ["gate"], name)
            self.assertIn("always()", domains["jobs"][name]["if"], name)

    def test_bills_paid_runs_on_a_tick_before_the_snapshot_and_follows_the_live_flag(self):
        from pathlib import Path

        domains = yaml.safe_load(wftext.logical("domains"))
        steps = domains["jobs"]["bills"]["steps"]
        ids = [s.get("id") for s in steps]
        self.assertLess(ids.index("bpaid"), ids.index("bsnap"))  # the snapshot sees the advanced dates
        step = steps[ids.index("bpaid")]
        self.assertIn("workflow_run", step["if"])
        self.assertIn("inputs.bills_paid", step["if"])
        self.assertIn('--live', step["run"])
        self.assertIs(domains[True]["workflow_dispatch"]["inputs"]["bills_paid"]["default"], False)

    def test_the_manual_amazon_orders_pass_queues_with_the_domain_jobs_and_maintenance_does_not(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        amazon = yaml.safe_load((root / ".github/workflows/amazon.yml").read_text())
        domains = yaml.safe_load(wftext.logical("domains"))
        group = amazon["concurrency"]["group"]
        self.assertIn("inputs.stage == 'orders'", group)
        self.assertIn("inputs.phase == 'tick')) && 'life-os-v7-domains' ||", domains["concurrency"]["group"])
        self.assertIn("|| 'life-os-v7-domains-late') }}", domains["concurrency"]["group"])  # ...
        self.assertIn("'life-os-v7-domains-idle'", domains["concurrency"]["group"])  # ...
        self.assertIn("'life-os-v7-domains-manual'", domains["concurrency"]["group"])  # ...
        self.assertIn("'life-os-v7-domains' ||", group)  # orders: the very group the real domain runs hold
        self.assertIn("'life-os-v7-amazon-orders'", group)  # backfill, retention, review-reset: their own
        self.assertIs(amazon["concurrency"]["cancel-in-progress"], False)
        self.assertEqual(
            group, "${{ inputs.stage == 'orders' && 'life-os-v7-domains' || 'life-os-v7-amazon-orders' }}"
        )  # left the queueing exactly as it was
        self.assertEqual(amazon["jobs"]["amazon"]["timeout-minutes"], 330)
        self.assertNotIn("schedule", amazon[True])

    def test_amazon_manual_workflow_is_dry_by_default_and_adds_no_hourly_input(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        hourly = yaml.safe_load(wftext.logical("hourly"))
        manual = yaml.safe_load((root / ".github/workflows/amazon.yml").read_text())
        self.assertNotIn("amazon", hourly[True]["workflow_dispatch"]["inputs"])
        self.assertIs(manual[True]["workflow_dispatch"]["inputs"]["live"]["default"], False)

    def test_amazon_reads_its_three_migrated_credentials_from_bitwarden_through_the_pinned_action(self):
        """Phase B: exactly the Gmail client secret, Gmail refresh token and Notion Amazon token come from Bitwarden"""
        import re
        from pathlib import Path

        text = (Path(__file__).resolve().parents[2] / ".github/workflows/amazon.yml").read_text()
        manual = yaml.safe_load(text)
        inputs = manual[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(list(manual[True]), ["workflow_dispatch"])  # manual only: no schedule, push or other trigger
        self.assertEqual(inputs["stage"]["options"], ["orders", "backfill", "retention", "review-reset", "census"])
        self.assertEqual(
            (inputs["since"]["default"], inputs["live"]["default"]), ("2015-01-01", False)
        )  # dry run stays the default
        self.assertEqual(
            sorted(set(re.findall(r"secrets\.(\w+)", text))), ["DOPPLER_TOKEN", "LIFEOS_BWS_RUNTIME_TOKEN"]
        )
        for moved in ("GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "NOTION_AMAZON_TOKEN"):
            self.assertNotIn(f"secrets.{moved}", text)  # no direct copy, so no fallback
        self.assertEqual(re.findall(r"uses:\s*(\S*bws-load)", text), ["./.github/actions/bws-load"] * 2)
        mappings = re.findall(
            r"^\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s*>\s*\w+)\s*$", text, re.M
        )
        self.assertEqual(
            sorted(mappings),
            sorted(
                [
                    "08245c07-728f-438e-940b-b4db015a6967 > GMAIL_OAUTH_CLIENT_SECRET",
                    "672a44e9-235f-4121-a99d-b4db015a6987 > GMAIL_OAUTH_REFRESH_TOKEN",
                    "46fe195b-82e2-416e-8272-b4db015eea12 > NOTION_AMAZON_TOKEN",
                    "8657d1ed-2cde-4298-bd62-b50301523e01 > GMAIL_OAUTH_CLIENT_ID",
                    UUIDS["NOTION_JIRA_TOKEN"] + " > NOTION_JIRA_TOKEN",
                    UUIDS["V7_RUNTIME_CONFIG_SOURCE_ID"] + " > V7_RUNTIME_CONFIG_SOURCE_ID",
                ]
            ),
        )
        self.assertEqual(len(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)), 6)
        self.assertLess(text.index("bws-load"), text.index("python -m lifeos.run"))  # loaded before the Amazon command
        self.assertIn("--live", text)

    def test_amazon_since_reaches_only_the_backfill_stage_so_orders_keeps_its_normal_watermark_window(self):
        """Live run 37541352965: the 2015-01-01 default of the backfill-only `since` input reached stage=orders
        (AMAZON_SINCE overrides the watermark in.
        """
        import re
        from pathlib import Path

        text = (Path(__file__).resolve().parents[2] / ".github/workflows/amazon.yml").read_text()
        step_env = yaml.safe_load(text)["jobs"]["amazon"]["steps"][-1]["env"]
        expression = step_env["AMAZON_SINCE"]
        self.assertEqual(expression, "${{ inputs.stage == 'backfill' && inputs.since || '' }}")
        self.assertEqual(len(re.findall(r"inputs\.since", text)), 1)  # the only use of the input
        stage_choices = yaml.safe_load(text)[True]["workflow_dispatch"]["inputs"]["stage"]["options"]
        gate = re.fullmatch(r"\$\{\{ inputs\.stage == '([a-z-]+)' && inputs\.since \|\| '' \}\}", expression).group(1)
        default_since = yaml.safe_load(text)[True]["workflow_dispatch"]["inputs"]["since"]["default"]
        passed = {stage: (default_since if stage == gate else "") for stage in stage_choices}
        self.assertEqual({stage for stage, value in passed.items() if value}, {"backfill"})
        self.assertEqual(passed["orders"], "")
        self.assertEqual(
            yaml.safe_load(text)[True]["workflow_dispatch"]["inputs"]["since"]["default"], "2015-01-01"
        )  # the backfill default is unchanged

    def test_diag_reads_its_seven_database_and_ssh_values_from_bitwarden_through_the_pinned_action(self):
        """Phase B: the read-only diagnostic keeps its manual contract"""
        import re
        from pathlib import Path

        text = wftext.read(Path(__file__).resolve().parents[2] / ".github/workflows/diag.yml")
        diag = yaml.safe_load(text)
        moved = {
            "LIFEOS_ACQ_DB_NAME": "d71bbaed-6a6c-49b5-8a1f-b4db015ecfda",
            "LIFEOS_ACQ_DB_PASSWORD": "00d82e0d-be10-4c8c-bdbe-b4db015ed389",
            "LIFEOS_ACQ_DB_USER": "9422b842-3bb0-4153-bb8d-b4db015ed746",
            "LIFEOS_ACQ_SSH_HOST": "6f1c86f6-c48f-4e77-b65d-b4db015edb15",
            "LIFEOS_ACQ_SSH_KNOWN_HOSTS": "b65dd2fc-2573-4bda-9e80-b4db015edec6",
            "LIFEOS_ACQ_SSH_PRIVATE_KEY": "7ada9cea-8711-4b37-bef9-b4db015ee298",
            "LIFEOS_ACQ_SSH_USER": "6b3e12b9-1987-4073-bda2-b4db015ee653",
        }
        stage = diag[True]["workflow_dispatch"]["inputs"]["stage"]
        job = diag["jobs"]["diag"]
        self.assertEqual(list(diag[True]), ["workflow_dispatch"])
        self.assertNotRegex(text, r"(?m)^\s*(schedule|workflow_run|repository_dispatch|push|pull_request\w*):")
        self.assertEqual(
            (stage["options"], stage["default"]),
            (
                [
                    "scale-up-holds",
                    "scale-up-links",
                    "scale-up-boards",
                    "scale-up-exclusions",
                    "scale-up-exclusion-review",
                    "scale-up-register",
                    "jobs-backlog",
                    "alerts-open",
                    "newsletter-trace",
                    "notion-sandbox-access",
                    "notion-sandbox-outreach-proof",
                    "interview-ui-probe",
                    "inbox-proof",
                    "inbox-recount",
                    "inbox-schema-check",
                    "inbox-schema-apply",
                    "followup-schema-check",
                    "followup-schema-apply",
                ],
                "scale-up-holds",
            ),
        )
        self.assertEqual(diag["permissions"], {"contents": "read"})
        self.assertEqual(diag["concurrency"], {"group": "life-os-v7-diag", "cancel-in-progress": False})
        self.assertEqual(job["timeout-minutes"], 10)
        self.assertEqual(job["steps"][-1]["run"], 'python -m lifeos.run "$STAGE"')
        self.assertEqual(
            job["steps"][-1]["env"],
            {
                "STAGE": "${{ inputs.stage }}",
                "SCHEMA_CONFIRM": "${{ inputs.confirm }}",
                "ALERTS_MESSAGE_REF": "${{ inputs.message_ref }}",
            },
        )
        message = diag[True]["workflow_dispatch"]["inputs"]["message_ref"]
        self.assertEqual((message["type"], message["default"]), ("string", ""))
        self.assertNotIn("--live", text.split("\n  # LIFE-10:")[0])  # the database and Gmail diagnostics never write
        confirm = diag[True]["workflow_dispatch"]["inputs"]["confirm"]
        self.assertEqual((confirm["type"], confirm["default"]), ("string", ""))
        self.assertEqual(
            sorted(set(re.findall(r"secrets\.(\w+)", text))), ["DOPPLER_TOKEN", "LIFEOS_BWS_RUNTIME_TOKEN"]
        )
        for name in moved:
            self.assertNotIn(f"secrets.{name}", text)  # no direct copy, so no fallback
        self.assertEqual(re.findall(r"uses:\s*(\S*bws-load)", text), ["./.github/actions/bws-load"] * 9)
        head, rest = text.split("\n  # LIFE-64:")
        trace_text, sandbox_text = rest.split("\n  # LIFE-10:")  # then the one job that reads Notion
        sandbox_text, ui_text = sandbox_text.split("\n  # LIFE-88:")
        ui_text, report_text = ui_text.split("\n  # LIFE-121:")
        report_text, qa_text = report_text.split("\n  # LIFE-791:")
        uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        gmail = {
            "GMAIL_OAUTH_CLIENT_SECRET": "08245c07-728f-438e-940b-b4db015a6967",
            "GMAIL_OAUTH_REFRESH_TOKEN": "672a44e9-235f-4121-a99d-b4db015a6987",
            "GMAIL_OAUTH_CLIENT_ID": "8657d1ed-2cde-4298-bd62-b50301523e01",
        }
        self.assertEqual(
            sorted(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", head, re.M)),
            sorted([f"{uuid_} > {name}" for name, uuid_ in moved.items()]),
        )
        self.assertEqual(
            sorted(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", trace_text, re.M)),
            sorted([f"{uuid_} > {name}" for name, uuid_ in {**moved, **gmail}.items()]),
        )
        self.assertEqual(
            (len(re.findall(uuid, head)), len(re.findall(uuid, trace_text))), (len(moved), len(moved) + len(gmail))
        )
        self.assertNotIn("GMAIL", head)  # no Gmail value or secret in the database-only job
        self.assertEqual(
            re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", sandbox_text, re.M),
            [
                "53dc3d04-dbb2-45fe-8185-b4db015eedcb > NOTION_API_TOKEN",
                f"{UUIDS['V7_RUNTIME_CONFIG_SOURCE_ID']} > V7_RUNTIME_CONFIG_SOURCE_ID",
            ],
        )
        self.assertEqual(len(re.findall(uuid, sandbox_text)), 2)
        self.assertNotIn("NOTION", head + trace_text)
        for word in ("LIFEOS_ACQ", "GMAIL", "inputs.message_ref", "secrets.NOTION"):
            self.assertNotIn(word, sandbox_text)
        self.assertLess(
            text.index("bws-load"), text.index("python -m lifeos.run")
        )  # loaded before the diagnostic command
        trace = diag["jobs"]["trace"]
        sandbox = diag["jobs"]["sandbox"]
        self.assertEqual(
            (job["if"], trace["if"], sandbox["if"]),
            (
                "${{ inputs.stage != 'scale-up-exclusion-review' && inputs.stage != 'scale-up-register' && inputs.stage != 'jobs-backlog' && inputs.stage != 'newsletter-trace' && inputs.stage != 'notion-sandbox-access' && inputs.stage != 'notion-sandbox-outreach-proof' && inputs.stage != 'interview-ui-probe' && inputs.stage != 'inbox-proof' && inputs.stage != 'inbox-recount' }}",
                "${{ inputs.stage == 'newsletter-trace' }}",
                "${{ inputs.stage == 'notion-sandbox-access' || inputs.stage == 'notion-sandbox-outreach-proof' }}",
            ),
        )  # exactly one of the three jobs runs
        self.assertEqual(sandbox["timeout-minutes"], 5)
        token = "${{ steps.bws_sandbox.outputs.NOTION_API_TOKEN }}"
        read, proof = sandbox["steps"][-2:]
        self.assertEqual(
            (read["if"], read["run"], without_address(read["env"])),
            (
                "${{ inputs.stage == 'notion-sandbox-access' }}",
                "python -m lifeos.run notion-sandbox-access --limit 0",
                {"NOTION_API_TOKEN": token, "LIFEOS_NOTION_IDS": ""},
            ),
        )  # the ids map is declared empty
        self.assertEqual(
            (proof["if"], proof["run"], without_address(proof["env"])),
            (
                "${{ inputs.stage == 'notion-sandbox-outreach-proof' }}",
                "python -m lifeos.run notion-sandbox-outreach-proof --limit 0 --live",
                {"NOTION_API_TOKEN": token, "PROOF_CONFIRM": "${{ inputs.confirm }}", "LIFEOS_NOTION_IDS": ""},
            ),
        )  # the only write step
        self.assertEqual(
            [s.get("env") for s in sandbox["steps"][:-2]], [None] * (len(sandbox["steps"]) - 2)
        )  # only the two stage steps receive the token
        self.assertIs(
            next(s for s in sandbox["steps"] if s.get("id") == "bws_sandbox")["with"]["set_env"], False
        )  # a masked step output, never exported job-wide
        report = diag["jobs"]["report"]  # private reports to one Notion page
        self.assertEqual(
            (report["if"], report["timeout-minutes"]),
            (
                "${{ inputs.stage == 'scale-up-exclusion-review' || inputs.stage == 'scale-up-register' || inputs.stage == 'jobs-backlog' }}",
                10,
            ),
        )
        self.assertEqual(
            sorted(re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", report_text, re.M)),
            sorted(
                [
                    f"{uuid_} > {name}"
                    for name, uuid_ in {**moved, "NOTION_API_TOKEN": "53dc3d04-dbb2-45fe-8185-b4db015eedcb"}.items()
                ]
            ),
        )
        self.assertEqual(
            report["steps"][-1]["env"],
            {
                "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "",  # declared empty, the V7 settings page fills it
                "STAGE": "${{ inputs.stage }}",
                "REPORT_CONFIRM": "${{ inputs.confirm }}",
            },
        )
        self.assertEqual(report["steps"][-1]["run"].count("--live"), 1)
        self.assertIn('[ "$REPORT_CONFIRM" = "REPORT" ]', report["steps"][-1]["run"])
        self.assertNotIn("GMAIL", report_text)
        ui = diag["jobs"]["interview_ui"]  # the one job that holds the Interview token
        self.assertEqual((ui["if"], ui["timeout-minutes"]), ("${{ inputs.stage == 'interview-ui-probe' }}", 5))
        self.assertEqual(
            re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", ui_text, re.M),
            [
                "092b44ae-32cb-453b-8cc2-b4db015ef571 > NOTION_INTERVIEW_TOKEN",
                f"{UUIDS['V7_RUNTIME_CONFIG_SOURCE_ID']} > V7_RUNTIME_CONFIG_SOURCE_ID",
            ],
        )  # plus the address, in its own loader
        for word in ("LIFEOS_ACQ", "GMAIL", "inputs.message_ref", "secrets.NOTION", "NOTION_API_TOKEN"):
            self.assertNotIn(word, ui_text)
        qa = diag["jobs"]["inbox_view_qa"]
        self.assertEqual(qa["if"], "${{ inputs.stage == 'inbox-proof' || inputs.stage == 'inbox-recount' }}")
        self.assertEqual(
            re.findall(rf"^\s*({uuid}\s*>\s*\w+)\s*$", qa_text, re.M),
            [
                "53dc3d04-dbb2-45fe-8185-b4db015eedcb > NOTION_API_TOKEN",
                f"{UUIDS['V7_RUNTIME_CONFIG_SOURCE_ID']} > V7_RUNTIME_CONFIG_SOURCE_ID",
            ],
        )  # plus the address, in its own loader
        self.assertEqual(qa_text.count("--live"), 1)
        self.assertIn("INBOX_VIEW_CONFIRM: ${{ inputs.confirm }}", qa_text)
        for word in ("LIFEOS_ACQ", "GMAIL", "NOTION_INTERVIEW_TOKEN", "inputs.message_ref"):
            self.assertNotIn(word, qa_text)
        self.assertNotIn("NOTION_INTERVIEW_TOKEN", head + trace_text + sandbox_text)
        probe = ui["steps"][-1]
        self.assertEqual(
            (probe["run"], without_address(probe["env"])),
            (
                "python -m lifeos.run interview-ui-probe --limit 0 --live",
                {
                    "NOTION_INTERVIEW_TOKEN": "${{ steps.bws_interview.outputs.NOTION_INTERVIEW_TOKEN }}",
                    "PROBE_CONFIRM": "${{ inputs.confirm }}",
                    "LIFEOS_NOTION_IDS": "",
                },
            ),
        )  # the stage itself refuses without QA
        self.assertEqual([s.get("env") for s in ui["steps"][:-1]], [None] * (len(ui["steps"]) - 1))
        self.assertEqual(trace["timeout-minutes"], 10)
        self.assertEqual(trace["steps"][-1]["run"], 'python -m lifeos.run "$STAGE"')
        self.assertEqual(
            trace["steps"][-1]["env"],
            {
                "GMAIL_OAUTH_CLIENT_ID": "${{ steps.bws_trace.outputs.GMAIL_OAUTH_CLIENT_ID }}",
                "STAGE": "${{ inputs.stage }}",
                "ALERTS_MESSAGE_REF": "${{ inputs.message_ref }}",
            },
        )


@unittest.skipIf(yaml is None, "PyYAML not installed")
class SuiteTimingWorkflow(unittest.TestCase):
    """suite-timing.yml measures the full unit-test suite on fresh runners."""

    path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "suite-timing.yml"

    def test_it_is_dispatch_only_read_only_and_holds_no_secret(self):
        text, doc = wftext.read(self.path), yaml.safe_load(wftext.read(self.path))
        self.assertEqual(list(doc[True]), ["workflow_dispatch"])  # no schedule, no push, no pull request
        self.assertEqual(doc["permissions"], {"contents": "read", "actions": "read"})
        for word in ("secrets.", "bitwarden", "LIFEOS_", "NOTION_", "GMAIL_", "--live", "bws-load"):
            self.assertNotIn(word, text)
        self.assertEqual(list(doc["jobs"]), ["plan", "timing", "report"])
        self.assertEqual(
            doc["jobs"]["timing"]["strategy"]["fail-fast"], False
        )  # one slow or failed run never hides the others

    def test_the_timing_job_runs_the_same_shard_command_as_pr_tests(self):
        timing = next(
            s
            for s in yaml.safe_load(wftext.read(self.path))["jobs"]["timing"]["steps"]
            if s.get("name", "").startswith("Full suite")
        )["run"]
        pr = (self.path.parent / "pr-tests.yml").read_text()
        self.assertIn('python -m tests.shard "$k" "$n"', timing)
        self.assertIn("python -m tests.shard", pr)  # the same module, the same split rule
        self.assertIn("-ge 120", pr)  # the CI limit the report compares against
        self.assertIn(
            "LIMIT = 120", (pathlib.Path(__file__).resolve().parents[2] / "tests" / "suite_timing.py").read_text()
        )


@unittest.skipIf(yaml is None, "PyYAML not installed")
class PullRequestTestsWorkflow(unittest.TestCase):
    path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pr-tests.yml"

    def test_it_runs_on_pull_requests_and_main_only_read_only_and_holds_no_secret(self):
        text, doc = wftext.read(self.path), yaml.safe_load(wftext.read(self.path))
        self.assertEqual(list(doc[True]), ["pull_request", "merge_group", "push"])
        self.assertEqual(doc[True]["merge_group"], {"types": ["checks_requested"]})  # the merge queue
        self.assertEqual(doc[True]["push"], {"branches": ["main"]})
        self.assertEqual(doc["permissions"], {"contents": "read"})
        for word in ("secrets.", "bitwarden", "NOTION_", "GMAIL_", "--live"):
            self.assertNotIn(word, text)
        self.assertEqual(sorted(set(re.findall(r"LIFEOS_[A-Z_]+", text))), ["LIFEOS_TEST_NOW"])
        self.assertEqual([j for j in doc["jobs"]], ["unit-tests", "test-index", "unit-tests-second-date"])

    def test_the_second_date_job_runs_date_sensitive_tests_on_pull_requests_and_everything_on_main(self):
        """today's date changing changes no test result (a pull request runs the listed modules only)."""
        doc = yaml.safe_load(self.path.read_text())
        job = doc["jobs"]["unit-tests-second-date"]
        self.assertEqual(
            (job["if"], job["timeout-minutes"], job.get("needs")),
            ("${{ github.event_name == 'pull_request' || github.event_name == 'push' }}", 5, None),
        )
        # a red second date on main must never turn the run red: the Close release and the test gate read its verdict
        self.assertEqual(job["continue-on-error"], "${{ github.event_name == 'push' }}")
        run = [s for s in job["steps"] if "env" in s and "LIFEOS_TEST_NOW" in s["env"]]
        self.assertEqual(len(run), 1)
        self.assertEqual(run[0]["env"], {"LIFEOS_TEST_NOW": "2027-03-09"})
        self.assertIn(
            "python -m tests.shard 1 4", run[0]["run"]
        )  # the same shard command as unit-tests, on a merge to main
        self.assertIn("tests/second-date-tests.txt", run[0]["run"])  # a pull request runs the listed modules only
        self.assertNotIn("permissions", job)  # the workflow's read-only default is all it gets
        self.assertIn("unit-tests:", wftext.read(self.path))  # the required check keeps its name

    def test_the_test_index_is_built_on_main_after_the_tests_and_only_restored_in_pull_requests(self):
        """docs/test-index.json is not committed."""
        doc = yaml.safe_load(wftext.read(self.path))
        unit, build = doc["jobs"]["unit-tests"]["steps"], doc["jobs"]["test-index"]
        key = "test-index-v1-${{ hashFiles('tests/testindex.py', 'requirements-test.lock') }}-"
        restore = next(s for s in unit if str(s.get("uses", "")).startswith("actions/cache/restore@"))
        names = [s.get("name", "") for s in unit]
        self.assertLess(
            unit.index(restore), names.index(next(n for n in names if n.startswith("Pick the tests")))
        )  # restored before `select` reads it
        self.assertEqual(
            restore["if"], "${{ github.event_name == 'pull_request' || github.event_name == 'merge_group' }}"
        )
        self.assertEqual(
            (restore["with"]["path"], restore["with"]["key"], restore["with"]["restore-keys"]),
            ("docs/test-index.json", key + "${{ github.sha }}", key),
        )
        self.assertFalse(
            [s for s in unit if "cache/save" in str(s.get("uses", ""))]
        )  # a pull request never writes the cache
        self.assertEqual((build["needs"], build["if"]), ("unit-tests", "${{ github.event_name == 'push' }}"))
        self.assertLessEqual(build["timeout-minutes"], 15)
        save = next(s for s in build["steps"] if str(s.get("uses", "")).startswith("actions/cache/save@"))
        self.assertEqual(
            (save["with"]["path"], save["with"]["key"]), ("docs/test-index.json", key + "${{ github.sha }}")
        )
        runs = [s["run"] for s in build["steps"] if "run" in s]
        self.assertIn("pip install --require-hashes -r requirements-test.lock", runs)
        built = next(i for i, s in enumerate(build["steps"]) if s.get("run") == "python -m tests.testindex build")
        self.assertLess(built, build["steps"].index(save))
        self.assertIn("coverage", (self.path.parent.parent.parent / "requirements-test.lock").read_text())

    def evaluate(self, expression, context):
        """Just enough of the Actions expression language for this file: ${{ a || b }}, == and string quotes."""
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

    def test_a_merge_to_main_never_cancels_the_previous_merges_check_but_a_pull_request_push_still_replaces_its_older_one(
        self,
    ):
        concurrency = yaml.safe_load(wftext.read(self.path))["concurrency"]

        def settings(name, **context):
            ctx = {"github": {"event_name": name, **context}}
            return self.evaluate(concurrency["group"], ctx), self.evaluate(concurrency["cancel-in-progress"], ctx)

        first = settings("push", ref="refs/heads/main", sha="aaa", event={})
        second = settings("push", ref="refs/heads/main", sha="bbb", event={})
        self.assertNotEqual(first[0], second[0])
        self.assertEqual((first[1], second[1]), ("false", "false"))
        pr_one = settings("pull_request", ref="refs/pull/7/merge", sha="ccc", event={"pull_request": {"number": 7}})
        pr_two = settings("pull_request", ref="refs/pull/7/merge", sha="ddd", event={"pull_request": {"number": 7}})
        self.assertEqual((pr_one[0], pr_one[1]), (pr_two[0], "true"))
        other_pr = settings("pull_request", ref="refs/pull/8/merge", sha="eee", event={"pull_request": {"number": 8}})
        self.assertNotEqual(pr_one[0], other_pr[0])

    def test_it_reuses_the_shared_python_action_and_runs_the_accepted_command_with_the_runtime_limits(self):
        doc = yaml.safe_load(wftext.read(self.path))
        steps = doc["jobs"]["unit-tests"]["steps"]
        names = [s.get("name") or s.get("uses") for s in steps]
        self.assertEqual(names[0], "Checkout the exact commit (own fetch, token never stored)")
        self.assertIn("./.github/actions/python", names)
        self.assertLess(
            names.index("./.github/actions/python"), next(i for i, n in enumerate(names) if "Unit tests" in n)
        )
        base = next(s for s in steps if s.get("name", "").startswith("Fetch the merge group's base"))
        self.assertEqual(
            base["if"], "${{ github.event_name == 'merge_group' }}"
        )  # the queue's group holds up to five PRs
        trailer = next(s for s in steps if s.get("name", "").startswith("Agent trailer"))
        self.assertEqual(trailer["if"], "${{ github.event_name == 'pull_request' }}")
        self.assertEqual(trailer["run"], "python tools/agent_trailer_check.py")
        run = [s["run"] for s in steps if "Unit tests" in s.get("name", "")][0]
        for k in (1, 2, 3, 4):
            self.assertIn(f"python -m tests.shard {k} 4 > shard-{k}.log", run)
        self.assertNotIn("python -m tests.shard 1 2", run)
        self.assertNotIn("unittest discover", run)
        self.assertRegex(run, r'-ge 120 \]; then .*exit 1')
        self.assertIn("TIME GATE ONLY: every test passed", run)
        self.assertRegex(run, r'-ge 90 \]; then echo "::warning::')

    def test_it_also_runs_the_concord_room_plugin_tests_after_the_python_suite_with_no_secret_or_install(self):
        doc = yaml.safe_load(wftext.read(self.path))
        steps = doc["jobs"]["unit-tests"]["steps"]
        names = [s.get("name", "") for s in steps]
        python_suite = next(i for i, n in enumerate(names) if "Unit tests" in n)
        room = next(i for i, n in enumerate(names) if "Concord Room plugin tests" in n)
        self.assertGreater(room, python_suite)
        self.assertEqual(steps[room]["run"], "node --test tools/orca-concord-room/test/worker.test.mjs")
        self.assertEqual(steps[room - 1]["uses"], "./.github/actions/node")
        self.assertTrue(
            pathlib.Path(__file__)
            .resolve()
            .parents[2]
            .joinpath("tools/orca-concord-room/test/worker.test.mjs")
            .is_file()
        )


class AmazonRetentionOffTheChainTests(unittest.TestCase):
    """Nothing else about it changed."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.jobs = yaml.safe_load(
                wftext.logical("domains")
            )["jobs"]
        except NameError:
            raise unittest.SkipTest("pyyaml unavailable") from None

    def test_retention_is_its_own_job_that_starts_after_the_amazon_job_and_nothing_waits_for_it_but_the_report(self):
        retention = self.jobs["amazon_retention"]
        self.assertEqual(retention["needs"], ["amazon", "gate"])
        self.assertEqual(retention["timeout-minutes"], 15)
        self.assertEqual(retention["if"].replace("due_amazon_retention", "due_amazon"), self.jobs["amazon"]["if"])
        waiting = sorted(name for name, job in self.jobs.items() if "amazon_retention" in (job.get("needs") or []))
        self.assertEqual(waiting, ["chain", "report"])  # no page writer waits for it

    def test_the_amazon_job_no_longer_runs_retention_and_the_command_is_unchanged_in_the_new_job(self):
        self.assertNotIn("aretain", [s.get("id") for s in self.jobs["amazon"]["steps"]])
        step = next(s for s in self.jobs["amazon_retention"]["steps"] if s.get("id") == "aretain")
        self.assertEqual(
            step["run"],
            'python -m lifeos.run amazon-retention --limit 200 $([ "$RETENTION_LIVE" = "true" ] && echo --live)',
        )
        self.assertEqual(step["env"]["RETENTION_LIVE"], "true")
        self.assertIs(step["continue-on-error"], True)  # a failure is a warning only, as before
        self.assertNotIn("always()", step.get("if", ""))  # the job's own steps run in order

    def test_a_retention_failure_is_named_in_the_report_and_warns_only(self):
        report = self.jobs["report"]["steps"][-1]["run"]
        self.assertIn("amazon_retention=${{ needs.amazon_retention.result }}", report)
        warn = next(s for s in self.jobs["amazon_retention"]["steps"] if "warning" in s.get("name", ""))
        self.assertEqual(warn["if"], "${{ always() && steps.aretain.outcome == 'failure' }}")


class UnitTestGateTests(unittest.TestCase):
    """the unit tests are skipped only through the fail-closed gate"""

    @classmethod
    def setUpClass(cls):
        try:
            cls.doc = yaml.safe_load(
                wftext.logical("hourly")
            )
        except NameError:
            raise unittest.SkipTest("pyyaml unavailable") from None

    def steps(self):
        return {s.get("name", ""): s for s in self.doc["jobs"]["unit-tests"]["steps"]}

    def test_the_gate_runs_first_and_only_it_can_skip_the_tests(self):
        steps = self.doc["jobs"]["unit-tests"]["steps"]
        names = [s.get("name", "") for s in steps]
        gate = next(i for i, n in enumerate(names) if n.startswith("Unit-test gate"))
        tests = next(i for i, n in enumerate(names) if n == "Unit tests")
        self.assertLess(gate, tests)
        self.assertEqual(steps[gate]["id"], "testgate")
        self.assertEqual(steps[gate]["run"], "python -m lifeos.platform.testgate")
        self.assertEqual(
            steps[tests]["if"], "${{ steps.testgate.outputs.skip != 'true' }}"
        )  # anything but the gate's own "true" runs the tests
        self.assertEqual(steps[tests]["run"], "python -m unittest discover -s tests -t . -v")
        self.assertNotIn("env", steps[tests])  # no index freshness flag or step
        self.assertFalse([n for n in names if "freshness" in n])

    def test_the_gate_reads_the_token_the_job_already_has_and_pr_tests_is_untouched(self):
        gate = next(s for n, s in self.steps().items() if n.startswith("Unit-test gate"))
        self.assertEqual(gate["env"], {"GITHUB_TOKEN": "${{ github.token }}"})
        self.assertEqual(self.doc["jobs"]["prep"]["permissions"].get("actions"), "write")  # prep keeps what it had
        pr = pathlib.Path(__file__).resolve().parents[2].joinpath(".github/workflows/pr-tests.yml").read_text()
        self.assertIn("unit-tests:", pr)  # the required check name is unchanged

    def test_the_tests_run_beside_the_run_in_their_own_job_and_a_failure_is_a_named_lane_in_the_report(self):
        job = self.doc["jobs"]["unit-tests"]
        self.assertNotIn("needs", job)
        self.assertNotIn("permissions", job)  # inherits the workflow's contents: read
        text = str(job)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("bws", text)  # no Bitwarden load, so no new secret name
        self.assertEqual(job["timeout-minutes"], 20)
        prep = [s.get("name", "") for s in self.doc["jobs"]["prep"]["steps"]]
        self.assertFalse(
            [
                n
                for n in prep
                if n.startswith("Unit-test gate")
                or n
                in (
                    "Unit tests",
                    "Test-only dependency (PyYAML, for the workflow contract tests; skipped if unavailable)",
                )
            ]
        )
        for name, j in self.doc["jobs"].items():
            if name not in ("report", "unit-tests"):
                self.assertNotIn(
                    "unit-tests", j.get("needs", []) if isinstance(j.get("needs"), list) else [j.get("needs")], name
                )  # nothing on the critical path waits for the tests
        report = self.doc["jobs"]["report"]
        self.assertIn("unit-tests", report["needs"])
        self.assertIn(
            "unit-tests=${{ needs.unit-tests.result }}",
            next(s for s in report["steps"] if "finish job report" in s.get("name", ""))["run"],
        )

    def test_chromium_is_cached_on_the_browser_lock_and_the_browser_step_is_its_own_timed_step(self):
        """item 13: ONE shared composite action installs the browser"""
        import yaml

        root = pathlib.Path(__file__).resolve().parents[2]
        steps = self.doc["jobs"]["finish"]["steps"]
        ids = [s.get("id") for s in steps]
        browser = next(s for s in steps if s.get("id") == "browser")
        tick = next(s for s in steps if s.get("id") == "tick")
        self.assertEqual(browser["uses"], "./.github/actions/browser")
        self.assertEqual(browser["with"], {"cache_bust": "life576a"})  # changing this one value forces a cold-cache run
        self.assertLess(ids.index("browser"), ids.index("tick"))
        self.assertEqual(browser["if"], tick["if"])
        self.assertNotIn("playwright", tick["run"])
        self.assertNotIn("pip install", tick["run"])
        jobright = self.doc["jobs"]["jobright"]["steps"]
        shared = next(s for s in jobright if s.get("id") == "jobright_browser")
        self.assertEqual(
            (shared["uses"], shared["with"]), (browser["uses"], browser["with"])
        )  # both jobs run the same step
        resolve = next(s for s in jobright if s.get("id") == "jobright")
        self.assertLess(
            [s.get("id") for s in jobright].index("jobright_browser"), [s.get("id") for s in jobright].index("jobright")
        )
        self.assertNotIn("playwright install", resolve["run"])  # no second install path left in the workflow
        self.assertNotIn("playwright install", wftext.logical("hourly"))
        action = yaml.safe_load((root / ".github/actions/browser/action.yml").read_text())
        inner = action["runs"]["steps"]
        inner_ids = [s.get("id") for s in inner]
        cache = inner[inner_ids.index("chromium_cache")]
        self.assertEqual(
            (cache["uses"], cache["with"]["path"]),
            ("actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830", "~/.cache/ms-playwright"),
        )
        self.assertIn("hashFiles('requirements-browser.lock')", cache["with"]["key"])
        self.assertIn("inputs.cache_bust", cache["with"]["key"])
        libs = inner[inner_ids.index("libs_cache")]  # the system libraries are cached next to Chromium
        self.assertEqual(
            (libs["uses"], libs["with"]["path"]),
            ("actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830", "~/apt-debs"),
        )
        self.assertIn("steps.runner_image.outputs.version", libs["with"]["key"])
        self.assertIn("hashFiles('requirements-browser.lock')", libs["with"]["key"])
        self.assertLess(inner_ids.index("runner_image"), inner_ids.index("libs_cache"))
        self.assertLess(inner_ids.index("libs_cache"), inner_ids.index("browser"))
        inner_browser = inner[inner_ids.index("browser")]
        self.assertEqual(
            inner_browser["env"],
            {
                "CHROMIUM_CACHE_HIT": "${{ steps.chromium_cache.outputs.cache-hit }}",
                "LIBS_CACHE_HIT": "${{ steps.libs_cache.outputs.cache-hit }}",
            },
        )
        run = inner_browser["run"]
        self.assertIn("pip install --require-hashes -r requirements-browser.lock", run)  # integrity unchanged
        self.assertNotIn("while ", run)  # bounded: three tries, never an unbounded loop
        self.assertNotIn("until ", run)
        self.assertNotIn("continue-on-error", inner_browser)  # a used-up retry fails the run
        self.assertLess(run.index("BROWSER_INSTALL_SKIPPED_APT"), run.index("--with-deps"))


class BrowserStepBehaviourTests(unittest.TestCase):
    """the real browser step script, run in bash with fake python/timeout/sleep, shows what a hit, a miss, a retry and a
    used-up retry do.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest("pyyaml unavailable") from None
        path = (
            pathlib.Path(__file__).resolve().parents[2] / ".github/actions/browser/action.yml"
        )  # item 13: the one shared step
        steps = yaml.safe_load(wftext.read(path))["runs"]["steps"]
        cls.script = next(s for s in steps if s.get("id") == "browser")["run"]

    def run_step(self, hit, fail_first, libs_hit="false", saved=None, dpkg_fails=False, download_fails=False):
        """saved: None = no saved libraries, else the list of file names in the restored folder (a manifest is written
        with them).
        """
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
                + self.script
            )
            done = subprocess.run(
                ["bash", "-c", harness],
                capture_output=True,
                text=True,
                env={"CHROMIUM_CACHE_HIT": hit, "LIBS_CACHE_HIT": libs_hit, "HOME": tmp, "PATH": "/usr/bin:/bin"},
            )
            self.debs_left = (
                sorted(p.name for p in pathlib.Path(tmp, "apt-debs").glob("*"))
                if pathlib.Path(tmp, "apt-debs").exists()
                else None
            )
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
        self.assertIn("apt-get download libx=1", calls)  # only the packages the install added

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
        self.assertIsNone(self.debs_left)  # nothing half-saved is left to be cached


class FastPathFirstTests(unittest.TestCase):
    """the Ledger never waits on bulk discovery, and a quick proof run reads few boards."""

    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest("PyYAML not installed") from None
        root = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows"
        cls.hourly = yaml.safe_load(wftext.logical("hourly"))

    def test_bulk_discovery_runs_after_publish_and_finalize(self):
        ids = [s.get("id") for s in self.hourly["jobs"]["finish"]["steps"]]
        fast = [ids.index(i) for i in ("enrich", "fit", "audit", "publish", "holds", "finalize")]
        bulk = [ids.index(i) for i in ("web_scale_up", "titlewatch", "sponsors")]
        self.assertEqual(fast, sorted(fast))
        self.assertLess(max(fast), min(bulk))

    def test_quick_is_a_manual_boolean_and_the_hourly_workflow_no_longer_crawls_employer_boards(self):
        inputs = self.hourly[True]["workflow_dispatch"]["inputs"]
        self.assertEqual((inputs["quick"]["type"], inputs["quick"]["default"]), ("boolean", False))
        self.assertNotIn("boards", [s.get("id") for s in self.hourly["jobs"]["finish"]["steps"]])
        text = wftext.logical("hourly")
        self.assertNotIn("lifeos.run boards --limit", text)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(yaml is None, "PyYAML not installed")
class IsolatedJobTests(unittest.TestCase):
    """Isolated OS jobs blank every workflow-level secret unless a step redeclares only what it needs."""

    def test_no_workflow_level_secret_reaches_an_isolated_job(self):
        import yaml
        from pathlib import Path

        doc = yaml.safe_load(wftext.logical("hourly"))
        shared = {k: v for k, v in (doc.get("env") or {}).items() if "secrets." in str(v)}
        self.assertEqual(sorted(shared), [])
        needs = {"interview": {"HIRING_PIPELINE_PAGE_ID"}}
        for name in ("interview",):
            env = doc["jobs"][name].get("env") or {}
            leaked = sorted(k for k in shared if k not in needs.get(name, ()) and env.get(k) not in ("",))
            self.assertEqual(leaked, [], f"{name} inherits {leaked}")

    def test_python_setup_is_declared_once(self):
        from pathlib import Path

        for path in (Path(__file__).resolve().parents[2] / ".github/workflows").glob("*.yml"):
            self.assertNotIn("setup-python", wftext.read(path), path.name)


DB = {
    "LIFEOS_ACQ_DB_NAME",
    "LIFEOS_ACQ_DB_PASSWORD",
    "LIFEOS_ACQ_DB_USER",
    "LIFEOS_ACQ_SSH_HOST",
    "LIFEOS_ACQ_SSH_KNOWN_HOSTS",
    "LIFEOS_ACQ_SSH_PORT",
    "LIFEOS_ACQ_SSH_PRIVATE_KEY",
    "LIFEOS_ACQ_SSH_USER",
    "LIFEOS_ACQ_SSH_JUMP_KEY",
    "LIFEOS_ACQ_SSH_JUMP_HOST",
    "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS",
}
# a job that has to read the V7 settings page adds ONE more loader, and it loads nothing but the Notion token.
SETTINGS_LOADER_ID = "bws_settings"
SETTINGS_CONSUMER = "${{ steps.bws_settings.outputs.NOTION_JIRA_TOKEN }}"
RUNTIME_CONFIG_LOADER_ID = "bws_runtime_config"
RUNTIME_CONFIG_JOBS = {
    "agenda": {"agenda"},
    "branches": {"report"},
    "capacity-card": {"capacitycard"},
    "diag": {"inbox_view_qa", "interview_ui", "sandbox"},
    "domains": {
        "agenda",
        "amazon",
        "bills",
        "dcc",
        "fit_cloud_backlog",
        "followup",
        "hiring",
        "imessage",
        "interview",
        "jira",
        "last_seen_purge",
        "mail",
        "megibow",
        "network_surface",
        "recruiters",
        "weekly_review",
    },
    "hourly": {"finish", "interview", "jobright", "lensa", "linkedin"},
    "network": {"network"},
    "v3-pages": {"v3pages"},
}  # the exact jobs that carry it: nothing else may
RUNTIME_CONFIG_CONSUMER = "${{ steps.bws_runtime_config.outputs.V7_RUNTIME_CONFIG_SOURCE_ID }}"
SETTINGS_JOBS = {
    "amazon": {"amazon"},
    "bills": {"bills"},
    "calendar": {"calendar"},
    "granola": {"granola"},
    "granola-auto": {"auto"},
    "hermes-stage": {"hermes"},
    "manual-job": {"manual"},
    "profile-sync": {"settings", "sync"},
    "recruiters": {"recruiters"},
    "interview-proof": {"proof"},
    "readiness-live": {"readiness"},
    "domains": {"gate", "outlook", "amazon_retention", "hiring_outcomes", "granola"},
}


def settings_loader_with():
    """The one `with:` the settings loader may have (the Notion token's id is read from UUIDS below, so this file never
    holds a second copy of it).
    """
    return {
        "access_token": "${{ secrets.LIFEOS_BWS_RUNTIME_TOKEN }}",
        "set_env": False,
        "secrets": f"{UUIDS['NOTION_JIRA_TOKEN']} > NOTION_JIRA_TOKEN\n",
    }


def without_address(env):
    """every settings-reading step also carries the Runtime Configuration address (not a secret)"""
    return {k: v for k, v in env.items() if k != "V7_RUNTIME_CONFIG_SOURCE_ID"}


def settings_loader_withs():
    """the settings loader may also carry the Runtime Configuration bootstrap ID (an address, not a credential) in the
    steps that run lifeos.run
    """
    base = settings_loader_with()
    plain = [
        base,
        {
            **base,
            "secrets": base["secrets"] + f"{UUIDS['V7_RUNTIME_CONFIG_SOURCE_ID']} > V7_RUNTIME_CONFIG_SOURCE_ID\n",
        },
    ]
    return plain + [
        {**w, "doppler_compare": "${{ secrets.DOPPLER_TOKEN }}"} for w in plain
    ]  # the Doppler parity compare runs inside the loader


def is_parity(step):
    """the side-by-side Doppler compare that follows a Bitwarden load."""
    return step.get("uses") == "./.github/actions/doppler-load"


def is_settings_loader(step):
    """The settings loader exactly as written: its id, the local loader, the one Notion token, nothing exported job-wide,
    and `continue-on-error` as its.
    """
    with_ = step.get("with")
    if isinstance(with_, dict) and "set" in with_:  # the list is named in sets.yml
        with_ = {**{k: v for k, v in with_.items() if k != "set"}, "secrets": wfsets.load()[with_["set"]]}
    return (
        step.get("id") == SETTINGS_LOADER_ID
        and step.get("uses") == "./.github/actions/bws-load"
        and with_ in settings_loader_withs()
        and step.get("continue-on-error") is True
        and set(step) == {"id", "name", "uses", "with", "continue-on-error"}
    )


DOMAIN_SECRETS = {  # the only secrets each domain job may see
    "granola": DB
    | {
        "LIFEOS_ACQ_SSH_PORT",
        "NOTION_INTERVIEW_TOKEN",
        "HIRING_PIPELINE_PAGE_ID",
        "IMESSAGE_ALERTS_HOST",
        "NOTION_JOB_LEDGER_DATA_SOURCE_ID",
    },
    "weekly_review": {
        "NOTION_JIRA_TOKEN",
        "IMESSAGE_ALERTS_HOST",
        "JIRA_CARD_BLOCK_ID",
        "WEEKLY_REVIEW",
        "WEEKLY_REVIEW_PAGE_ID",
        "NOTION_WEEKLY_REVIEWS_DATA_SOURCE_ID",
        "V7_RUNTIME_CONFIG_SOURCE_ID",
    },  # the Weekly Review job
    "report": {"NTFY_TOPIC"},
    "chain": set(),
    "gate": {"NOTION_JIRA_TOKEN"},
    "imessage": DB
    | {
        "LIFEOS_ACQ_SSH_PORT",
        "NTFY_TOPIC",
        "IMESSAGE_ALERTS_HOST",
        "NOTION_JIRA_TOKEN",
        "FOLLOWUP_TODOIST_PERSONAL_TOKEN",
        "AMAZON_CARD_BLOCK_ID",
        "ATTENTION_CARD_BLOCK_ID",
        "BILLS_CARD_BLOCK_ID",
        "CALENDAR_CARD_BLOCK_ID",
        "HIRING_CARD_BLOCK_ID",
        "JIRA_CARD_BLOCK_ID",
    },
    "jira": DB
    | {
        "JIRA_API_TOKEN",
        "JIRA_BASE_URL",
        "JIRA_BOARDS",
        "JIRA_CARD_BLOCK_ID",
        "JIRA_CARD_PROJECTS",
        "JIRA_EMAIL",
        "JIRA_GTV_CONTEXT_URL",
        "JIRA_GTV_EPIC",
        "JIRA_SITE_URL",
        "LIFEOS_MCP_JIRA_CAPTURE",
        "NOTION_JIRA_TOKEN",
        "NTFY_TOPIC",
    },
    "followup": DB
    | {
        "JIRA_API_TOKEN",
        "JIRA_EMAIL",
        "OUTLOOK_CLIENT_ID",
        "NOTION_API_TOKEN",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_OAUTH_REFRESH_TOKEN",
        "FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH",
        "FOLLOWUP_TODOIST_PERSONAL_TOKEN",
    },
    "outlook": DB | {"GCAL_CALENDAR_ID", "NTFY_TOPIC", "OUTLOOK_CLIENT_ID"},
    "bills": DB
    | {
        "V7_RUNTIME_CONFIG_SOURCE_ID",
        "BILLS_CARD_BLOCK_ID",
        "CALENDAR_CARD_BLOCK_ID",
        "HIRING_CARD_BLOCK_ID",
        "JIRA_CARD_BLOCK_ID",
        "NOTION_BILLS_DATA_SOURCE_ID",
        "NOTION_BILLS_TOKEN",
        "NOTION_JIRA_TOKEN",
    },
    "agenda": DB
    | {"CALENDAR_CARD_BLOCK_ID", "GCAL_CALENDAR_ID", "HIRING_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "NOTION_JIRA_TOKEN"},
    "megibow": DB
    | {
        "GCAL_CALENDAR_ID",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_OAUTH_REFRESH_TOKEN",
        "MEGIBOW_BLOCK_ID",
        "MEGIBOW_EXCLUDED_DOMAINS",
        "MEGIBOW_REVIEW_DB_ID",
        "NOTION_JIRA_TOKEN",
        "NTFY_TOPIC",
        "OUTLOOK_CLIENT_ID",
    },
    "dcc": {
        "V7_RUNTIME_CONFIG_SOURCE_ID",
        "WEEKLY_ARCHIVE_PAGE_ID",
        "AMAZON_CARD_BLOCK_ID",
        "BILLS_CARD_BLOCK_ID",
        "CALENDAR_CARD_BLOCK_ID",
        "DCC_CARD_BLOCK_ID",
        "GITHUB_TOKEN",
        "HIRING_CARD_BLOCK_ID",
        "JIRA_CARD_BLOCK_ID",
        "MAIL_ALERTS_CARD_BLOCK_ID",
        "NOTION_JIRA_TOKEN",
    },
    "recruiters": DB
    | {
        "V7_RUNTIME_CONFIG_SOURCE_ID",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_OAUTH_REFRESH_TOKEN",
        "NOTION_RECRUITERS_DATA_SOURCE_ID",
        "NOTION_RECRUITERS_TOKEN",
        "OUTLOOK_CLIENT_ID",
        "NOTION_API_TOKEN",
        "NOTION_JOB_LEDGER_DATA_SOURCE_ID",
        "V7_CLOSE_BASE_URL",
        "RECRUITERS_SCAN",
        "RECRUITERS_SCAN_AT",  # step outputs, not secrets
        "HIRING_TABLE_BLOCK_ID",
        "NOTION_JIRA_TOKEN",
    },
    "hiring_outcomes": DB
    | {"GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "OUTLOOK_CLIENT_ID"},
    "interview": DB
    | {
        "V7_RUNTIME_CONFIG_SOURCE_ID",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_OAUTH_REFRESH_TOKEN",
        "OUTLOOK_CLIENT_ID",
        "NOTION_RECRUITERS_DATA_SOURCE_ID",
        "NOTION_RECRUITERS_TOKEN",
        "NOTION_API_TOKEN",
        "NOTION_JOB_LEDGER_DATA_SOURCE_ID",
        "V7_CLOSE_BASE_URL",
        "GCAL_CALENDAR_ID",
        "HIRING_PIPELINE_PAGE_ID",
        "NOTION_INTERVIEW_TOKEN",
    },  # the Interview OS step
    "interview_mail": DB
    | {"GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "OUTLOOK_CLIENT_ID"},
    "mail": DB
    | {
        "AMAZON_CARD_BLOCK_ID",
        "BILLS_CARD_BLOCK_ID",
        "CALENDAR_CARD_BLOCK_ID",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_OAUTH_REFRESH_TOKEN",
        "HIRING_CARD_BLOCK_ID",
        "JIRA_CARD_BLOCK_ID",
        "MAIL_ALERTS_CARD_BLOCK_ID",
        "NOTION_JIRA_TOKEN",
        "NTFY_TOPIC",
    },
    "hiring": DB
    | {
        "GCAL_CALENDAR_ID",
        "HIRING_CARD_BLOCK_ID",
        "HIRING_PIPELINE_PAGE_ID",
        "HIRING_STATUS_BLOCK_ID",
        "HIRING_TABLE_BLOCK_ID",
        "JIRA_CARD_BLOCK_ID",
        "NOTION_API_TOKEN",
        "NOTION_INTERVIEW_TOKEN",
        "NOTION_JIRA_TOKEN",
        "NOTION_JOB_LEDGER_DATA_SOURCE_ID",
        "V7_CLOSE_BASE_URL",
    },
    "amazon": {
        "V7_RUNTIME_CONFIG_SOURCE_ID",
        "AMAZON_CARD_BLOCK_ID",
        "BILLS_CARD_BLOCK_ID",
        "CALENDAR_CARD_BLOCK_ID",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_OAUTH_REFRESH_TOKEN",
        "HIRING_CARD_BLOCK_ID",
        "JIRA_CARD_BLOCK_ID",
        "NOTION_AMAZON_DATA_SOURCE_ID",
        "NOTION_AMAZON_TOKEN",
        "NOTION_JIRA_TOKEN",
    },
    "network_surface": DB
    | {
        "FIT_PROFILE_EXTRA_JSON",
        "FIT_PROFILE_JSON",
        "JOBRIGHT_EMAIL",
        "JOBRIGHT_PASSWORD",
        "LIFEOS_ACQ_SSH_JUMP_HOST",
        "LIFEOS_ACQ_SSH_JUMP_KEY",
        "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS",
        "NETWORK_SURFACE_SKIP",
        "NOTION_API_TOKEN",
        "TINYFISH_API_KEY",
        "V7_CLOSE_BASE_URL",
    },  # Step 7c moved here from hourly's finish job
    "last_seen_purge": DB
    | {
        "LIFEOS_ACQ_SSH_JUMP_HOST",
        "LIFEOS_ACQ_SSH_JUMP_KEY",
        "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS",
        "NOTION_API_TOKEN",
        "NTFY_TOPIC",
        "PURGE_FORCE",
    },
    "fit_cloud_backlog": DB
    | {
        "CLOUD_BACKLOG",
        "FIT_PROFILE_EXTRA_JSON",
        "FIT_PROFILE_JSON",
        "IMESSAGE_ALERTS_HOST",
        "LIFEOS_ACQ_SSH_JUMP_HOST",
        "LIFEOS_ACQ_SSH_JUMP_KEY",
        "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS",
        "NOTION_API_TOKEN",
        "PIPELINE",
        "V7_CLOUD_FIT_BACKLOG_BUDGET",
        "V7_CLOUD_FIT_BACKLOG_MAX",
        "V7_CLOUD_FIT_BACKLOG_PAGE",
        "V7_CLOUD_FIT_PARALLEL",
        "V7_CLOUD_FIT_PROFILE_REF",
    },
    "amazon_retention": DB
    | {
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_OAUTH_REFRESH_TOKEN",
        "NOTION_AMAZON_DATA_SOURCE_ID",
        "NOTION_AMAZON_TOKEN",
    },
}
for _job in SETTINGS_JOBS["domains"] - {"gate"}:
    DOMAIN_SECRETS[_job] = DOMAIN_SECRETS[_job] | {"NOTION_JIRA_TOKEN"}
for _job in ("jira", "megibow", "dcc", "interview", "granola"):
    DOMAIN_SECRETS[_job] = DOMAIN_SECRETS[_job] | {"LIFEOS_NOTION_IDS"}


@unittest.skipIf(yaml is None, "PyYAML not installed")
class DomainsWorkflowTests(unittest.TestCase):
    """domains.yml holds the single-provider jobs."""

    @classmethod
    def setUpClass(cls):
        path = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "domains.yml"
        cls.doc = yaml.safe_load(wftext.read(path))

    def test_no_secret_is_declared_at_the_top(self):
        self.assertEqual([k for k, v in (self.doc.get("env") or {}).items() if "secrets." in str(v)], [])

    def test_manual_runs_never_share_a_slot_with_after_tick_runs_and_the_interview_writer_takes_turns(self):
        group = self.doc["concurrency"]["group"]
        self.assertIn("workflow_run", group)
        self.assertIn("'life-os-v7-domains-manual'", group)
        self.assertNotEqual(group.count("life-os-v7-domains'"), 0)
        self.assertIs(self.doc["concurrency"]["cancel-in-progress"], False)
        writer = self.doc["jobs"]["interview"]["concurrency"]
        self.assertEqual(writer, {"group": "life-os-v7-interview-writer", "cancel-in-progress": False})
        self.assertEqual(self.doc["jobs"]["granola"]["concurrency"], writer)
        self.assertEqual(
            [name for name, job in self.doc["jobs"].items() if "concurrency" in job], ["interview", "granola"]
        )
        self.assertEqual(self.doc["jobs"]["granola"]["needs"], ["interview", "gate"])

    def test_recruiters_takes_its_turn_after_the_jira_card_and_still_runs_when_jira_is_red(self):
        """the Recruiters job reads the whole Daily Report page while the Jira card removes and rewrites its blocks"""
        job = self.doc["jobs"]["recruiters"]
        self.assertEqual(job["needs"], ["gate", "jira", "hiring"])
        self.assertEqual(self.doc["jobs"]["hiring"]["needs"], ["gate"])  # hiring goes first
        self.assertIn("always()", self.doc["jobs"]["hiring"]["if"])
        self.assertIn("always()", job["if"])
        self.assertNotIn("!cancelled()", job["if"])
        self.assertIn("needs.gate.result != 'failure'", job["if"])

    def test_only_five_steps_read_the_close_origin_from_its_own_secret(self):
        """S3 / / / E05: the links exist only when the V7_CLOSE_BASE_URL secret is set"""
        seen = [
            (job, step.get("id"))
            for job, spec in self.doc["jobs"].items()
            for step in spec.get("steps", [])
            if "V7_CLOSE_BASE_URL" in str(step)
        ]
        self.assertEqual(
            sorted(seen),
            [
                ("hiring", "hiring"),
                ("interview", "interview"),
                ("network_surface", "network_surface"),
                ("recruiters", "jpoutreach"),
                ("recruiters", "routreach"),
            ],
        )  # the Recruiter Outreach table's action links
        for job, ident in seen:
            step = next(s for s in self.doc["jobs"][job]["steps"] if s.get("id") == ident)
            self.assertEqual(step["env"]["V7_CLOSE_BASE_URL"], "")  # declared empty
        for path in (pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.y*ml"):
            if path.name not in ("domains.yml", "hourly.yml"):
                self.assertNotIn("V7_CLOSE_BASE_URL", wftext.read(path), path.name)
        hourly = yaml.safe_load(
            wftext.logical("hourly")
        )
        named = [
            (job, step.get("id"))
            for job, spec in hourly["jobs"].items()
            for step in spec.get("steps", [])
            if "V7_CLOSE_BASE_URL" in str(step)
        ]
        self.assertEqual(named, [])
        self.assertNotIn("V7_CLOSE_BASE_URL", str(hourly.get("env")))

    def test_the_calendar_mass_delete_brake_is_skipped_only_by_a_manual_input_and_that_run_touches_nothing_else(self):
        inputs = self.doc[True]["workflow_dispatch"]["inputs"]
        self.assertIs(inputs["allow_mass_delete"]["default"], False)  # off unless a person ticks it for one run
        self.assertIs(inputs["calendar_sync"]["default"], False)
        job = self.doc["jobs"]["outlook"]
        self.assertIn("inputs.calendar_sync", job["if"])
        steps = {s.get("id"): s for s in job["steps"]}
        self.assertEqual(
            steps["calsync"]["env"]["CALENDAR_ALLOW_MASS_DELETE"], "${{ inputs.allow_mass_delete && 'true' || '' }}"
        )  # a tick (no inputs) is always ''
        for name, other in self.doc["jobs"].items():
            if name != "outlook":
                self.assertNotIn("CALENDAR_ALLOW_MASS_DELETE", str(other))

    def test_each_job_sees_only_its_own_secrets(self):
        self.assertEqual(set(self.doc["jobs"]), set(DOMAIN_SECRETS))
        for name, job in self.doc["jobs"].items():
            used = {
                k
                for k, v in (job.get("env") or {}).items()
                if "secrets." in str(v) or "steps." in str(v) and ".outputs." in str(v)
            }
            for step in job["steps"]:
                used |= {
                    k
                    for k, v in (step.get("env") or {}).items()
                    if "secrets." in str(v) or "steps." in str(v) and ".outputs." in str(v)
                }
            used -= {"V7_RUNTIME_CONFIG_SOURCE_ID"}
            self.assertLessEqual(used, DOMAIN_SECRETS[name], f"{name}: {sorted(used - DOMAIN_SECRETS[name])}")

    V3_STAGES_THAT_ONLY_WARN = ()  # the mechanism stays

    @staticmethod
    def uncovered_stage_steps(job, exempt=()):
        """> what a job's final step fails to surface."""
        steps, problems = job["steps"], []
        stage = [
            s
            for s in steps
            if s.get("continue-on-error")
            and not is_settings_loader(s)
            and not is_parity(s)
            and s.get("id") != RUNTIME_CONFIG_LOADER_ID
        ]  # the settings loader may fail quietly
        for step in stage:
            if not step.get("id") and step.get("uses") != "./.github/actions/tailscale":
                problems.append(f"stage step without an id: {step.get('name')}")
        last = steps[-1]
        condition, ids = last.get("if", ""), [s["id"] for s in stage if s.get("id")]
        if (
            not str(condition).startswith("${{ always() &&")
            or not str(last.get("run", "")).rstrip().endswith("exit 1")
            or last.get("continue-on-error")
        ):
            problems.append("the last step does not fail the job after every stage")
        elif "contains(steps.*.outcome, 'failure')" not in condition:
            problems += [
                f"{i} not covered"
                for i in ids
                if i not in exempt and f"steps.{i}.outcome == 'failure'" not in condition
            ]
        return problems

    def test_a_failed_stage_step_ends_its_job_red_after_every_stage_ran_and_never_skips_another_job(self):
        """every continue-on-error stage step is covered by its job's final step, so the job result is `failure` and the
        report job pushes the phone once
        """
        jobs = {n: j for n, j in self.doc["jobs"].items() if n not in ("gate", "report", "chain")}
        self.assertEqual(set(jobs), set(due.load_table()))
        for name, job in jobs.items():
            self.assertEqual(
                self.uncovered_stage_steps(job, self.V3_STAGES_THAT_ONLY_WARN if name == "jira" else ()), [], name
            )
            self.assertNotIn("continue-on-error", job)  # the job itself never swallows the failure
            self.assertTrue(any(s.get("continue-on-error") and s.get("id") for s in job["steps"]), name)
            if name != "granola":
                self.assertIn(f"::error title={name}::", job["steps"][-1]["run"])  # names the job only, never a value
            condition = job["if"]  # independence: a red job never skips another one
            self.assertTrue("always()" in condition or "!cancelled()" in condition, name)
            self.assertIn("needs.gate.result != 'failure'", condition, name)
            self.assertLessEqual(
                set(job.get("needs") or []) - {"gate"},
                {"jira", "agenda", "bills", "amazon", "mail", "hiring", "imessage", "interview", "dcc"},
                name,
            )
            if set(job.get("needs") or []) - {"gate"}:
                self.assertIn("always()", condition, name)
        self.assertEqual(self.doc["concurrency"]["cancel-in-progress"], False)

    def test_a_stage_step_added_without_the_surfacing_step_is_caught(self):
        """the check above fails when a stage step is added to a job that does not surface it, or when the surfacing step
        goes.
        """
        import copy

        job = copy.deepcopy(self.doc["jobs"]["bills"])
        self.assertEqual(self.uncovered_stage_steps(job), [])
        gone = copy.deepcopy(job)
        gone["steps"].pop()
        self.assertTrue(self.uncovered_stage_steps(gone))  # surfacing step removed
        quiet = copy.deepcopy(job)
        quiet["steps"].insert(-1, {"name": "New stage", "continue-on-error": True, "run": "true"})
        self.assertTrue(self.uncovered_stage_steps(quiet))
        listed = copy.deepcopy(self.doc["jobs"]["granola"])
        listed["steps"].insert(-1, {"id": "extra", "name": "Another stage", "continue-on-error": True, "run": "true"})
        self.assertEqual(self.uncovered_stage_steps(listed), ["extra not covered"])

    def test_the_jira_job_ends_red_for_every_step_except_the_five_v3_stages_that_only_warn(self):
        """(temporary): the V3 accountability stages warn but do not end the jira job red"""
        job = self.doc["jobs"]["jira"]
        condition = job["steps"][-1]["if"]
        ids = [
            s["id"] for s in job["steps"] if s.get("id") and s["id"] != RUNTIME_CONFIG_LOADER_ID and not is_parity(s)
        ]
        v3 = set(self.V3_STAGES_THAT_ONLY_WARN)
        self.assertLessEqual(v3, set(ids))  # the five exempt ids are real steps of this job
        self.assertNotIn("contains(steps.*.outcome", condition)
        on_list = set(re.findall(r"steps\.(\w+)\.outcome == 'failure'", condition))
        self.assertEqual(on_list & v3, set(), "a V3 stage is on the red list")
        self.assertEqual(on_list, set(ids) - v3, "a jira step is missing from, or extra on, the red list")
        warn = next(s for s in job["steps"] if s.get("name") == "Jira warning (does not fail the run)")
        for i in v3:  # a failed V3 stage still prints its warning
            self.assertIn(f"steps.{i}.outcome == 'failure'", warn["if"], i)
        for name, other in self.doc["jobs"].items():
            if name not in ("jira", "gate", "report", "chain"):
                self.assertEqual(self.uncovered_stage_steps(other), [], name)

    def test_the_writer_check_steps_run_only_on_their_manual_choice_and_take_the_key_from_the_environment(self):
        """decision E: writers-check and writers-verify start only when a person picks them"""
        job = self.doc["jobs"]["jira"]
        steps = {s["id"]: s for s in job["steps"] if s.get("id") in ("jwcheck", "jwverify")}
        self.assertEqual(set(steps), {"jwcheck", "jwverify"})
        self.assertEqual(steps["jwcheck"]["if"], "${{ inputs.jira == 'writers-check' }}")
        self.assertEqual(steps["jwverify"]["if"], "${{ inputs.jira == 'writers-verify' }}")
        self.assertEqual(steps["jwcheck"]["run"], "python -m lifeos.jira.writers contract-check")
        run = steps["jwverify"]["run"]
        self.assertNotIn("${{", run)  # no input is interpolated into the shell
        self.assertEqual(steps["jwverify"]["env"]["JIRA_WRITERS_VERIFY"], "${{ inputs.verify }}")
        self.assertIn('[ "$VERIFY_LIVE" = "yes" ]', run)
        self.assertEqual(run.count("--live"), 2)
        self.assertEqual(run.count("--keep"), 1)  # keep only with yes and the exact fourth word keep
        self.assertIn('[ "$VERIFY_KEEP" = "keep" ]', run)
        for step in steps.values():
            self.assertEqual({k for k in step["env"] if k != "JIRA_WRITERS_VERIFY"}, {"JIRA_API_TOKEN", "JIRA_EMAIL"})
        self.assertEqual(self.doc["permissions"], {"contents": "read"})
        trigger = self.doc.get("on") or self.doc[True]
        self.assertLessEqual(
            {"writers-check", "writers-verify"}, set(trigger["workflow_dispatch"]["inputs"]["jira"]["options"])
        )

    def test_the_daily_reconcile_runs_on_the_due_tick_or_the_manual_reconcile_choice_and_goes_live_only_with_the_live_box(
        self,
    ):
        """AC10: the permanent manual entry point."""
        job = self.doc["jobs"]["jira"]
        step = [s for s in job["steps"] if s.get("id") == "jreconcile"][0]
        self.assertEqual(step["if"], "${{ needs.gate.outputs.due_jira == 'true' || inputs.jira == 'reconcile' }}")
        self.assertEqual(
            step["run"], 'python -m lifeos.run jira-reconcile-daily $([ "$LIVE" = "true" ] && echo --live)'
        )
        self.assertEqual(
            step["env"]["JIRA_RECONCILE_FORCE"],
            "${{ github.event_name == 'workflow_dispatch' && inputs.jira == 'reconcile' && inputs.reconcile_force && 'true' || '' }}",
        )
        self.assertEqual(self.doc[True]["workflow_dispatch"]["inputs"]["reconcile_force"]["default"], False)
        trigger = self.doc.get("on") or self.doc[True]
        self.assertIn("reconcile", trigger["workflow_dispatch"]["inputs"]["jira"]["options"])
        self.assertEqual(self.doc["permissions"], {"contents": "read"})

    def test_v3_pages_runs_after_successful_after_tick_domains_runs_and_keeps_its_cron_fallback_and_live_rule(self):
        """only a successful after-tick domains run starts the V3 page path"""
        path = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows/v3-pages.yml"
        text = wftext.read(path)
        data = yaml.safe_load(text)
        trigger = data.get("on") or data[True]
        self.assertEqual(trigger["workflow_run"], {"workflows": ["domains"], "types": ["completed"]})
        self.assertEqual(trigger["schedule"], [{"cron": "40 * * * *"}])  # the fallback stays
        self.assertEqual(list(trigger["workflow_dispatch"]["inputs"]), ["live"])  # workflow_dispatch is unchanged
        self.assertEqual(
            data["jobs"]["v3pages"]["if"],
            "${{ github.event_name != 'workflow_run' || (github.event.workflow_run.display_title == 'domains (after tick)' && github.event.workflow_run.event == 'workflow_run' && github.event.workflow_run.conclusion == 'success') }}",
        )
        self.assertIn(
            "live_mode: ${{ github.event_name == 'workflow_dispatch' && (inputs.live && 'true' || 'false') || 'follow' }}",
            text,
        )  # a workflow_run start follows V7_LIVE
        self.assertEqual(data["permissions"], {"contents": "read"})
        self.assertEqual(data["concurrency"], {"group": "life-os-v7-v3-pages", "cancel-in-progress": False})
        domains_name = yaml.safe_load(
            wftext.logical("domains")
        )["run-name"]
        self.assertIn("'domains (after tick)'", domains_name)  # the title the condition depends on

    def test_a_new_jira_step_must_be_put_on_the_red_list_or_among_the_v3_stages(self):
        import copy

        job = copy.deepcopy(self.doc["jobs"]["jira"])
        self.assertEqual(self.uncovered_stage_steps(job, self.V3_STAGES_THAT_ONLY_WARN), [])
        job["steps"].insert(-1, {"id": "jnew", "name": "A new jira stage", "continue-on-error": True, "run": "true"})
        self.assertEqual(
            self.uncovered_stage_steps(job, self.V3_STAGES_THAT_ONLY_WARN), ["jnew not covered"]
        )  # it fails until the red list names it
        self.assertEqual(
            self.uncovered_stage_steps(copy.deepcopy(self.doc["jobs"]["jira"])),
            [f"{i} not covered" for i in self.V3_STAGES_THAT_ONLY_WARN],
        )  # without the exemption the V3 stages are uncovered

    def test_a_failing_domain_step_is_a_warning_never_a_failed_run(self):
        for name, job in self.doc["jobs"].items():
            if name in ("report", "gate", "chain"):
                continue
            work = [s for s in job["steps"] if s.get("id") and not str(s.get("uses", "")).endswith("bws-load")]
            self.assertTrue(work and all(s.get("continue-on-error") for s in work), name)

    def test_the_report_job_pushes_the_phone_only_for_a_failed_tick_triggered_run_and_writes_nothing(self):
        job = self.doc["jobs"]["report"]
        self.assertEqual(set(job["needs"]), set(self.doc["jobs"]) - {"report", "chain"})
        condition = job["if"]
        for part in ("always()", "needs.gate.outputs.tick == 'true'", "contains(needs.*.result, 'failure')"):
            self.assertIn(part, condition)
        (step,) = [
            s for s in job["steps"] if "run" in s and not str(s.get("name", "")).startswith("Checkout the exact commit")
        ]
        self.assertIn("lifeos.platform.alerts push --key domain_jobs_failed", step["run"])
        self.assertFalse(step.get("continue-on-error"))  # a push the provider refuses turns the job red
        self.assertEqual(
            sorted(re.findall(r"needs\.(\w+)\.result", step["run"])), sorted(n for n in job["needs"] if n != "gate")
        )


DB = {
    f"LIFEOS_ACQ_{n}"
    for n in ("DB_NAME", "DB_PASSWORD", "DB_USER", "SSH_HOST", "SSH_KNOWN_HOSTS", "SSH_PRIVATE_KEY", "SSH_USER")
}
JUMP = {"LIFEOS_ACQ_SSH_JUMP_KEY", "LIFEOS_ACQ_SSH_JUMP_HOST", "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS"}
TS = {"TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET"}  # loaded by those store jobs for their tailnet join
HRL = {
    "FIT_PROFILE_EXTRA_JSON",
    "FIT_PROFILE_JSON",
    "GMAIL_OAUTH_CLIENT_SECRET",
    "GMAIL_OAUTH_REFRESH_TOKEN",
    "JOBRIGHT_EMAIL",
    "JOBRIGHT_PASSWORD",
    "NOTION_API_TOKEN",
    "OPEN_JOBS_CONTACT",
    "TINYFISH_API_KEY",
    "GMAIL_OAUTH_CLIENT_ID",
}
# The 25 values verified value-for-value in Bitwarden by migration run 37532718917 (name -> secret id).
UUIDS = {
    "V7_RUNTIME_CONFIG_SOURCE_ID": "0053654c-d504-4453-87f7-b5080113d136",
    "FIT_PROFILE_EXTRA_JSON": "3e264d79-3af4-4f2a-a86a-b4db015a68de",
    "FIT_PROFILE_JSON": "31a07198-aa35-450f-913c-b4db015a690d",
    "FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH": "c87d470c-b351-4004-a5ea-b50401885f98",
    "FOLLOWUP_TODOIST_PERSONAL_TOKEN": "3accb8df-ba18-4b2f-b8fc-b50f014be86c",
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
    "INTERVIEW_ACCEPTANCE_JSON": "1051e89d-233b-4050-b6ce-b50401674a4f",
    "INTERVIEW_ACCEPTANCE_NEGATIVE_JSON": "881cd6f8-ee94-4e6d-a20d-b5040167732c",
    "DCC_CARD_BLOCK_ID": "b78d1476-663e-496d-9bc4-b4e1003b61b0",
    "NOTION_JIRA_TOKEN": "53f47cb9-f6bb-4cde-87f4-b4db015ef928",
    "LIFEOS_MCP_JIRA_CAPTURE": "a6d3a41b-c451-4d4d-b1fb-b50e00c2d0f4",  # the jira-capture client bearer
    "NOTION_RECRUITERS_TOKEN": "b7b921fa-3f62-478a-a4f5-b4db015efcf7",
    "NTFY_TOPIC": "83c553e9-23a6-45aa-9006-b4db015f00ac",
    "OPEN_JOBS_CONTACT": "7b62e076-8a46-4228-9977-b4db015f0463",
    "TINYFISH_API_KEY": "f45f29e2-acbc-4be7-9749-b4db015f0817",
    "TAILSCALE_OAUTH_CLIENT_ID": "454e12df-637f-4f65-9ec1-b4e30052942f",  # record ids only, never values
    "TAILSCALE_OAUTH_CLIENT_SECRET": "e54f0a84-84c3-47d8-8b5b-b4e30052d6e9",
    "IMESSAGE_ALERTS_HOST": "6590208b-506d-4238-ab10-b4e30051b25f",
    "LIFEOS_ACQ_SSH_JUMP_KEY": "192db2d1-be66-4fca-b9b5-b4e900240507",  # the v7-store-fwd relay key
    # copies of former GitHub values, read through the masking loader (identifiers and addresses, never a plain.
    "LIFEOS_ACQ_SSH_JUMP_HOST": "4d0c7449-ed0a-46c1-9c2c-b503015f8d54",
    "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS": "82efa413-6b9c-4c5c-b0d2-b503015f8e3d",
    "GMAIL_OAUTH_CLIENT_ID": "8657d1ed-2cde-4298-bd62-b50301523e01",
    "OUTLOOK_CLIENT_ID": "2b2c73a9-7a1e-44b9-95f0-b50301509b09",
    "BUZZ_STAGE_KEY": "f92e4a37-0289-40a6-b31f-b503015f8824",
    "BUZZ_STAGE_HERMES_PUBKEY": "486e59dc-2cfe-4ce1-80e4-b503015b80b0",
    "BUZZ_URL_LIFE_OS": "6c067b5b-b2c6-4f6b-b6f2-b503015a2e3f",
    "NOTION_FIT_SYNC_TOKEN": "bb1503dc-1603-43b8-a315-b5030156ca0d",
    "V7_ARCHIVE_TOKEN": "c7b3ff1a-e0d9-456a-b8c6-b5030158f900",
}
# The one action that may receive loader outputs through `with:`: the pinned Tailscale join, and only its two.
TAILSCALE_ACTION = "./.github/actions/tailscale"
TAILSCALE_WITH = {"oauth-client-id": "TAILSCALE_OAUTH_CLIENT_ID", "oauth-secret": "TAILSCALE_OAUTH_CLIENT_SECRET"}
# Least privilege: the credentials each job may load from Bitwarden (a conscious decision)
HRL_PREP = HRL - {
    "FIT_PROFILE_EXTRA_JSON",
    "FIT_PROFILE_JSON",
    "JOBRIGHT_EMAIL",
    "JOBRIGHT_PASSWORD",
    "OPEN_JOBS_CONTACT",
    "TINYFISH_API_KEY",
}
HRL_LANE = HRL - {
    "GMAIL_OAUTH_CLIENT_ID",
    "GMAIL_OAUTH_CLIENT_SECRET",
    "GMAIL_OAUTH_REFRESH_TOKEN",
    "OPEN_JOBS_CONTACT",
}
LOADED = {
    "branches": {"report": {"NOTION_JIRA_TOKEN"}},
    "agenda": {"agenda": DB | {"NOTION_JIRA_TOKEN"}},
    "bills": {"bills": DB | {"NOTION_BILLS_TOKEN"}},
    "calendar": {"calendar": DB | {"OUTLOOK_CLIENT_ID"}},
    "hourly": {
        "prep": DB | JUMP | TS | HRL_PREP,
        "jobright": DB | JUMP | TS | HRL_LANE,
        "linkedin": DB | JUMP | TS | HRL_LANE,
        "lensa": DB | JUMP | TS | HRL_LANE,
        "finish": DB
        | JUMP
        | TS
        | HRL
        | {"NTFY_TOPIC", "TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "IMESSAGE_ALERTS_HOST"},
        "interview": {"NOTION_INTERVIEW_TOKEN", "INTERVIEW_ACCEPTANCE_JSON", "INTERVIEW_ACCEPTANCE_NEGATIVE_JSON"},
        "report": {"NTFY_TOPIC"},
    },
    "network": {"network": DB | JUMP | TS | {"NOTION_API_TOKEN"}},
    "boards": {"boards": DB | JUMP | TS},
    "v3-pages": {"v3pages": {"JIRA_API_TOKEN", "JIRA_EMAIL", "NOTION_JIRA_TOKEN"}},
    "capacity-card": {
        "capacitycard": {"TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "NOTION_JIRA_TOKEN"}
    },  # the retired AI Capacity stage reads nothing
    "outlook": {"outlook": DB | {"OUTLOOK_CLIENT_ID"}},
    "profile-sync": {"sync": DB | {"NOTION_FIT_SYNC_TOKEN"}},
    "granola-auto": {
        "auto": DB - {"LIFEOS_ACQ_SSH_PORT"}
        | {
            "NOTION_INTERVIEW_TOKEN",
            "TAILSCALE_OAUTH_CLIENT_ID",
            "TAILSCALE_OAUTH_CLIENT_SECRET",
            "IMESSAGE_ALERTS_HOST",
        }
    },
    "granola": {
        "granola": {
            "NOTION_INTERVIEW_TOKEN",
            "TAILSCALE_OAUTH_CLIENT_ID",
            "TAILSCALE_OAUTH_CLIENT_SECRET",
            "IMESSAGE_ALERTS_HOST",
        }
    },
    "manual-job": {"manual": DB | {"NOTION_INTERVIEW_TOKEN"}},
    "hermes-stage": {
        "hermes": {"JIRA_API_TOKEN", "JIRA_EMAIL", "BUZZ_STAGE_KEY", "BUZZ_STAGE_HERMES_PUBKEY", "BUZZ_URL_LIFE_OS"}
    },
    "readiness-live": {"readiness": {"JIRA_API_TOKEN", "JIRA_EMAIL"}},
    "interview-proof": {"proof": {"NOTION_INTERVIEW_TOKEN"}},
    "recruiters": {
        "recruiters": DB
        | {
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "NOTION_RECRUITERS_TOKEN",
            "GMAIL_OAUTH_CLIENT_ID",
            "OUTLOOK_CLIENT_ID",
        }
    },
    "watchdog": {"watch": {"NTFY_TOPIC"}},
    "safety": {"safety": DB | {"FIT_PROFILE_EXTRA_JSON", "FIT_PROFILE_JSON", "NTFY_TOPIC"}},
    "amazon": {
        "amazon": {
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "NOTION_AMAZON_TOKEN",
            "GMAIL_OAUTH_CLIENT_ID",
        }
    },
    "fit-edge-test": {
        "fit_edge": {"TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET", "IMESSAGE_ALERTS_HOST"}
    },
    "diag": {
        "diag": DB,
        "trace": DB | {"GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "GMAIL_OAUTH_CLIENT_ID"},
        "sandbox": {"NOTION_API_TOKEN"},
        "interview_ui": {"NOTION_INTERVIEW_TOKEN"},
        "inbox_view_qa": {"NOTION_API_TOKEN"},
        "report": DB | {"NOTION_API_TOKEN"},
    },
}
PILOTS = ("watchdog", "safety", "amazon", "diag")
SECRET_FREE = ("pr-tests", "suite-timing", "mariadb-race")


@unittest.skipIf(yaml is None, "PyYAML not installed")
class BitwardenConsumerTests(unittest.TestCase):
    """step 2: rules, not pins."""

    ACTION = "./.github/actions/bws-load"  # the one local loader
    PINNED = "./.github/actions/sm-action"
    OUTPUT = re.compile(r"\$\{\{ steps\.(\w+)\.outputs\.(\w+) \}\}")  # a loaded value, read from the loader's output
    PAUSE = "sleep $((30 + RANDOM % 45))"

    @classmethod
    def setUpClass(cls):
        root = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
        cls.text = {stem: text for stem, text in wftext.workflow_texts().items() if stem not in SECRET_FREE}
        cls.doc = {n: yaml.safe_load(t) for n, t in cls.text.items()}

    def loaders(self, name, job):
        """The job's credential loader."""
        return [
            (i, s)
            for i, s in enumerate(self.doc[name]["jobs"][job]["steps"])
            if s.get("uses") == self.ACTION and s.get("id") not in (SETTINGS_LOADER_ID, RUNTIME_CONFIG_LOADER_ID)
        ]

    def settings_loaders(self, name, job):
        return [
            (i, s)
            for i, s in enumerate(self.doc[name]["jobs"][job]["steps"])
            if s.get("uses") == self.ACTION and s.get("id") == SETTINGS_LOADER_ID
        ]

    def jobs_with_loaders(self, name):
        return [job for job in self.doc[name]["jobs"] if self.loaders(name, job)]

    def settings_jobs(self, name):
        return {job for job in self.doc[name]["jobs"] if self.settings_loaders(name, job)}

    def loaded(self, name, job):
        return {
            m.group(2)
            for m in re.finditer(r"^([0-9a-f-]{36}) > (\w+)$", self.loaders(name, job)[0][1]["with"]["secrets"], re.M)
        }

    def test_the_runtime_config_loader_is_the_bootstrap_id_alone_in_exactly_its_jobs(self):
        """the compare-only read loads the one existing bootstrap ID (an address) through its own step, never exported
        job-wide, and a failed load only means no.
        """
        for name, doc in self.doc.items():
            carriers = {
                job
                for job, body in doc["jobs"].items()
                if any(st.get("id") == RUNTIME_CONFIG_LOADER_ID for st in body["steps"])
            }
            self.assertEqual(carriers, RUNTIME_CONFIG_JOBS.get(name, set()), name)
            for job in carriers:
                (step,) = [st for st in doc["jobs"][job]["steps"] if st.get("id") == RUNTIME_CONFIG_LOADER_ID]
                self.assertEqual(step["uses"], self.ACTION, (name, job))
                self.assertIs(step.get("continue-on-error"), True, (name, job))
                self.assertEqual(step["with"]["access_token"], "${{ secrets.LIFEOS_BWS_RUNTIME_TOKEN }}", (name, job))
                self.assertEqual(str(step["with"]["set_env"]).lower(), "false", (name, job))
                self.assertEqual(
                    step["with"]["secrets"].strip(),
                    f"{UUIDS['V7_RUNTIME_CONFIG_SOURCE_ID']} > V7_RUNTIME_CONFIG_SOURCE_ID",
                    (name, job),
                )
                self.assertNotIn("V7_RUNTIME_CONFIG_SOURCE_ID", doc["jobs"][job].get("env") or {}, (name, job))

    def test_every_loader_is_the_local_action_with_the_exact_ids_and_nothing_that_could_bypass_it(self):
        for name, doc in self.doc.items():
            text, jobs = self.text[name], self.jobs_with_loaders(name)
            self.assertTrue(jobs, name)
            self.assertIn("LIFEOS_BWS_RUNTIME_TOKEN", re.findall(r"secrets\.(\w+)", text), name)
            self.assertFalse(
                [n for n in UUIDS if re.search(rf"secrets\.{n}\b", text)], name
            )  # no migrated value is read from GitHub any more
            self.assertEqual(
                re.findall(r"uses:\s*(\S*bws-load)", text),
                [self.ACTION] * (len(jobs) + len(SETTINGS_JOBS.get(name, ())) + len(RUNTIME_CONFIG_JOBS.get(name, ()))),
                name,
            )
            self.assertNotIn("bitwarden/", text, name)
            if name != "domains":
                self.assertEqual(set(jobs), set(LOADED[name]), name)
            for job in jobs:
                ((at, loader),) = self.loaders(name, job)
                steps, names = doc["jobs"][job]["steps"], self.loaded(name, job)
                self.assertTrue(names and names <= set(UUIDS), (name, job))
                self.assertTrue(
                    names <= DOMAIN_SECRETS[job] | TS if name == "domains" else names == LOADED[name][job],
                    (name, job, sorted(names)),
                )
                self.assertEqual(
                    steps[0].get("name"), "Checkout the exact commit (own fetch, token never stored)", (name, job)
                )
                self.assertEqual(loader["with"]["access_token"], "${{ secrets.LIFEOS_BWS_RUNTIME_TOKEN }}", (name, job))
                self.assertEqual(
                    sorted(re.findall(r"^([0-9a-f-]{36}) > (\w+)$", loader["with"]["secrets"], re.M)),
                    sorted((UUIDS[n], n) for n in names),
                    (name, job),
                )
                self.assertEqual(len(re.findall(r" > ", loader["with"]["secrets"])), len(names), (name, job))
                self.assertFalse({"if", "env", "continue-on-error"} & set(loader), (name, job))
                self.assertTrue(
                    {"access_token", "secrets"}
                    <= set(loader["with"])
                    <= {"access_token", "secrets", "set_env", "doppler_compare"},
                    (name, job),
                )
                if name not in PILOTS:
                    self.assertEqual(loader["id"], f"bws_{job}", (name, job))  # deterministic, unique id
                    self.assertIs(loader["with"]["set_env"], False, (name, job))  # nothing is exported job-wide
                    self.assertEqual(set(loader), {"id", "name", "uses", "with"}, (name, job))

    def test_the_local_loader_is_the_pinned_action_once_plus_its_one_retry_and_no_fallback(self):
        """(Bitwarden's 429 / 5xx gets one retry after a jittered pause, and a failed retry fails the job) is written
        once, here, instead of in 45 jobs.
        """
        root = pathlib.Path(__file__).resolve().parents[2] / ".github"
        action = yaml.safe_load((root / "actions" / "bws-load" / "action.yml").read_text())
        sets, first, pause, retry, names, doppler = action["runs"]["steps"]
        failed = "${{ steps.first.outcome == 'failure' }}"
        passed = {
            "access_token": "${{ inputs.access_token }}",
            "secrets": "${{ inputs.set != '' && steps.sets.outputs.list || inputs.secrets }}",
            "set_env": "${{ inputs.set_env }}",
        }  # the list is the named set, else the inline one
        self.assertEqual(action["runs"]["using"], "composite")
        self.assertEqual(first, {"id": "first", "continue-on-error": True, "uses": self.PINNED, "with": passed})
        self.assertEqual(
            pause, {"if": failed, "shell": "bash", "run": self.PAUSE}
        )  # a jittered pause, only when the first load failed
        self.assertEqual(
            retry, {"id": "retry", "if": failed, "uses": self.PINNED, "with": passed}
        )  # the same pinned action and ids
        self.assertEqual(names["id"], "names")
        self.assertIn("inputs.doppler_compare != ''", names["if"])
        self.assertEqual(
            {k: doppler.get(k) for k in ("id", "continue-on-error", "uses")},
            {"id": "doppler", "continue-on-error": True, "uses": "./.github/actions/doppler-load"},
        )
        self.assertEqual(doppler["with"]["token"], "${{ inputs.doppler_compare }}")
        self.assertEqual(set(doppler["with"]), {"token", "names", "bitwarden"})
        needed = {n for text in self.text.values() for n in re.findall(r"^\s+[0-9a-f-]{36} > (\w+)$", text, re.M)}
        self.assertTrue(
            needed <= set(action["outputs"]), sorted(needed - set(action["outputs"]))
        )  # every loaded name has an output
        for key, spec in action["outputs"].items():
            self.assertEqual(
                spec["value"], "${{ steps.first.outputs.%s || steps.retry.outputs.%s }}" % (key, key), key
            )  # the first load's value, else its one retry's
        for path in sorted(root.rglob("*.y*ml")):
            if path != root / "actions" / "bws-load" / "action.yml":
                self.assertNotRegex(
                    wftext.read(path), r"uses:\s*bitwarden/", path.name
                )  # the third-party action is used nowhere else

    def test_only_steps_that_run_a_command_receive_a_loaded_value_and_the_loader_carries_exactly_what_they_use(self):
        for name, doc in self.doc.items():
            if name in PILOTS:
                continue
            self.assertFalse(
                [k for k, v in (doc.get("env") or {}).items() if k in UUIDS], name
            )  # nothing at workflow level
            for job, body in doc["jobs"].items():
                self.assertFalse([k for k, v in (body.get("env") or {}).items() if k in UUIDS and v != ""], (name, job))
                found = self.loaders(name, job)
                settings_at = min([i for i, _ in self.settings_loaders(name, job)], default=None)
                if not found:
                    for i, step in enumerate(body["steps"]):
                        for k, v in (step.get("env") or {}).items():
                            if k in UUIDS:
                                self.assertIn(
                                    (k, str(v).strip()),
                                    (
                                        ("NOTION_JIRA_TOKEN", SETTINGS_CONSUMER),
                                        (
                                            "V7_RUNTIME_CONFIG_SOURCE_ID",
                                            SETTINGS_CONSUMER.replace(
                                                "NOTION_JIRA_TOKEN", "V7_RUNTIME_CONFIG_SOURCE_ID"
                                            ),
                                        ),
                                    ),
                                    (name, job, step.get("name")),
                                )
                                self.assertTrue(
                                    settings_at is not None and i > settings_at, (name, job, step.get("name"))
                                )
                    self.assertFalse(
                        [
                            v
                            for step in body["steps"]
                            for v in (step.get("with") or {}).values()
                            if "steps.bws_" in str(v) and not is_parity(step)
                        ],
                        (name, job),
                    )
                    continue
                ((at, loader),) = found
                seen = set()
                for i, step in enumerate(body["steps"]):
                    label = step.get("name") or step.get("uses")
                    got = set()
                    for key, value in (step.get("env") or {}).items():
                        if key in UUIDS and str(value).strip() == SETTINGS_CONSUMER:
                            self.assertEqual(
                                (key, settings_at is not None and i > settings_at),
                                ("NOTION_JIRA_TOKEN", True),
                                (name, job, label),
                            )
                            continue
                        if key == "V7_RUNTIME_CONFIG_SOURCE_ID" and str(value).strip() == RUNTIME_CONFIG_CONSUMER:
                            rc_at = min(
                                [j for j, st in enumerate(body["steps"]) if st.get("id") == RUNTIME_CONFIG_LOADER_ID],
                                default=None,
                            )
                            self.assertTrue(
                                rc_at is not None and i > rc_at and job in RUNTIME_CONFIG_JOBS.get(name, ()),
                                (name, job, label),
                            )
                            continue
                        if key == "V7_RUNTIME_CONFIG_SOURCE_ID" and str(value).strip() == SETTINGS_CONSUMER.replace(
                            "NOTION_JIRA_TOKEN", key
                        ):
                            self.assertTrue(settings_at is not None and i > settings_at, (name, job, label))
                            continue
                        if key in UUIDS:
                            m = self.OUTPUT.fullmatch(str(value).strip())
                            alias = {
                                ("interview", "INTERVIEW_ACCEPTANCE_JSON"): "INTERVIEW_ACCEPTANCE_NEGATIVE_JSON"
                            }.get((job, key))
                            self.assertTrue(
                                m and m.group(2) in {key, alias} and m.group(1) == loader["id"], (name, job, label, key)
                            )
                            got.add(m.group(2))
                            self.assertGreater(i, at, (name, job, label))
                    if step is loader:
                        continue
                    passed = {k: self.OUTPUT.fullmatch(str(v).strip()) for k, v in (step.get("with") or {}).items()}
                    passed = {k: m for k, m in passed.items() if m}
                    if step.get("uses") == TAILSCALE_ACTION:
                        self.assertEqual(
                            {k: (m.group(1), m.group(2)) for k, m in passed.items()},
                            {k: (loader["id"], n) for k, n in TAILSCALE_WITH.items()},
                            (name, job),
                        )
                        self.assertGreater(i, at, (name, job, label))
                        seen |= set(TAILSCALE_WITH.values())
                    else:
                        self.assertEqual(passed, {}, (name, job, label))  # no other action receives a loader output
                    if "run" not in step or "uses" in step:
                        self.assertEqual(got, set(), (name, job, label))
                    seen |= got
                self.assertEqual(
                    seen, self.loaded(name, job), (name, job)
                )  # the loader carries exactly what its consumers use

    def test_the_settings_loader_is_exactly_the_notion_token_in_exactly_the_jobs_that_read_the_settings_page(self):
        """one extra loader step, in these jobs only, loading nothing but NOTION_JIRA_TOKEN, never failing the job, after
        the credential loader and the checkout.
        """
        for name in self.doc:
            self.assertEqual(self.settings_jobs(name), SETTINGS_JOBS.get(name, set()), name)
            for job in self.settings_jobs(name):
                ((at, loader),) = self.settings_loaders(name, job)
                steps = self.doc[name]["jobs"][job]["steps"]
                self.assertTrue(is_settings_loader(loader), (name, job))
                self.assertTrue(all(i < at for i, _ in self.loaders(name, job)), (name, job))
                self.assertEqual(
                    steps[0].get("name"), "Checkout the exact commit (own fetch, token never stored)", (name, job)
                )
                consumers = [s for s in steps if SETTINGS_CONSUMER in str((s.get("env") or {}).values())]
                self.assertTrue(consumers, (name, job))  # a loader nobody uses is not there
                for step in consumers:
                    self.assertGreater(steps.index(step), at, (name, job))
                    self.assertTrue(
                        "lifeos.platform.settings" in str(step.get("run")) or "lifeos.run" in str(step.get("run")),
                        (name, job, step.get("name")),
                    )  # only a command that reads the page gets the token

    def test_every_workflow_is_read_only_queues_its_runs_and_bounds_every_job(self):
        for name, doc in self.doc.items():
            self.assertTrue({"pull_request_target", "issue_comment"}.isdisjoint(doc[True]), name)
            extra = {
                "watchdog": {"issues": "write"},
                "calendar": {"id-token": "write"},
                "agenda": {"id-token": "write"},
            }  # id-token is the keyless Google sign-in
            self.assertEqual({k: v for k, v in doc["permissions"].items() if v != "read"}, extra.get(name, {}), name)
            self.assertTrue(doc.get("concurrency") or name == "watchdog", name)  # runs queue
            self.assertIs(
                (doc.get("concurrency") or {}).get("cancel-in-progress", False), False, name
            )  # and a waiting run is never cancelled by the next
            for job, body in doc["jobs"].items():
                self.assertIsInstance(body.get("timeout-minutes"), int, (name, job))  # every job is bounded
            self.assertFalse(
                re.findall(r"run:[^\n]*\$\{\{ *secrets\.", self.text[name]), name
            )  # a secret is never pasted into a command line

    def test_a_stopped_granola_bridge_fails_the_job_so_the_report_job_pushes_the_phone(self):
        """the bridge step keeps continue-on-error so the warning prints, then a final step fails the job"""
        job = self.doc["domains"]["jobs"]["granola"]
        steps = job["steps"]
        bridge = next(s for s in steps if s.get("id") == "gran")
        self.assertIs(bridge.get("continue-on-error"), True)
        self.assertNotIn("continue-on-error", job)
        last = steps[-1]
        self.assertEqual((last["run"], last["if"]), ("exit 1", "${{ always() && steps.gran.outcome == 'failure' }}"))
        self.assertIn("Granola auto warning", steps[-2]["name"])  # the warning still prints first
        self.assertIn("granola", self.doc["domains"]["jobs"]["report"]["needs"])  # and the report job watches this job


@unittest.skipIf(yaml is None, "PyYAML not installed")
class ProfileSyncWorkflowTests(unittest.TestCase):
    """The Fit profile sync reads the store credentials from Bitwarden like any store job, writes nothing to Bitwarden,
    and holds one Notion read token for.
    """

    def setUp(self):
        self.root = pathlib.Path(".github/workflows")
        self.text = (self.root / "profile-sync.yml").read_text()
        self.doc = yaml.safe_load(self.text)

    def test_nothing_in_it_can_write_to_bitwarden(self):
        self.assertNotIn("bws secret", self.text)
        self.assertNotIn("sdk-sm/releases", self.text)
        self.assertEqual(re.findall(r"uses:\s*(\S*bws-load)", self.text), ["./.github/actions/bws-load"] * 3)

    def test_only_it_can_see_the_notion_sync_token(self):
        for path in self.root.glob("*.yml"):
            if path.stem != "profile-sync":
                self.assertNotIn("NOTION_FIT_SYNC_TOKEN", wftext.read(path), path.stem)

    def test_it_has_no_schedule_no_top_level_secret_and_runs_one_stage(self):
        on = self.doc.get("on") or self.doc.get(True)
        self.assertEqual(sorted(on), ["workflow_dispatch", "workflow_run"])
        self.assertNotIn("schedule", self.text.replace("No schedule of its own", ""))
        self.assertNotIn("env", self.doc)
        self.assertEqual(self.text.count("python -m lifeos.run"), 1)
        self.assertIn("python -m lifeos.run fit-profile-sync", self.text)

    def test_it_is_a_dry_run_unless_ticked_or_following_a_tick_with_the_variable_on(self):
        self.assertFalse(
            self.doc["on"]["workflow_dispatch"]["inputs"]["live"]["default"]
            if "on" in self.doc
            else self.doc[True]["workflow_dispatch"]["inputs"]["live"]["default"]
        )
        job = self.doc["jobs"]["sync"]
        self.assertEqual(job["needs"], ["settings"])
        self.assertIn(
            "needs.settings.outputs.fit_profile_sync == 'on'", job["if"]
        )  # the switch now comes from the V7 settings page...
        self.assertIn("github.event.workflow_run.conclusion == 'success'", job["if"])
        self.assertIn("github.event_name == 'workflow_dispatch'", job["if"])
        self.assertEqual(
            self.doc["jobs"]["settings"]["outputs"]["fit_profile_sync"],
            "${{ steps.settings.outputs.FIT_PROFILE_SYNC }}",
        )  # ...
        self.assertNotIn("vars.FIT_PROFILE_SYNC", job["if"])


class SweepFailureIsItsOwnLaneTests(unittest.TestCase):
    """it is its own failed lane in the report job's push, while credentials, the gate and the unit tests still block."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.doc = yaml.safe_load(
                wftext.logical("hourly")
            )
        except NameError:
            raise unittest.SkipTest("pyyaml unavailable") from None
        cls.steps = cls.doc["jobs"]["prep"]["steps"]

    def test_the_sweep_step_cannot_fail_the_prep_job_and_its_outcome_is_a_prep_output(self):
        sweep = next(s for s in self.steps if s.get("id") == "sweep")
        self.assertTrue(sweep.get("continue-on-error"))
        self.assertIn("python -m lifeos.sources.newsletters.sweep", sweep["run"])
        self.assertNotIn("|| true", sweep["run"])
        self.assertEqual(self.doc["jobs"]["prep"]["outputs"]["sweep"], "${{ steps.sweep.outcome }}")
        self.assertNotIn("continue-on-error", self.doc["jobs"]["prep"])  # only the one step is tolerated, never the job
        for later in self.steps[self.steps.index(sweep) + 1 :]:
            self.assertNotIn("steps.sweep", str(later.get("if", "")), later.get("name"))

    def test_credentials_and_gate_still_fail_prep_and_so_block_finish(self):
        sweep_at = next(i for i, s in enumerate(self.steps) if s.get("id") == "sweep")
        before = self.steps[:sweep_at]
        for prefix in ("Load the migrated credentials", "Gate - skip"):
            step = next(s for s in before if s.get("name", "").startswith(prefix))
            self.assertNotIn("continue-on-error", step, prefix)
        tolerated = [
            s.get("name", "") for s in self.steps[:sweep_at] if s.get("continue-on-error") and not is_parity(s)
        ]
        self.assertEqual(len(tolerated), 2)
        self.assertEqual(
            sum(name.startswith("LIFE-854 - start the employer-board window") for name in tolerated), 0
        )  # ...
        self.assertEqual(sum("tailnet" in name for name in tolerated), 1)
        self.assertEqual(sum(name.startswith("Start the early domains run") for name in tolerated), 1)  # ...
        finish = self.doc["jobs"]["finish"]
        self.assertEqual(finish["needs"], ["prep"])
        self.assertIn("needs.prep.result == 'success'", finish["if"])

    def test_the_report_job_names_a_failed_sweep_as_its_own_lane_and_pushes_once(self):
        import os
        import subprocess

        report = next(s for s in self.doc["jobs"]["report"]["steps"] if "finish job report" in s.get("name", ""))
        self.assertIn("sweep=${{ needs.prep.outputs.sweep }}", report["run"])

        def run(prep="success", sweep="success", finish="success"):
            values = {"needs.prep.result": prep, "needs.prep.outputs.sweep": sweep, "needs.finish.result": finish}
            script = re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", lambda m: values.get(m.group(1), "success"), report["run"])
            script = script.replace(
                "python3 -m lifeos.platform.alerts push", "echo PUSH"
            )  # the real push is never called from a test
            done = subprocess.run(
                ["bash", "-e", "-c", script],
                env={**os.environ, "STAGES": "", "CODES": ""},
                capture_output=True,
                text=True,
                check=False,
            )
            return done.returncode, done.stdout

        code, out = run(sweep="failure")  # the sweep failed, everything else ran
        self.assertEqual(code, 1)
        self.assertIn("FAILED LANES: sweep", out)
        self.assertEqual(out.count("PUSH"), 1)
        self.assertIn("Failed lanes: sweep.", out)
        code, out = run(prep="failure", sweep="")
        self.assertEqual((code, "FAILED LANES: prep" in out), (1, True))
        self.assertEqual(run(sweep="skipped"), (0, ""))  # a cloud-backlog run never sweeps
        self.assertEqual(run(), (0, ""))
        code, out = run(sweep="failure", finish="failure")  # both: two lanes, one push
        self.assertIn("FAILED LANES: sweep finish", out)
        self.assertEqual(out.count("PUSH"), 1)
        self.assertIn("always()", self.doc["jobs"]["report"]["if"])


class FinishFailuresAreSurfacedTests(unittest.TestCase):
    """a failed browser install costs only the Jobright lane, and no `continue-on-error` step in the finish job fails
    silently: each is a lane in the lanes.
    """

    SILENT = {
        None: "the tailnet join (no id): a failed join only means no Oracle fallback (LIFE-85)",
        "review": "dispatch-only report: a person started it and reads the log",
        "explain": "dispatch-only report: a person started it and reads the log",
        "boardcheck": "dispatch-only report: a person started it and reads the log",
        "funnel": "dispatch-only report: a person started it and reads the log",
        "boards_preview": "dispatch-only report: a person started it and reads the log",
        "bws_runtime_config": "the compare-only source ID load (LIFE-858): a failed load only means no compare; the step after it runs exactly as before",
    }

    @classmethod
    def setUpClass(cls):
        try:
            cls.doc = yaml.safe_load(
                wftext.logical("hourly")
            )
        except NameError:
            raise unittest.SkipTest("pyyaml unavailable") from None
        cls.steps = cls.doc["jobs"]["finish"]["steps"]
        cls.lanes = next(s for s in cls.steps if s.get("id") == "lanes")

    def lane_result(self, failed_step):
        import os
        import subprocess
        import tempfile

        script = re.sub(
            r"\$\{\{\s*([^}]+?)\s*\}\}",
            lambda m: "failure" if m.group(1) == f"steps.{failed_step}.outcome" else "success",
            self.lanes["run"],
        )
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory, "out")
            done = subprocess.run(
                ["bash", "-e", "-c", script],
                env={**os.environ, "GITHUB_OUTPUT": str(output)},
                capture_output=True,
                text=True,
                check=False,
            )
            return done.returncode, next(line for line in output.read_text().splitlines() if line.startswith("failed="))

    def test_every_continue_on_error_step_is_a_lane_or_listed_as_silent_with_a_reason(self):
        listed = set(re.findall(r"=\$\{\{ steps\.(\w+)\.outcome \}\}", self.lanes["run"]))
        for step in self.steps:
            if step.get("continue-on-error") and step.get("id") != "lanes" and not is_parity(step):
                ident = step.get("id")
                self.assertTrue(
                    ident in listed or (ident in self.SILENT and self.SILENT[ident]),
                    f"silent and unlisted: {ident or step.get('name')}",
                )
        self.assertTrue(listed.isdisjoint(self.SILENT))  # a step is one or the other, never both
        for ident in self.SILENT:
            if ident:
                self.assertIn(ident, [s.get("id") for s in self.steps])  # a stale entry fails here

    def test_the_network_surface_and_alerts_steps_are_lanes_and_still_do_not_stop_the_steps_after_them(self):
        for ident in ("alerts",):
            step = next(s for s in self.steps if s.get("id") == ident)
            self.assertTrue(step.get("continue-on-error"), ident)  # every later stage still runs
            self.assertEqual(
                self.lane_result(ident), (1, f"failed={ident}")
            )  # the run ends red and names only the lane
            for later in self.steps[self.steps.index(step) + 1 :]:
                if "annotation" not in later.get("name", ""):  # the warning step reads the outcome on purpose
                    self.assertNotIn(f"steps.{ident}.", str(later.get("if", "")), later.get("name"))
        self.assertEqual(self.lane_result("none"), (0, "failed="))  # nothing changes when they succeed

    def test_a_failed_browser_install_is_only_the_jobright_lane_and_the_tick_skips_only_that_stage(self):
        browser = next(s for s in self.steps if s.get("id") == "browser")
        tick = next(s for s in self.steps if s.get("id") == "tick")
        self.assertTrue(browser.get("continue-on-error"))
        self.assertEqual(self.lane_result("browser"), (1, "failed=jobright"))  # the lane is named
        self.assertEqual(
            tick["env"]["TICK_SKIP_STAGES"], "${{ steps.browser.outcome == 'failure' && 'resolve-jobright' || '' }}"
        )
        self.assertNotIn("steps.browser", str(tick.get("if", "")))  # the tick still runs after a failed install
        for later in self.steps[self.steps.index(browser) + 1 :]:
            self.assertNotIn("steps.browser", str(later.get("if", "")), later.get("name"))
        manual = next(s for s in self.doc["jobs"]["jobright"]["steps"] if s.get("id") == "jobright_browser")
        self.assertNotIn("continue-on-error", manual)
        self.assertEqual(self.lane_result("tick"), (1, "failed=tick"))

    def test_credentials_the_gate_and_unit_tests_still_block_the_finish_job(self):
        browser_at = next(i for i, s in enumerate(self.steps) if s.get("id") == "browser")
        for step in self.steps[:browser_at]:
            if step.get("continue-on-error") and not is_parity(step):
                self.assertEqual(step.get("uses"), "./.github/actions/tailscale")
        loader = next(s for s in self.steps if s.get("id") == "bws_finish")
        self.assertFalse({"if", "continue-on-error"} & set(loader))
        self.assertNotIn("continue-on-error", self.doc["jobs"]["finish"])
        self.assertIn("needs.prep.result == 'success'", self.doc["jobs"]["finish"]["if"])

    def test_the_report_job_pushes_once_and_names_the_stages(self):
        import os
        import subprocess

        report = next(s for s in self.doc["jobs"]["report"]["steps"] if "finish job report" in s.get("name", ""))

        def run(stages):
            script = re.sub(
                r"\$\{\{\s*([^}]+?)\s*\}\}",
                lambda m: "failure" if m.group(1) == "needs.finish.result" else "success",
                report["run"],
            )
            script = script.replace("python3 -m lifeos.platform.alerts push", "echo PUSH")
            done = subprocess.run(
                ["bash", "-e", "-c", script],
                env={**os.environ, "STAGES": stages, "CODES": ""},
                capture_output=True,
                text=True,
                check=False,
            )
            return done.returncode, done.stdout

        code, out = run("network_surface alerts jobright")
        self.assertEqual((code, out.count("PUSH")), (1, 1))
        self.assertIn("stages: network_surface alerts jobright", out)


class ConcurrencyHasNoSwitch(unittest.TestCase):
    """approval 5: TICK_CONCURRENCY are gone from the workflow and the code"""

    def test_no_workflow_or_code_file_reads_the_switch(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        for path in list((root / ".github/workflows").glob("*.yml")) + [
            root / "lifeos/jobs/tick.py",
            root / "lifeos/platform/credential_list.py",
        ]:
            self.assertNotIn("TICK_CONCURRENCY", wftext.read(path), path.name)
            self.assertNotIn("BENCH_TICK_CONCURRENCY", wftext.read(path), path.name)


LEAN = ("v3-pages", "domains", "hourly")


class DopplerParityTests(unittest.TestCase):
    """every job that loads Bitwarden secrets also compares the same names with Doppler, side by side."""

    @classmethod
    def setUpClass(cls):
        try:
            import yaml as _yaml
        except ImportError:
            raise unittest.SkipTest("pyyaml unavailable") from None
        root = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows"
        cls.docs = {stem: _yaml.safe_load(text) for stem, text in wftext.workflow_texts(expand=False).items()}
        cls.texts = wftext.workflow_texts()

    def loaders_with_compare(self):
        """> [(workflow, job, loader step, the compare step that follows it or None)] for every runtime-token Bitwarden
        load that names secrets.
        """
        out = []
        for name, doc in self.docs.items():
            for job, body in (doc.get("jobs") or {}).items():
                steps = body.get("steps") or []
                for i, step in enumerate(steps):
                    with_ = step.get("with") or {}
                    if (
                        step.get("uses") == "./.github/actions/bws-load"
                        and step.get("id")
                        and "LIFEOS_BWS_RUNTIME_TOKEN" in str(with_.get("access_token"))
                    ):
                        later = steps[i + 1 :]
                        stop = next(
                            (k for k, s in enumerate(later) if s.get("uses") == "./.github/actions/bws-load"),
                            len(later),
                        )
                        old = next((s for s in later[:stop] if is_parity(s)), None)
                        out.append((name, job, step, step if with_.get("doppler_compare") else old))
        return out

    def test_every_secret_loading_job_compares_the_same_names_side_by_side(self):
        found = self.loaders_with_compare()
        self.assertGreaterEqual(len(found), 55)
        for name, job, loader, compare in found:
            self.assertIsNotNone(compare, (name, job, loader["id"]))
            if compare is loader:
                self.assertEqual(loader["with"]["doppler_compare"], "${{ secrets.DOPPLER_TOKEN }}", (name, job))
                continue
            self.assertNotIn(name, LEAN, (name, job))  # the three lean workflows use the folded form only
            names = [line.split(">")[1].strip() for line in str(loader["with"]["secrets"]).splitlines() if ">" in line]
            self.assertEqual(str(compare["with"]["names"]).split(","), names, (name, job))
            self.assertEqual(compare["with"]["token"], "${{ secrets.DOPPLER_TOKEN }}", (name, job))
            self.assertEqual(compare["with"]["bitwarden"], "{}", (name, job))
            self.assertTrue(compare.get("continue-on-error") is True, (name, job))
            self.assertEqual(compare.get("if"), f"${{{{ steps.{loader['id']}.outcome == 'success' }}}}", (name, job))

    def test_production_keeps_bitwarden_nothing_consumes_the_compare_and_the_token_has_one_use(self):
        for name, doc in self.docs.items():
            text = self.texts[name]
            if name in LEAN:
                self.assertNotIn("actions/doppler-load", text, name)  # no separate compare step in the lean workflows
            ids = [
                s["id"]
                for body in (doc.get("jobs") or {}).values()
                for s in (body.get("steps") or [])
                if is_parity(s) and s.get("id")
            ]
            for ident in ids:
                self.assertEqual(re.findall(rf"steps\.{ident}\.(\w+)", text), [], (name, ident))
            token_uses = re.findall(r".*secrets\.DOPPLER_TOKEN.*", text)
            compares = sum(
                1
                for body in (doc.get("jobs") or {}).values()
                for s in (body.get("steps") or [])
                if is_parity(s) or (s.get("with") or {}).get("doppler_compare")
            )
            self.assertEqual(len(token_uses), compares, name)

    def test_the_action_only_reports_a_code_and_counts(self):
        import yaml as _yaml

        action = _yaml.safe_load(
            pathlib.Path(__file__).resolve().parents[2].joinpath(".github/actions/doppler-load/action.yml").read_text()
        )
        self.assertEqual(set(action["outputs"]), {"code", "matched", "total", "doppler_ms"})
