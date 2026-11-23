"""Jim, Nov 21 2026: the Development Policy is ONE file, short enough to read once, and it keeps the guardrails Jim kept.
The old 11-part split, its manifest and the dated amendments are history in docs/archive/ (no authority). No network, Jira or Notion."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "docs" / "life-os-development-policy.md"
POLICY = POLICY_PATH.read_text()


class OneFilePolicy(unittest.TestCase):
    def test_it_is_one_file_under_the_size_ceiling(self):
        self.assertLessEqual(len(POLICY.encode()), 15 * 1024)
        self.assertFalse((ROOT / "docs" / "development-policy").exists(), "the policy is one file; history lives in docs/archive/")

    def test_the_kept_guardrails_are_stated(self):
        for kept in ("workflow `permissions`", "credential", "one bootstrap token per secrets manager", "Deleting a GitHub value or production data", "Spend or a paid model",
                     "metered Notion query", "sign-in", "Notion page or database IDs", "docs/PRIVACY.md", "ARCHITECTURAL DRIFT DETECTED — NO MUTATION",
                     "exactly one writer", "one writer per branch", "never invent an approval"):
            self.assertIn(kept, POLICY, kept)

    def test_the_release_rule_and_the_start_rule_are_stated(self):
        self.assertIn("## 4. Jim must do first (the release rule)", POLICY)
        self.assertIn("does not move to Agent Go", POLICY)
        self.assertIn("Notion wireframe", POLICY)
        self.assertIn("Jim starts every agent run", POLICY)
        self.assertIn("agents use `ssh opc@lifeos-edge-01`; `sudo -u ubuntu` reads are read-only", POLICY)   # Jim, Nov 21 2026: Oracle access
        self.assertIn("inside one, it may watch its own CI and runs", POLICY)   # Jim, Nov 21 2026: in-session watching allowed
        self.assertIn('project = LIFE AND status = "Agent Go" AND issuetype != Epic ORDER BY priority DESC, Rank ASC', POLICY)

    def test_los_is_retired_and_epics_carry_the_lifecycle(self):
        self.assertIn("The `LOS` project is retired", POLICY)
        self.assertIn("`Scoping → Ready → In Progress → Done`", POLICY)
        for label in ("`ready`", "`needs-prep`", "`needs-jim`"):
            self.assertIn(label, POLICY)
        self.assertNotIn("LOS idea", POLICY)

    def test_every_epic_proves_feasibility_before_any_build_ticket_runs(self):
        self.assertIn("**Feasibility first, new Epics only:**", POLICY)   # Jim, Nov 22 2026: feasibility only for a new Epic he asks for
        self.assertIn("`research`", POLICY)
        self.assertIn("GO / NO-GO comment", POLICY)
        self.assertIn("A change inside an existing Epic goes straight to a build ticket.", POLICY)
        self.assertIn("**Close a `research` ticket:** no PR, so no closeout script.", POLICY)
        self.assertIn("only then MCP rows or SQL queries, which count as metered", POLICY)
        self.assertIn("Build tickets wait for the verdict.", POLICY)

    def test_runs_do_not_stop_midway_for_pre_approvable_steps(self):   # Jim, "approve all", Nov 22 2026, 6:03 PM CT
        self.assertIn("**Standing approvals (Jim, Nov 22, 2026):**", POLICY)
        self.assertIn("a job reusing a secret or Bitwarden name its workflow already loads", POLICY)
        self.assertIn("**Check before claiming:**", POLICY)
        self.assertIn("**One ticket at a time** per session", POLICY)
        self.assertIn("for at most 24 hours; a rare event (missed window, outage) is a watch note, not a blocker.", POLICY)
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
        for gone in ("Deadline Mode", "LIFEOS_DEADLINE_EPOCH", "five minutes", "300 production-line", "three production files", "[claim]",
                     "authorization receipt", "manifest.json", "Tech Lead", "Codex"):
            self.assertNotIn(gone, POLICY, gone)

    def test_the_boot_files_route_to_it_and_carry_no_deadline_or_buzz_ceremony(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            text = (ROOT / name).read_text()
            self.assertIn("docs/life-os-development-policy.md", text, name)
            for gone in ("Deadline Mode", "START_EPOCH", "check Buzz"):
                self.assertNotIn(gone, text, name)


class LosIsRetired(unittest.TestCase):
    """LIFE-849: LOS is retired, so nothing live queries it. Historical `LOS-nnn` keys in comments and docs stay as history."""

    PATTERN = re.compile(r"project\s*=\s*LOS\b|project\s+in\s*\([^)\n]*\bLOS\b", re.I)

    def test_no_code_or_workflow_queries_the_los_project(self):
        hits = []
        for base in ("lifeos", ".github"):
            for path in sorted((ROOT / base).rglob("*")):
                if path.is_file() and path.suffix in {".py", ".yml", ".yaml", ".sh", ".json", ".mjs", ".js", ".md", ".txt"}:
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
    """LIFE-222 (LOS-74 Q11): the root INDEX.md routes every global source with its path, authority, purpose, owner and retrieval, and copies no policy text."""
    INDEX = ROOT / "INDEX.md"

    def rows(self, heading):
        section = self.INDEX.read_text().split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
        return [[c.strip() for c in line.strip("|").split(" | ")] for line in section.splitlines() if line.startswith("| [`")]

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
        self.assertLessEqual({"INDEX.md", "AGENTS.md", "CLAUDE.md", "docs/life-os-development-policy.md", "docs/agent-reference.md", "docs/jira/issue-schema.v1.json"}, listed)

    def test_it_copies_no_policy_text_and_stays_small(self):
        text = self.INDEX.read_text()
        self.assertLessEqual(len(text.encode()), 15 * 1024)
        for line in POLICY.splitlines():
            if len(line) > 80 and not line.startswith(("#", "|")):
                self.assertNotIn(line, text)
        self.assertIn("[INDEX.md](INDEX.md)", (ROOT / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
