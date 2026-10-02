import json
import unittest
from unittest.mock import patch
from lifeos.interview import stage
from lifeos.interview.models import ParentQuery, RoundQuery
from lifeos.run import STAGES
from tests.interview.test_notion import Fake, ENV, block


class StageTests(unittest.TestCase):
    def test_probe_privacy_no_fingerprints_no_writes(self):
        client = Fake()
        with patch("lifeos.interview.notion.protected_snapshot", side_effect=AssertionError("probe fingerprints")):
            result = stage.probe(10, True, environ=ENV, client=client)
        self.assertEqual(result["observed"], 1)
        self.assertEqual(result["machine_pages"], 1)
        self.assertEqual(result["writes"], 0)
        self.assertTrue(all(method == "GET" for method, path in client.calls))
        serialized = json.dumps(result)
        for private in ("Example", "Program Manager", "private human notes", "synthetic", "root", "http"):
            self.assertNotIn(private, serialized)
        self.assertTrue(all(type(v) is int for k, v in result.items() if k != "why"))

    def test_dry_run_creation_is_only_eligibility(self):
        client = Fake()
        result = stage.run(10, False, environ=ENV, client=client,
                           evidence=[(ParentQuery("Example", "Program Manager"), RoundQuery(True, "2026-10-01", ordinal=1))])
        self.assertEqual(result["why"]["create_allowed"], 1)
        self.assertEqual(result["not_found"], 1)
        self.assertEqual(result["writes_planned"], 1)
        self.assertEqual(result["writes"], 0)
        self.assertTrue(all(method == "GET" for method, path in client.calls))

    def test_failures_are_not_empty_success(self):
        for failure in ("/pages/root", "/blocks/root/children?page_size=100", "/blocks/p/children?page_size=1"):
            client = Fake()
            client.fail.add(failure)
            result = stage.run(10, False, environ=ENV, client=client)
            self.assertGreater(result["blocked"], 0)
            self.assertTrue(result["why"])
        client = Fake()
        client.blocks["root"].append({"id": "p2", "type": "child_page", "child_page": {"title": "Example — Program Manager"}})
        client.pages["p2"] = client.pages["p"]
        result = stage.run(10, False, environ=ENV, client=client,
                           evidence=[(ParentQuery("Example", "Program Manager"), RoundQuery(True, ordinal=1))])
        self.assertGreaterEqual(result["why"]["parent_ambiguous"], 1)
        self.assertGreater(result["blocked"], 0)

    def test_cli_uses_interview_owned_stage(self):
        with patch("lifeos.interview.stage.run", return_value={"writes": 0}) as run:
            self.assertEqual(STAGES["interview"](2, False), {"writes": 0})
            run.assert_called_once_with(2, False)
        with patch("lifeos.interview.stage.probe", return_value={"writes": 0}) as probe:
            STAGES["interview-probe"](2, False)
            probe.assert_called_once_with(2, False)

    def test_blocked_parents_stop_before_ownership_and_children(self):
        client = Fake()
        client.blocks["root"].append({"id": "duplicate", "type": "child_page", "child_page": {"title": "Example — Program Manager"}})
        with patch("lifeos.interview.notion.ownership", side_effect=AssertionError("blocked ownership read")), \
             patch("lifeos.interview.notion.child_scan", side_effect=AssertionError("blocked child scan")):
            result = stage.run(10, True, environ=ENV, client=client)
        self.assertEqual(result["observed"], 2)
        self.assertEqual(result["blocked"], 2)
        self.assertEqual(result["why"], {"parent_ambiguous": 2})
        for count in ("valid_parents", "with_rounds", "machine_pages", "human_pages", "writes_planned", "writes"):
            self.assertEqual(result[count], 0)
        self.assertEqual(client.calls, [("GET", "/pages/root"), ("GET", "/blocks/root/children?page_size=100")])

    def test_unrelated_malformed_parent_does_not_hide_valid_parent(self):
        client = Fake()
        client.blocks["root"].append({"id": "bad", "type": "child_page", "child_page": {"title": "Other Program Manager"}})
        result = stage.run(10, True, environ=ENV, client=client)
        self.assertEqual(result["valid_parents"], 1)
        self.assertEqual(result["machine_pages"], 1)
        self.assertEqual(result["why"], {"identity_invalid": 1})
        self.assertFalse(any("/bad/" in path for _, path in client.calls))

    def test_legacy_round_preserves_parent_and_human_gate(self):
        client = Fake()
        client.blocks["p"][0] = block("human opportunity")
        client.blocks["p"].append({"id": "c", "type": "child_page", "child_page": {"title": "Legacy Round"}})
        client.blocks["c"][0] = block("human round")
        result = stage.run(10, True, environ=ENV, client=client,
                           evidence=[(ParentQuery("Example", "Program Manager"), RoundQuery(True, explicit_child_page_id="c"))])
        self.assertEqual(result["valid_parents"], 1)
        self.assertEqual(result["with_rounds"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["human_pages"], 2)
        self.assertEqual(result["why"]["human_page"], 1)
        self.assertEqual(result["writes"], 0)
        self.assertTrue(all(method == "GET" for method, _ in client.calls))
