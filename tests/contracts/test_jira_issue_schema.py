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
        self.assertIn("LIFE workflow statuses include Agent UAT and Human UAT", SCHEMA["description"])
        self.assertIs(SCHEMA["properties"]["fast_proof"]["properties"]["schedule_independent"]["const"], True)
        self.assertIn("LIFE uses the `Agent UAT` and `Human UAT` workflow statuses", POLICY)
        self.assertIn("no compulsory independent Tech Lead review", POLICY)
        self.assertNotIn("Assign an independent reviewer and merge operator before implementation", POLICY)
        self.assertIn("Do not invent approvals", POLICY)
        self.assertIn("Independent technical review is optional for routine PRs", SCHEMA["description"])

    def test_agent_go_is_the_human_released_priority_ordered_queue(self):
        """LIFE-126 (Jim, Nov 1 2026): Agent Go is the explicit human-released agent queue, ordered by Priority then Rank."""
        selector = POLICY.split("### Deterministic agent work selection", 1)[1].split("### Jira-first development lifecycle", 1)[0]
        authorization = next(line for line in POLICY.splitlines() if line.startswith("- **Authorization, scope, assignment, and routing.**"))
        query = 'project = LIFE AND status = "Agent Go" AND issuetype != Epic ORDER BY priority DESC, Rank ASC'
        self.assertIn(query, selector)                                   # the one selector query, Priority first and Rank as tie-breaker
        self.assertIn("Rank breaks ties", selector)
        self.assertIn("never promotes work into `Agent Go`", selector)    # a newly started agent cannot authorize or promote its own ticket
        self.assertIn("never promotes its own, a self-created or any unapproved issue into Agent Go", authorization)
        self.assertIn("not a technical guarantee", authorization)         # the org-admins restriction is not an agent-identity boundary
        self.assertIn("does not by itself start a dormant agent", selector)
        for skipped in ("incomplete incoming `Blocks` predecessor", "`acceptance-pending` hold", "conflicting or already-claimed writer", "execution environment the agent does not support"):
            self.assertIn(skipped, selector)                              # blocked or conflicting work is skipped without mutation
        self.assertIn("exclusive-writer mutation surface is free", selector)
        self.assertIn('transition Agent Go to In Progress (ID 6, "Start authorized work")', selector)
        self.assertIn("transition ID 5 (To Do to Agent Go", authorization)
        self.assertIn("empty `Agent Go` queue", selector)
        for obsolete in ("unavailable on Jira Free", "queries its own lane **and** `pull-ready`", "even when labeled `pull-ready`", "readiness marker"):
            self.assertNotIn(obsolete, POLICY)
        self.assertNotIn("unavailable Agent Go transition", SCHEMA["description"])
        self.assertNotIn("unavailable on Jira Free", json.dumps(SCHEMA))
        for retained in ("Agent UAT", "Human UAT", "protected-action", "single-writer"):
            self.assertIn(retained, POLICY)

    def test_requirement_changes_are_reconciled_in_the_live_records(self):
        """LIFE-79 (Jim, Nov 1 2026): an approved requirement change corrects the directly affected live Jira records, bounded and event-driven."""
        rule = POLICY.split("### Requirement-change reconciliation", 1)[1].split("## 2. Locked architecture", 1)[0]
        for required in ("not a scheduler, audit, backlog, workflow, status, automation or approval gate", "bounded text search", "in place",
                         "must not remain an active prerequisite", "`HISTORICAL — SUPERSEDED`", "`RECONCILED`", "`PARTIAL / BLOCKED`",
                         "never moves an issue into `Agent Go`", "one-writer scope and protected-operation gates are unchanged"):
            self.assertIn(required, rule)
        self.assertNotIn("Buzz or T3", POLICY)                            # T3 is retired as an authorization channel

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
