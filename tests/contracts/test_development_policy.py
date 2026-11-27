"""The old 11-part split, its manifest and the dated amendments are history in docs/archive/ (no authority)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "docs" / "life-os-development-policy.md"
POLICY = POLICY_PATH.read_text()


class DryRunGate(unittest.TestCase):
    """the scoping gate is in the policy, the one-page boot guide and the ticket template."""

    def test_the_policy_section_has_the_rules(self):
        self.assertIn("## 12. Dry run gate (scoping, before Agent Go;", POLICY)
        for kept in (
            "**Dry run**",
            "**Decisions pre-made**",
            "`dry-run-green`",
            "`dry-run-miss`",
            "`release LIFE-nnn`",
            "`dry run 856 877 981`",
            "read-only: no write, merge, deploy or delete",
            "One session, one outcome",
            "grandfathered",
            "is already its own `UAT for LIFE-nnn` ticket under LIFE-615",
        ):
            self.assertIn(kept, POLICY, kept)
        self.assertNotIn("Found mid-run: the same, then keep building.", POLICY)

    def test_the_orchestrator_exception_and_the_agent_layer_rule_are_written_down(self):
        """the one exception to 'Jim starts every run', its kill switch, and the agent-layer rules agents follow."""
        for name in ("docs/life-os-development-policy.md", "CLAUDE.md", "AGENTS.md"):
            self.assertIn("LIFE-987", (ROOT / name).read_text(), name)
        self.assertIn("`ORCHESTRATOR_CAN_START`", POLICY)
        self.assertIn("Jim starts every agent run", POLICY)
        agents = (ROOT / "AGENTS.md").read_text()
        for kept in (
            "claim with `start_work`",
            "post status, blocker and completion to the Orca room",
            "it never approves anything new",
        ):
            self.assertIn(kept, agents, kept)
        self.assertNotIn(
            "anything that starts or wakes an agent after the session ends", (ROOT / "CLAUDE.md").read_text()
        )

    def test_agents_md_requires_the_label_at_claim_time_and_the_template_has_every_section(self):
        self.assertIn("only with label `dry-run-green` (policy §12", (ROOT / "AGENTS.md").read_text())
        template = (ROOT / "docs" / "templates" / "ticket.md").read_text()
        for heading in (
            "Why",
            "Exact targets",
            "Dry run",
            "Decisions pre-made",
            "Jim must do first",
            "Done when",
            "Not in scope",
            "Rollback",
            "Executing agent",
            "Verifier",
        ):
            self.assertIn(f"## {heading}\n", template, heading)


class OneFilePolicy(unittest.TestCase):
    def test_it_is_one_file_under_the_size_ceiling(self):
        self.assertLessEqual(len(POLICY.encode()), 18 * 1024)
        self.assertFalse(
            (ROOT / "docs" / "development-policy").exists(), "the policy is one file; history lives in docs/archive/"
        )

    def test_the_kept_guardrails_are_stated(self):
        for kept in (
            "workflow `permissions`",
            "credential",
            "one bootstrap token per secrets manager",
            "Deleting a GitHub value or production data",
            "Spend or a paid model",
            "metered Notion query",
            "sign-in",
            "Notion page or database IDs",
            "docs/PRIVACY.md",
            "ARCHITECTURAL DRIFT DETECTED — NO MUTATION",
            "exactly one writer",
            "one writer per branch",
            "never invent an approval",
        ):
            self.assertIn(kept, POLICY, kept)

    def test_the_release_rule_and_the_start_rule_are_stated(self):
        self.assertIn("## 4. Jim must do first (the release rule)", POLICY)
        self.assertIn("does not move to Agent Go", POLICY)
        self.assertIn("Notion wireframe", POLICY)
        self.assertIn("Jim starts every agent run", POLICY)
        self.assertIn("agents use `ssh opc@lifeos-edge-01`; `sudo -u ubuntu` reads are read-only", POLICY)
        self.assertIn("inside one, it may watch its own CI and runs", POLICY)
        self.assertIn(
            'project = LIFE AND status = "Agent Go" AND issuetype != Epic ORDER BY priority DESC, Rank ASC', POLICY
        )

    def test_los_is_retired_and_epics_carry_the_lifecycle(self):
        self.assertIn("The `LOS` project is retired", POLICY)
        self.assertIn("`Scoping → Ready → In Progress → Done`", POLICY)
        for label in ("`ready`", "`needs-prep`", "`needs-jim`"):
            self.assertIn(label, POLICY)
        self.assertNotIn("LOS idea", POLICY)

    def test_every_epic_proves_feasibility_before_any_build_ticket_runs(self):
        self.assertIn("**Feasibility first, new Epics only:**", POLICY)
        self.assertIn("`research`", POLICY)
        self.assertIn("GO / NO-GO comment", POLICY)
        self.assertIn("A change inside an existing Epic goes straight to a build ticket.", POLICY)
        self.assertIn("**Close a `research` ticket:** no PR, so no closeout script.", POLICY)
        self.assertIn("only then MCP rows or SQL queries, which count as metered", POLICY)
        self.assertIn("Build tickets wait for the verdict.", POLICY)

    def test_runs_do_not_stop_midway_for_pre_approvable_steps(self):
        self.assertIn("**Standing approvals (Jim, Nov 22, 2026):**", POLICY)
        self.assertIn("a job reusing a secret or Bitwarden name its workflow already loads", POLICY)
        self.assertIn("**Check before claiming:**", POLICY)
        self.assertIn("**One ticket at a time** per session", POLICY)
        self.assertIn("a natural run never gates a build ticket's Done; its UAT ticket checks it", POLICY)
        self.assertIn("only with label `needs-uat` or when Jim is the real user of the outcome", POLICY)

    def test_done_needs_outcome_proof_and_one_natural_run_rule(self):
        self.assertIn("A merged PR on green is not Done by itself.", POLICY)
        self.assertIn("**Natural runs, one rule:**", POLICY)
        self.assertNotIn("Merged on green with local proof is Done", POLICY)
        self.assertIn("is a Production Contract change", POLICY)
        self.assertIn("**Reuse, never redo:**", POLICY)

    def test_the_modernization_exceptions_jim_kept_are_in_force(self):
        self.assertIn("V7 modernization consolidation exception", POLICY)
        self.assertIn("Freezes and STOP gates lifted", POLICY)

    def test_the_removed_ceremony_stays_removed(self):
        for gone in (
            "Deadline Mode",
            "LIFEOS_DEADLINE_EPOCH",
            "five minutes",
            "300 production-line",
            "three production files",
            "[claim]",
            "authorization receipt",
            "manifest.json",
            "Tech Lead",
            "Codex",
        ):
            self.assertNotIn(gone, POLICY, gone)

    def test_the_boot_files_route_to_it_and_carry_no_deadline_or_buzz_ceremony(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            text = (ROOT / name).read_text()
            self.assertIn("docs/life-os-development-policy.md", text, name)
            for gone in ("Deadline Mode", "START_EPOCH", "check Buzz"):
                self.assertNotIn(gone, text, name)


class LosIsRetired(unittest.TestCase):
    """LOS is retired, so nothing live queries it."""

    PATTERN = re.compile(r"project\s*=\s*LOS\b|project\s+in\s*\([^)\n]*\bLOS\b", re.I)

    def test_no_code_or_workflow_queries_the_los_project(self):
        hits = []
        for base in ("lifeos", ".github"):
            for path in sorted((ROOT / base).rglob("*")):
                if path.is_file() and path.suffix in {
                    ".py",
                    ".yml",
                    ".yaml",
                    ".sh",
                    ".json",
                    ".mjs",
                    ".js",
                    ".md",
                    ".txt",
                }:
                    for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                        if self.PATTERN.search(line):
                            hits.append(f"{path.relative_to(ROOT)}:{n}")
        self.assertEqual(hits, [], "an active query names the retired LOS project")

    def test_the_check_catches_the_query_shapes_it_is_for(self):
        for bad in ("project = LOS", "project=LOS AND status = Done", "jql: project in (LIFE, LOS)", "PROJECT  =  los"):
            self.assertTrue(self.PATTERN.search(bad), bad)
        for fine in ("project = LIFE", "historical LOS-86", "project in (JFM, LIFE)", "project = LOSS"):
            self.assertFalse(self.PATTERN.search(fine), fine)


class GlobalIndex(unittest.TestCase):
    """(LOS-74 Q11): the root INDEX.md routes every global source with its path, authority, purpose, owner and retrieval,
    and copies no policy text.
    """

    INDEX = ROOT / "INDEX.md"

    def rows(self, heading):
        section = self.INDEX.read_text().split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
        return [
            [c.strip() for c in line.strip("|").split(" | ")]
            for line in section.splitlines()
            if line.startswith("| [`")
        ]

    @staticmethod
    def path(cell):
        found = re.fullmatch(r"\[`([^`]+)`\]\(([^)]+)\)", cell)
        assert found and found.group(1) == found.group(2), cell
        return found.group(1)

    def test_every_row_is_complete_and_resolves(self):
        rows = self.rows("Global sources")
        self.assertGreater(len(rows), 10)
        for row in rows:
            self.assertEqual(len(row), 5, row)
            self.assertTrue(all(row), row)
            self.assertTrue((ROOT / self.path(row[0])).is_file(), row[0])
        listed = [self.path(r[0]) for r in rows]
        self.assertEqual(len(listed), len(set(listed)))

    def test_the_policy_and_bootstrap_are_routed(self):
        listed = {self.path(r[0]) for r in self.rows("Global sources")}
        self.assertLessEqual(
            {
                "INDEX.md",
                "AGENTS.md",
                "CLAUDE.md",
                "docs/life-os-development-policy.md",
                "docs/agent-reference.md",
                "docs/jira/issue-schema.v1.json",
            },
            listed,
        )

    def test_it_copies_no_policy_text_and_stays_small(self):
        text = self.INDEX.read_text()
        self.assertLessEqual(len(text.encode()), 15 * 1024)
        for line in POLICY.splitlines():
            if len(line) > 80 and not line.startswith(("#", "|")):
                self.assertNotIn(line, text)
        self.assertIn("[INDEX.md](INDEX.md)", (ROOT / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()


class AgentNames(unittest.TestCase):
    """the app decides, never the machine."""

    def run_with(self, **env):
        import subprocess, os

        base = {"PATH": os.environ["PATH"]}
        base.update(env)
        out = subprocess.run(
            ["sh", str(ROOT / "tools/agent_name.sh")], env=base, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()

    def test_vscode_is_oc(self):
        self.assertEqual(self.run_with(VSCODE_PID="1"), "OC")
        self.assertEqual(self.run_with(__CFBundleIdentifier="com.microsoft.VSCode"), "OC")
        self.assertEqual(self.run_with(CLAUDE_CODE_ENTRYPOINT="claude-vscode"), "OC")

    def test_mac_app_is_mc(self):
        self.assertEqual(self.run_with(__CFBundleIdentifier="com.anthropic.claudefordesktop"), "MC")
        self.assertEqual(self.run_with(CLAUDE_CODE_ENTRYPOINT="claude-desktop"), "MC")

    def test_no_signal_is_unknown(self):
        self.assertEqual(self.run_with(), "UNKNOWN")

    def test_boot_file_and_policy_name_agents_by_app(self):
        text = (ROOT / "CLAUDE.md").read_text()
        for must in (
            "**OC** is Claude Code inside VS Code",
            "**MC** is Claude Code inside the Claude Mac app",
            "tools/agent_name.sh",
            "Claimed by OC",
        ):
            self.assertIn(must, text)
        self.assertNotIn("Mac and Oracle Claude Code", POLICY)
        self.assertNotIn("Mac Claude Code is the exclusive writer", POLICY)


class CloseWhenShipped(unittest.TestCase):
    """every wait becomes a `UAT for LIFE-nnn` Task under the Bug Epic."""

    def test_the_policy_states_the_rule(self):
        for phrase in (
            "a build ticket is Done once it is merged on same-head green CI, live, and first-proved",
            "UAT for LIFE-nnn",
            "LIFE-615",
            "label `uat`",
            "not before <time> CT",
            "Not before (CT)",
            "A merged PR on green is not Done by itself.",
            "Agent Go",
            "a successor starts once its predecessor is Done",
        ):
            self.assertIn(phrase, POLICY, phrase)
        self.assertNotIn(
            "gates Done only when the ticket's Done when names it", POLICY
        )  # the old 24-hour natural-run gate is gone

    def test_the_boot_file_states_the_rule_and_no_longer_asks_for_every_done_when_line_before_done(self):
        text = (ROOT / "CLAUDE.md").read_text()
        for phrase in (
            "merged, live and first-proved",
            "UAT for LIFE-nnn",
            "LIFE-615",
            "`uat`",
            "Not before (CT)",
            "If it fails: file a Bug under LIFE-615",
            "a successor starts once its predecessor is Done",
        ):
            self.assertIn(phrase, text, phrase)
        self.assertNotIn(
            "prove every Done when line on the delivered outcome (Agent UAT), then move the ticket to Done", text
        )
        self.assertNotIn("gets a watch note, not your session", text)

    def test_the_fixture_catches_a_policy_without_the_rule(self):  # negative control: the check can fail
        self.assertNotIn(
            "UAT for LIFE-nnn",
            "- **Agent UAT:** after merge, the agent checks the delivered outcome. A merged PR on green is not Done by itself.",
        )


class NeverIdleOnAClock(unittest.TestCase):
    def test_boot_file_says_build_successors_during_waits(self):
        text = (ROOT / "CLAUDE.md").read_text()
        self.assertIn("**Never idle on a clock", text)
        self.assertIn("move the successor to Done only after the predecessor is Done", text)
