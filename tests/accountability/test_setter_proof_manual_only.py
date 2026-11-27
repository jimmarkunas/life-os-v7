"""The same-value setter proof is reachable only from a manual dispatch, with the Revision and an explicit yes."""

import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / ".github" / "workflows-src"
STEP = (SRC / "domains" / "020d-jira.yml").read_text()
PROOF = STEP[STEP.index("- id: jsetterproof") : STEP.index("- id: jroll")]


class ManualOnly(unittest.TestCase):
    def test_the_step_runs_only_on_a_manual_dispatch_of_the_setter_proof_choice(self):
        condition = next(line for line in PROOF.splitlines() if line.strip().startswith("if:"))
        self.assertIn("github.event_name == 'workflow_dispatch'", condition)
        self.assertIn("inputs.jira == 'setter-proof'", condition)

    def test_nothing_else_in_the_workflow_parts_runs_it(self):
        users = [p.name for p in SRC.rglob("*.yml") if "setter_proof" in p.read_text()]
        self.assertEqual(users, ["020d-jira.yml"])

    def test_it_needs_a_numeric_revision_and_sends_only_after_the_word_yes(self):
        self.assertIn("grep -Eq '^[0-9]{1,6}$'", PROOF)
        self.assertIn('if [ "$PROOF_LIVE" = "yes" ]; then python -m lifeos.accountability.setter_proof --live', PROOF)
        self.assertIn("else python -m lifeos.accountability.setter_proof --revision", PROOF)

    def test_it_adds_no_secret_beyond_the_two_the_settings_step_already_uses(self):
        names = {line.split(":")[0].strip() for line in PROOF.splitlines() if "${{" in line and "steps." in line}
        self.assertEqual(names, {"V7_RUNTIME_CONFIG_SOURCE_ID", "NOTION_JIRA_TOKEN"})


if __name__ == "__main__":
    unittest.main()
