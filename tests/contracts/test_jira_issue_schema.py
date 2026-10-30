"""LIFE-79 (Jim, Oct 29 2026): the LIFE Jira issue-writing contract has one machine-readable schema, linked from the canonical policy.
The schema is a pure JSON document; these checks need no network, Jira or Notion."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "jira" / "issue-schema.v1.json"
POLICY = (ROOT / "docs" / "life-os-development-policy.md").read_text()
SCHEMA = json.loads(SCHEMA_PATH.read_text())
DEFS = SCHEMA["$defs"]

try:
    import jsonschema
except ImportError:                                    # not a runtime dependency; the stdlib checks below still run
    jsonschema = None


def matches(name, value):
    return re.fullmatch(DEFS[name]["pattern"], value) is not None


class Contract(unittest.TestCase):
    def test_the_policy_owns_the_contract_and_links_the_schema(self):
        self.assertIn("### LIFE Jira issue-writing contract", POLICY)
        self.assertIn("(jira/issue-schema.v1.json)", POLICY)
        self.assertNotIn("As the Main User. Given:", POLICY)        # the old combined story/criteria format is reconciled away

    def test_the_policy_examples_match_the_schema_formats(self):
        branch = re.search(r"for example `([^`]+)`", POLICY).group(1)
        self.assertTrue(matches("branch", branch), branch)
        self.assertTrue(matches("pr_title", "[LIFE-79][platform-core-os][platform-core-os-1-0-0] Adopt the Jira issue-writing contract"))

    def test_malformed_names_are_refused(self):
        for bad in ("claude/friendly-johnson-kcjfxw", "life/life-79/platform-core-os-1-0-0/platform-core-os/x", "life/LIFE-79/Platform/platform-core-os/x"):
            self.assertFalse(matches("branch", bad), bad)
        for bad in ("LIFE-79: Adopt the contract", "[LIFE-79][Platform][platform-core-os-1-0-0] x", "[LIFE-79][platform-core-os][platform-core-os-1-0-0]"):
            self.assertFalse(matches("pr_title", bad), bad)

    def test_the_story_statement_and_acceptance_criteria_stay_separate(self):
        story = SCHEMA["properties"]["statement"]["oneOf"][0]["properties"]["text"]["pattern"]
        self.assertTrue(re.fullmatch(story, "As a job seeker, I want my interview pages built for me, so that I prepare faster."))
        self.assertFalse(re.fullmatch(story, "As the Main User. Given: x. When: y. Then: z."))
        self.assertEqual(SCHEMA["properties"]["acceptance_criteria"]["items"]["required"], ["given", "when", "then"])

    def test_secret_values_are_excluded_and_life_workflow_is_documented(self):
        refs = SCHEMA["properties"]["execution"]["properties"]["secret_refs"]["items"]["properties"]
        self.assertEqual(set(refs), {"kind", "identifier"})
        self.assertIn("LIFE workflow statuses are Agent Go, Agent UAT, and Human UAT", SCHEMA["description"])
        self.assertIs(SCHEMA["properties"]["fast_proof"]["properties"]["schedule_independent"]["const"], True)
        self.assertIn("LIFE uses the `Agent UAT` and `Human UAT` workflow statuses", POLICY)
        self.assertIn("merge only after required checks and review pass", POLICY)
        self.assertIn("Assign an independent reviewer and merge operator before implementation", POLICY)
        self.assertIn("The writer\'s GitHub identity cannot approve its own PR", POLICY)
        self.assertIn("Name an independent reviewer and authorized merge operator", SCHEMA["description"])

    def test_every_example_carries_the_required_fields(self):
        for example in SCHEMA["examples"]:
            self.assertEqual(sorted(set(SCHEMA["required"]) - set(example)), [])

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_the_schema_is_valid_and_its_examples_validate(self):
        jsonschema.Draft202012Validator.check_schema(SCHEMA)
        for example in SCHEMA["examples"]:
            jsonschema.Draft202012Validator(SCHEMA).validate(example)
        bad = dict(SCHEMA["examples"][0], ui={"scope": "new_or_changed_ui"})          # new UI without Figma frames or a reason
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(SCHEMA).validate(bad)
        bad = dict(SCHEMA["examples"][0], human_uat={"required": False})              # not required, but no reason recorded
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(SCHEMA).validate(bad)


if __name__ == "__main__":
    unittest.main()
