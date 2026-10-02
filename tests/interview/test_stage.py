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
        self.assertEqual(result["writes_planned"], 0)
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
