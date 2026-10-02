import copy
import json
import unittest
from unittest.mock import patch
from lifeos.interview import create, notion, prep
from lifeos.interview.models import ParentQuery, PrepEvidence, RoundQuery
from lifeos.platform.notion_client import NotionError
from lifeos.platform.runtime import RunContext
from tests.interview.test_create import WritableFake, machine_parent, PARENT, ROUND
from tests.interview.test_notion import ENV, block


def machine_round(client, ordinal=1, day=None):
    parent_id = machine_parent(client)
    query = RoundQuery(True, day or "2026-10-01", ordinal=ordinal)
    result = create.apply(client, ENV, PARENT, query, True, RunContext.start(60), set())
    return parent_id, "created-2", query


class PrepFake(WritableFake):
    def call(self, method, path, body=None):
        if method == "PATCH":
            self.calls.append((method, path))
            page_id = path.split("/")[2]
            index = next((i for i, block_value in enumerate(self.blocks[page_id]) if block_value.get("id") == body.get("after")), len(self.blocks[page_id]) - 1)
            inserted = copy.deepcopy(body["children"])
            for i, value in enumerate(inserted):
                value["id"] = f"derived-{i}"
            self.blocks[page_id][index + 1:index + 1] = inserted
            return {}
        return super().call(method, path, body)


class InputTests(unittest.TestCase):
    def test_validation_normalization_hash_order_and_render(self):
        for value in (PrepEvidence(), PrepEvidence(focus=("  ",)), PrepEvidence(focus=("x" * 501,)),
                      PrepEvidence(focus=tuple(str(i) for i in range(6)))):
            with self.assertRaises((ValueError, OverflowError)):
                prep.normalize(value)
        normalized = prep.normalize(PrepEvidence(focus=(" First ", "First", "Second"), questions=("Q2", "Q1")))
        self.assertEqual(normalized["focus"], ("First", "Second"))
        self.assertEqual(normalized["questions"], ("Q2", "Q1"))
        self.assertEqual(prep.digest(normalized), prep.digest(normalized))
        rendered = prep.render(normalized)
        self.assertEqual([notion.text(b) for b in rendered[1:]], ["Focus", "First", "Second", "Questions", "Q2", "Q1"])
        self.assertEqual([b["type"] for b in rendered].count("heading_3"), 2)


class PrepTests(unittest.TestCase):
    def apply(self, client, query, data, live=True):
        return prep.apply(client, ENV, PARENT, query, data, live, RunContext.start(60))

    def test_dry_live_exact_replay_and_conflict_are_append_only(self):
        client = PrepFake()
        _, round_id, query = machine_round(client)
        evidence = PrepEvidence(focus=("Focus A",), pressure_points=("Risk A",), questions=("Ask A",))
        before = notion.protected_snapshot(client, round_id)
        dry = self.apply(client, query, evidence, False)
        self.assertEqual((dry["writes_planned"], dry["writes"], dry["code"]), (1, 0, "derived_allowed"))
        self.assertEqual(self.apply(client, query, evidence)["code"], "derived_created")
        self.assertEqual(self.apply(client, query, evidence)["code"], "derived_match")
        self.assertEqual(len([c for c in client.calls if c[0] == "PATCH"]), 1)
        blocks = client.blocks[round_id]
        heading = next(i for i, b in enumerate(blocks) if notion.text(b) == "Derived")
        self.assertTrue(notion.text(blocks[heading + 1]).startswith(prep.DERIVED_PREFIX))
        self.assertEqual(notion.protected_snapshot(client, round_id), before)
        self.assertEqual(self.apply(client, query, PrepEvidence(focus=("Different",)))["code"], "derived_conflict")
        self.assertEqual(len([c for c in client.calls if c[0] == "PATCH"]), 1)

    def test_protected_snapshot_failure_and_append_failure_do_not_retry(self):
        client = PrepFake()
        _, round_id, query = machine_round(client)
        with patch("lifeos.interview.notion.protected_snapshot", return_value=None):
            result = self.apply(client, query, PrepEvidence(focus=("A",)))
        self.assertEqual(result["code"], "readback_protected_missing")
        self.assertFalse(any(c[0] == "PATCH" for c in client.calls))
        original = notion.append_children
        with patch("lifeos.interview.notion.append_children", side_effect=NotionError("private")) as append:
            result = self.apply(client, query, PrepEvidence(focus=("A",)))
        self.assertEqual(result["code"], "derived_readback_failed")
        append.assert_called_once()
        with patch("lifeos.interview.notion.append_children", side_effect=original):
            self.assertEqual(self.apply(client, query, PrepEvidence(focus=("A",)))["code"], "derived_created")

    def test_unexpected_derived_content_conflicts_and_notes_are_not_inputs(self):
        client = PrepFake()
        _, round_id, query = machine_round(client)
        client.blocks[round_id].append(block("existing payload"))
        protected = notion.protected_snapshot(client, round_id)
        calls = []
        original = client.call
        def record(method, path, body=None):
            calls.append(path)
            return original(method, path, body)
        client.call = record
        result = self.apply(client, query, PrepEvidence(focus=("Input",)))
        self.assertEqual(result["code"], "derived_conflict")
        self.assertEqual(notion.protected_snapshot(client, round_id), protected)
        self.assertFalse(any("Live Notes" in path or "Raw Notes" in path for path in calls))
        serialized = json.dumps(result)
        self.assertNotIn("Input", serialized)

    def test_ordinal_carry_forward_merges_only_pressure_and_questions(self):
        client = PrepFake()
        parent_id, previous_id, previous_query = machine_round(client, ordinal=1)
        old = PrepEvidence(focus=("Old focus",), strongest_evidence=("Old proof",),
                           pressure_points=("Old risk", "Shared risk"), questions=("Old question",))
        self.assertEqual(prep.apply(client, ENV, PARENT, previous_query, old, True, RunContext.start(60))["code"], "derived_created")
        current_query = RoundQuery(True, "2026-10-02", ordinal=3)
        create.apply(client, ENV, PARENT, current_query, True, RunContext.start(60), set())
        current = PrepEvidence(focus=("Current focus",), strongest_evidence=("Current proof",),
                               pressure_points=("Current risk", "Shared risk"), questions=("Current question",))
        result = self.apply(client, current_query, current)
        self.assertEqual(result["code"], "derived_created")
        _, heading, payload = notion.derived_blocks(client, "created-3")
        rendered = [notion.text(b) for b in payload]
        self.assertIn("Current risk", rendered)
        self.assertIn("Old risk", rendered)
        self.assertEqual(rendered.count("Shared risk"), 1)
        self.assertIn("Old question", rendered)
        self.assertNotIn("Old focus", rendered)
        self.assertNotIn("Old proof", rendered)
        self.assertLess(rendered.index("Current risk"), rendered.index("Old risk"))
        self.assertLess(rendered.index("Current question"), rendered.index("Old question"))

    def test_date_selection_and_bad_or_human_prior_rounds_are_ignored(self):
        client = PrepFake()
        parent_id, old_id, _ = machine_round(client, ordinal=1, day="2026-09-29")
        old_query = RoundQuery(True, "2026-09-29", interviewer="Prior interviewer")
        old = PrepEvidence(pressure_points=("Earlier",), questions=("Earlier question",))
        prep.apply(client, ENV, PARENT, old_query, old, True, RunContext.start(60))
        # A malformed machine round and a human round must not be selected.
        client.blocks[parent_id].append({"id": "bad-round", "type": "child_page", "child_page": {"title": "Interview broken"}})
        client.pages["bad-round"] = copy.deepcopy(client.pages[old_id])
        client.pages["bad-round"]["parent"] = {"page_id": parent_id}
        client.blocks["bad-round"] = [block(notion.MARKERS["round"]), block("invalid identity")]
        client.blocks["bad-round"][0]["id"] = "bad-marker"
        client.blocks["bad-round"][1]["id"] = "bad-identity"
        client.blocks[parent_id].append({"id": "human-round", "type": "child_page", "child_page": {"title": "Human"}})
        client.pages["human-round"] = copy.deepcopy(client.pages[old_id])
        client.pages["human-round"]["parent"] = {"page_id": parent_id}
        client.blocks["human-round"] = [block("human")]
        current_query = RoundQuery(True, "2026-10-02", interviewer="Current interviewer", ordinal=2)
        current_id = "date-current"
        client.pages[current_id] = {"parent": {"page_id": parent_id}, "properties": {"title": {"type": "title", "title": []}}}
        client.blocks[current_id] = [block(notion.MARKERS["round"]), block(create.identity_text(RoundQuery(True, "2026-10-02", ordinal=2))),
                                     block("Live Notes", "heading_2"), block("Raw Notes", "heading_2"), block("Derived", "heading_2")]
        client.blocks[current_id][-1]["id"] = "current-derived"
        client.blocks[parent_id].append({"id": current_id, "type": "child_page", "child_page": {"title": "Date current"}})
        current_query = RoundQuery(True, "2026-10-02", ordinal=2, explicit_child_page_id=current_id)
        result = prep.apply(client, ENV, PARENT, RoundQuery(True, "2026-10-02", ordinal=2, explicit_child_page_id=current_id), PrepEvidence(focus=("Date current",)), True, RunContext.start(60))
        self.assertEqual(result["code"], "derived_created")
        _, heading, _ = notion.derived_blocks(client, current_id)
        _, _, payload = notion.derived_blocks(client, current_id)
        self.assertIn("Earlier", [notion.text(b) for b in payload])

    def test_rejects_oversized_caps_and_returns_counts_only(self):
        client = PrepFake()
        _, _, query = machine_round(client)
        for evidence, code in ((PrepEvidence(focus=("x" * 501,)), "prep_too_large"),
                               (PrepEvidence(questions=("q",) * 6), "prep_too_large"),
                               (PrepEvidence(questions=(" ",)), "prep_invalid")):
            result = self.apply(client, query, evidence)
            self.assertEqual(result["code"], code)
            self.assertEqual(result["writes"], 0)
            self.assertNotIn("x" * 20, json.dumps(result))

    def test_return_and_logs_exclude_private_evidence(self):
        client = PrepFake()
        _, _, query = machine_round(client)
        private = "Confidential synthetic evidence"
        result = self.apply(client, query, PrepEvidence(focus=(private,)))
        self.assertEqual(set(result), {"writes_planned", "writes", "code"})
        self.assertNotIn(private, json.dumps(result))
        self.assertFalse(any("/jobs/" in path or "Live Notes" in path or "Raw Notes" in path for _, path in client.calls))


if __name__ == "__main__":
    unittest.main()
