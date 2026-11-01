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

    def test_los_first_discovery_and_description_first_contract_is_in_the_policy(self):
        """LIFE-79 (Jim, Nov 1 2026): new product outcomes start in an approved LOS Idea; answers persist before the next tranche; Description owns requirements."""
        rule = POLICY.split("### LOS-first discovery and description-first requirements", 1)[1].split("## 2. Locked architecture", 1)[0]
        for required in ("`DISCOVERY_NOT_READY", "`DISCOVERY_CAPTURE_BLOCKED`", "A parked LOS Idea is not delivery authorization", "**Exception without bypass:**",
                         "cannot launder a new product outcome", "**NOT TECHNICALLY ENFORCED**", "Discovery log — Tranche N", "never inferred from an assistant recommendation",
                         "never comment-only", "A stale comment never outranks a current Description", "`Given / When / Then`", "**UNVERIFIED**",
                         "is not an interactive Jira checklist", "is never Done", "`QUEUE_NOT_READY`", "Agent Go release, Priority order, claims, one-writer scope and protected-operation gates are unchanged"):
            self.assertIn(required, rule)

    def _origin_errors(self, origin):
        issue = dict(SCHEMA["examples"][0], product_origin=origin)
        return list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(issue))

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_a_new_product_outcome_needs_an_approved_los_origin_but_existing_epic_work_does_not(self):
        issue = {k: v for k, v in SCHEMA["examples"][0].items() if k != "product_origin"}
        self.assertTrue(list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(issue)))                       # missing product origin
        self.assertEqual(self._origin_errors({"kind": "los_idea", "los_key": "LOS-70", "approved_for_delivery": True, "approval_source": "Jim, 2026-11-01"}), [])
        self.assertTrue(self._origin_errors({"kind": "los_idea", "los_key": "LOS-70", "approved_for_delivery": False, "approval_source": "parked"}))   # parked is not approved
        self.assertTrue(self._origin_errors({"kind": "los_idea", "los_key": "LIFE-70", "approved_for_delivery": True, "approval_source": "x"}))          # not an LOS key
        self.assertEqual(self._origin_errors({"kind": "approved_epic_child", "epic_key": "LIFE-100", "epic_approval_source": "Jim approved the finite Epic"}), [])
        self.assertEqual(self._origin_errors({"kind": "bug", "parent_ref": "LIFE-101", "approval_source": "approved Epic LIFE-100"}), [])
        self.assertTrue(self._origin_errors({"kind": "approved_epic_child", "epic_key": "LIFE-100"}))             # a claimed exception must cite its approval
        self.assertTrue(self._origin_errors({"kind": "new_product"}))                                              # no unlisted way around LOS

    @staticmethod
    def _unpersisted(answered, capture):
        """Test fixture check only: answered question numbers that are not in the saved entries or in the verified read-back."""
        saved = {e["question_number"] for e in capture["entries"]} & set(capture["readback"]["saved_questions"])
        return sorted(set(answered) - saved)

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_every_answered_discovery_question_must_be_saved_and_read_back(self):
        validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/discovery_capture", "$defs": DEFS})
        entry = lambda n, state="APPROVED DECISION": {"question_number": n, "owner_answer": "Jim's answer", "decision_state": state, "requirement_impact": "impact", "open_gap": "none"}
        capture = {"los_key": "LOS-70", "tranche": 4, "owner_reply_date": "2026-11-01", "entries": [entry(13), entry(14, "UNRESOLVED")], "readback": {"verified": True, "saved_questions": [13, 14], "revision_after": "2026-11-01T12:00:00.000-0600"}}
        self.assertEqual(list(validator.iter_errors(capture)), [])
        self.assertEqual(self._unpersisted([13, 14], capture), [])
        partial = dict(capture, entries=[entry(13)], readback={"verified": True, "saved_questions": [13], "revision_after": "r"})
        self.assertEqual(self._unpersisted([13, 14], partial), [14])                                              # DISCOVERY_CAPTURE_BLOCKED: Q14 not saved
        self.assertTrue(list(validator.iter_errors(dict(capture, readback={"verified": False, "saved_questions": [13, 14], "revision_after": "r"}))))
        self.assertTrue(list(validator.iter_errors(dict(capture, readback={"verified": True, "saved_questions": [13, 14]}))))     # a read-back names the revision it read   # no unverified read-back
        self.assertTrue(list(validator.iter_errors(dict(capture, entries=[]))))
        self.assertTrue(list(validator.iter_errors(dict(capture, entries=[entry(13, "ASSUMED")]))))              # approval state is one of the four

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_acceptance_needs_given_when_then_and_a_check_needs_cited_evidence(self):
        validator = jsonschema.Draft202012Validator(SCHEMA)
        issue = lambda criteria: dict(SCHEMA["examples"][0], acceptance_criteria=criteria)
        item = {"given": "g", "when": "w", "then": "t"}
        self.assertEqual(list(validator.iter_errors(issue([dict(item, state="unchecked")]))), [])
        self.assertEqual(list(validator.iter_errors(issue([dict(item, state="checked", evidence="PR #1 same-head CI run 123")]))), [])
        self.assertTrue(list(validator.iter_errors(issue([]))))                                                    # missing acceptance criteria
        self.assertTrue(list(validator.iter_errors(issue([{"given": "g", "when": "w"}]))))                         # not Given/When/Then
        self.assertTrue(list(validator.iter_errors(issue([dict(item, state="checked")]))))                         # unsafe completion claim: ticked without proof
        self.assertTrue(list(validator.iter_errors(issue([dict(item, state="checked", evidence="")]))))
        self.assertIn("a checklist tick is never Done", SCHEMA["description"])
        self.assertIn("not a native Jira validator", SCHEMA["description"])

    def test_context_efficiency_contract_is_in_the_policy(self):
        """LIFE-79 (Jim, Nov 1 2026): minimal Jira-first retrieval, clear ownership, persisted interviews, budgets that never beat correctness, fresh-agent handoff."""
        rule = POLICY.split("### Context efficiency", 1)[1].split("## 2. Locked architecture", 1)[0]
        for required in ("Read the owning Jira issue first", "only the specific Notion section, GitHub file or related Jira issue", "Never pull whole conversation histories",
                         "while its source revision is unchanged", "stop researching once sufficient authoritative evidence exists",
                         "safety, authority, a contradiction, missing proof or verification",                                  # the exceptions to reuse
                         "The LOS Description holds", "The LIFE Description holds", "Notion holds detailed product specifications", "GitHub holds code",
                         "Comments hold active development findings and receipts, not requirements", "never move, delete, summarize away or overwrite",
                         "do not re-fetch Notion during routine questioning", "narrow edit", "revision changed since it was read, re-read and merge",
                         "Independently read the change back before the next tranche", "`DISCOVERY_CAPTURE_BLOCKED` and stops the next tranche",
                         "never overrides correctness", "from one Jira key and its authoritative links, without pasted conversation history",
                         "rather than speculative implementation", "no task store, scheduler, dispatcher, index, context-sync service or agent relay",
                         "cannot observe or limit an external agent's tool calls",
                         "Agent Go release, Priority order, claims, one-writer scope and protected-operation gates are unchanged"):
            self.assertIn(required, rule)
        self.assertNotIn("token limit", rule.lower())                       # no artificial token budget

    def test_the_schema_lets_a_fresh_agent_recover_scope_from_one_key_without_chat_history(self):
        for needed in ("key", "product_origin", "acceptance_criteria", "execution", "authorization", "owner", "component", "fix_version"):
            self.assertIn(needed, SCHEMA["required"])
        self.assertFalse(SCHEMA["additionalProperties"])                    # no place to paste a conversation
        for chat in ("chat_history", "conversation", "transcript"):
            self.assertNotIn(chat, SCHEMA["properties"])
        self.assertEqual(SCHEMA["properties"]["notion_requirement"]["$ref"], "#/$defs/https_url")   # detail is linked, not copied into Jira

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_a_pasted_conversation_is_not_accepted_as_issue_content(self):
        bad = dict(SCHEMA["examples"][0], chat_history="Jim said ...")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(SCHEMA).validate(bad)

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
