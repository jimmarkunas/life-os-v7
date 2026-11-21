"""LIFE-79 (Jim, Oct 29 2026): the LIFE Jira issue-writing contract has one machine-readable schema, linked from the canonical policy.
The schema is a pure JSON document; these checks need no network, Jira or Notion."""
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "jira" / "issue-schema.v1.json"
POLICY = (ROOT / "docs" / "life-os-development-policy.md").read_text()     # Jim, Nov 21 2026: the whole policy is one file
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
        self.assertIn("## 7. Writing tickets", POLICY)
        self.assertIn("(jira/issue-schema.v1.json)", POLICY)
        self.assertNotIn("As the Main User. Given:", POLICY)        # the old combined story/criteria format is reconciled away

    def test_the_policy_examples_match_the_schema_formats(self):
        self.assertIn("one branch `life/LIFE-nnn/<slug>`, one PR titled `[LIFE-nnn] <outcome>`", POLICY)
        self.assertTrue(matches("branch", "life/LIFE-79/delivery-flow-v3"))
        self.assertTrue(matches("pr_title", "[LIFE-79] Adopt the Jira issue-writing contract"))

    def test_malformed_names_are_refused(self):
        for bad in ("claude/friendly-johnson-kcjfxw", "life/life-79/x", "life/LIFE-79/Platform", "life/LIFE-79/a/b"):
            self.assertFalse(matches("branch", bad), bad)
        for bad in ("LIFE-79: Adopt the contract", "[LIFE-79]", "[life-79] x"):
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
        self.assertIn("Human UAT", POLICY)
        self.assertIn("No reviewer is required", POLICY)
        self.assertIn("never invent an approval", POLICY)
        self.assertIn("Reference secrets by identifier only", POLICY)
        self.assertIn("Independent technical review is optional for routine PRs", SCHEMA["description"])

    def test_agent_go_is_the_human_released_priority_ordered_queue(self):
        """Agent Go is the released queue, ordered by Priority then Rank; a status change wakes nobody (Jim, Nov 21 2026)."""
        delivery = POLICY.split("## 3. Delivery", 1)[1].split("## 4.", 1)[0]
        self.assertIn('project = LIFE AND status = "Agent Go" AND issuetype != Epic ORDER BY priority DESC, Rank ASC', delivery)
        self.assertIn("never reorder or reprioritize", delivery)
        self.assertIn("A Jira status change starts nothing", delivery)
        self.assertIn("unfinished `Blocks` predecessor", delivery)
        self.assertIn("transition ID 14", delivery)
        self.assertIn("`NO_ELIGIBLE_WORK`", delivery)
        self.assertIn("Agent Go is the approval", POLICY)
        self.assertNotIn("unavailable Agent Go transition", SCHEMA["description"])
        self.assertNotIn("unavailable on Jira Free", json.dumps(SCHEMA))

    def test_requirement_change_reconciliation_is_retired_and_t3_stays_retired(self):
        self.assertNotIn("Requirement-change reconciliation", POLICY)
        self.assertNotIn("Buzz or T3", POLICY)

    def test_los_first_discovery_and_description_first_contract_is_in_the_policy(self):
        """Jim, Nov 21 2026: LOS is retired; a new outcome starts as a LIFE Epic in Scoping; the description owns requirements; discovery answers are saved and read back."""
        rule = POLICY.split("## 7. Writing tickets", 1)[1].split("## 8.", 1)[0]
        for required in ("starts as a LIFE Epic in Scoping", "Claude writes, ChatGPT reviews", "Only `ready` tickets move to Agent Go", "The description beats comments", "never a comment alone",
                         "`Given … when … then …`", "A tick without cited proof is not Done", "read back before the next question", "Link the Notion requirement"):
            self.assertIn(required, rule)

    def _origin_errors(self, origin):
        issue = dict(SCHEMA["examples"][0], product_origin=origin)
        return list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(issue))

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_a_new_product_outcome_needs_a_scoping_epic_origin_and_los_is_refused(self):
        issue = {k: v for k, v in SCHEMA["examples"][0].items() if k != "product_origin"}
        self.assertTrue(list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(issue)))                       # missing product origin
        self.assertEqual(self._origin_errors({"kind": "scoping_epic", "epic_key": "LIFE-846", "spec_url": "https://example.com/spec", "approval_source": "Jim, 2026-11-21"}), [])
        self.assertTrue(self._origin_errors({"kind": "scoping_epic", "epic_key": "LOS-70", "spec_url": "https://example.com/spec", "approval_source": "x"}))   # LOS is retired
        self.assertTrue(self._origin_errors({"kind": "scoping_epic", "epic_key": "LIFE-846", "approval_source": "x"}))   # an Epic needs its spec link
        self.assertTrue(self._origin_errors({"kind": "los_idea", "los_key": "LOS-70", "approved_for_delivery": True, "approval_source": "Jim"}))   # no LOS origin any more
        self.assertEqual(self._origin_errors({"kind": "approved_epic_child", "epic_key": "LIFE-100", "epic_approval_source": "Jim approved the finite Epic"}), [])
        self.assertEqual(self._origin_errors({"kind": "bug", "parent_ref": "LIFE-101", "approval_source": "approved Epic LIFE-100"}), [])
        self.assertTrue(self._origin_errors({"kind": "approved_epic_child", "epic_key": "LIFE-100"}))             # a claimed exception must cite its approval
        self.assertTrue(self._origin_errors({"kind": "new_product"}))                                              # no unlisted origin

    @staticmethod
    def _unpersisted(answered, capture):
        """Test fixture check only: answered question numbers that are not in the saved entries or in the verified read-back."""
        saved = {e["question_number"] for e in capture["entries"]} & set(capture["readback"]["saved_questions"])
        return sorted(set(answered) - saved)

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_every_answered_discovery_question_must_be_saved_and_read_back(self):
        validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/discovery_capture", "$defs": DEFS})
        entry = lambda n, state="APPROVED DECISION": {"question_number": n, "owner_answer": "Jim's answer", "decision_state": state, "requirement_impact": "impact", "open_gap": "none"}
        capture = {"epic_key": "LIFE-846", "tranche": 4, "owner_reply_date": "2026-11-01", "entries": [entry(13), entry(14, "UNRESOLVED")], "readback": {"verified": True, "saved_questions": [13, 14], "revision_after": "2026-11-01T12:00:00.000-0600"}}
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
        """One owner per fact, linked not copied; the policy is one short read; heavy files are read only when needed."""
        for required in ("never copy a fact into a second place", "Read only when needed", "never read the whole file", "so an agent reads it once"):
            self.assertIn(required, POLICY)
        self.assertNotIn("token limit", POLICY.lower())

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
