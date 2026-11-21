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
        for kept in ("workflow `permissions`", "credential", "Bitwarden only", "Deleting a GitHub value or production data", "Spend or a paid model",
                     "metered Notion query", "sign-in", "Notion page or database IDs", "docs/PRIVACY.md", "ARCHITECTURAL DRIFT DETECTED — NO MUTATION",
                     "exactly one writer", "one writer per branch", "never invent an approval"):
            self.assertIn(kept, POLICY, kept)

    def test_the_release_rule_and_the_start_rule_are_stated(self):
        self.assertIn("## 4. Jim must do first (the release rule)", POLICY)
        self.assertIn("does not move to Agent Go", POLICY)
        self.assertIn("Notion wireframe", POLICY)
        self.assertIn("Jim starts every agent run", POLICY)
        self.assertIn('project = LIFE AND status = "Agent Go" AND issuetype != Epic ORDER BY priority DESC, Rank ASC', POLICY)

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
