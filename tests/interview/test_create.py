"""Production-shaped page-parent Notion fake: title is the only page property."""
import copy
import json
import unittest
from unittest.mock import patch
from lifeos.interview import create, notion, stage
from lifeos.interview.identity import resolve_child
from lifeos.interview.models import ParentQuery, Resolution, RoundQuery, Scan, State
from lifeos.platform.notion_client import NotionError
from tests.interview.test_notion import Fake, ENV, block, page

PARENT = ParentQuery(" New Example ", " Program Manager ")
ROUND = RoundQuery(True, "2026-10-01", ordinal=1)


class WritableFake(Fake):
    def __init__(self):
        super().__init__()
        self.blocks["root"] = [{"id": "active", "type": "child_page", "child_page": {"title": "Active Opportunities"}}]
        self.pages["active"] = page("Active Opportunities")
        self.blocks["active"] = []
        self.posts = []
        self.post_error = False
        self.bad_readback = False
        self.change_notes = False
        self.hide_created = False

    def call(self, method, path, body=None):
        if method != "POST":
            return super().call(method, path, body)
        assert path == "/pages"
        self.calls.append((method, path))
        self.posts.append(copy.deepcopy(body))
        if self.post_error:
            raise NotionError("private error body")
        assert set(body["properties"]) == {"title"}
        parent_id = body["parent"]["page_id"]
        title = "".join(x["text"]["content"] for x in body["properties"]["title"]["title"])
        page_id = f"created-{len(self.posts)}"
        self.pages[page_id] = page(title, parent_id)
        self.blocks[page_id] = copy.deepcopy(body["children"])
        for index, item in enumerate(self.blocks[page_id]):
            item["id"] = f"{page_id}-block-{index}"
        if not self.hide_created:
            self.blocks[parent_id].append({"id": page_id, "type": "child_page", "child_page": {"title": title}})
        if self.bad_readback:
            self.blocks[page_id][0] = block("missing marker")
        if self.change_notes:
            self.blocks[parent_id].insert(2, block("changed human content"))
        return {"id": page_id}


class ColumnFake(WritableFake):
    """Actual visual shape; Notion metadata independently specifies block/page ancestry."""
    def __init__(self, parent_type="block_id", enclosing="root"):
        super().__init__()
        self.blocks["root"] = [{"id": "columns", "type": "column_list", "has_children": True}]
        self.blocks["columns"] = [{"id": "left", "type": "column", "has_children": True}, {"id": "right", "type": "column", "has_children": True}]
        self.blocks["left"] = [block("Active Opportunities", "heading_2"), {"id": "opportunity", "type": "child_page", "child_page": {"title": "Synthetic — Manager"}}]
        self.blocks["right"] = [block("Retired Opportunities", "heading_2"), {"id": "retired", "type": "child_page", "child_page": {"title": "Retired — Manager"}}]
        self.pages["opportunity"] = page("Synthetic — Manager")
        self.pages["opportunity"]["parent"] = {"type": parent_type, parent_type: "left" if parent_type == "block_id" else enclosing}
        self.pages["retired"] = page("Retired — Manager")
        if enclosing != "root":
            self.blocks["root"] = [{"id": enclosing, "type": "child_page", "child_page": {"title": "Active Opportunities"}}]
            self.blocks[enclosing] = [{"id": "columns", "type": "column_list", "has_children": True}]
            self.pages[enclosing] = page("Active Opportunities")
            self.blocks["right"] = []


def machine_parent(client):
    result = stage.run(10, True, environ=ENV, client=client, evidence=[(PARENT, ROUND)])
    assert result["parents_created"] == 1
    return "created-1"


class CreationTests(unittest.TestCase):
    def run_evidence(self, client, live=True, parent=PARENT, query=ROUND, copies=1):
        return stage.run(10, live, environ=ENV, client=client, evidence=[(parent, query)] * copies)

    def test_parent_round_replay_vertical_slice_and_skeletons(self):
        for query in (ROUND, RoundQuery(True, "2026-10-01", interviewer="Synthetic Person"), RoundQuery(True, "2026-10-01", "Synthetic Person", 1)):
            with self.subTest(query=query):
                client = WritableFake()
                result = self.run_evidence(client, query=query)
                self.assertEqual((result["writes"], result["parents_created"], result["readback_ok"]), (1, 1, 1))
                self.assertEqual(len(client.posts), 1)
                parent_id = "created-1"
                self.assertEqual(client.pages[parent_id]["properties"]["title"]["title"][0]["plain_text"], "New Example — Program Manager")
                self.assertEqual([notion.text(b) for b in client.blocks[parent_id]], [notion.MARKERS["opportunity"], "Live Notes", "Raw Notes", "Interview Rounds"])
                before = notion.protected_snapshot(client, parent_id)
                result = self.run_evidence(client, query=query)
                self.assertEqual((result["writes"], result["rounds_created"], result["readback_ok"]), (1, 1, 1))
                self.assertEqual(client.posts[-1]["parent"]["page_id"], parent_id)
                round_blocks = client.blocks["created-2"]
                self.assertEqual([notion.text(b) for b in round_blocks], [notion.MARKERS["round"], create.identity_text(query), "Live Notes", "Raw Notes", "Derived"])
                self.assertEqual(json.loads(notion.text(round_blocks[1]).split(";", 1)[1]), {"interview_date": query.interview_date, "interviewer": query.interviewer, "ordinal": query.ordinal})
                self.assertEqual(notion.protected_snapshot(client, parent_id), before)
                result = self.run_evidence(client, query=query)
                self.assertEqual(result["writes"], 0)
                self.assertEqual(result["why"]["child_match"], 1)
                self.assertEqual(len(client.posts), 2)

    def test_dry_run_and_no_evidence_never_mutate(self):
        client = WritableFake()
        result = self.run_evidence(client, False)
        self.assertEqual((result["writes_planned"], result["writes"]), (1, 0))
        self.assertEqual(result["why"]["parent_create_allowed"], 1)
        machine_parent(client)
        result = self.run_evidence(client, False)
        self.assertEqual((result["writes_planned"], result["writes"]), (1, 0))
        result = stage.run(10, True, environ=ENV, client=client)
        self.assertEqual(result["writes"], 0)
        self.assertEqual(len(client.posts), 1)
        with patch("lifeos.interview.notion.protected_snapshot", side_effect=AssertionError("probe snapshot")):
            stage.probe(10, True, environ=ENV, client=client, evidence=[(PARENT, ROUND)])
        self.assertEqual(len(client.posts), 1)

    def test_same_run_duplicate_evidence_defers_round(self):
        client = WritableFake()
        result = self.run_evidence(client, copies=2)
        self.assertEqual(result["writes"], 1)
        self.assertEqual(result["why"]["parent_exists"], 1)
        result = self.run_evidence(client, copies=2)
        self.assertEqual(result["writes"], 1)
        self.assertEqual(len(client.posts), 2)

    def test_parent_fail_closed_gates(self):
        for mode, code in (("target", "target_unreadable"), ("scan", "pipeline_incomplete"), ("invalid", "identity_invalid"), ("ambiguous", "parent_ambiguous"), ("malformed", "identity_invalid"), ("unconfirmed", "pursuit_unconfirmed"), ("missing", "active_target_incomplete"), ("duplicate", "active_target_incomplete"), ("heading", "active_target_incomplete")):
            with self.subTest(mode=mode):
                client = WritableFake()
                parent, query = PARENT, ROUND
                if mode == "target": client.fail.add("/pages/root")
                if mode == "scan": client.more = True
                if mode == "invalid": parent = ParentQuery("", "Manager")
                if mode in ("ambiguous", "malformed"):
                    title = "New Example Program Manager" if mode == "malformed" else "New Example — Program Manager"
                    client.blocks["active"] = [{"id": f"existing-{i}", "type": "child_page", "child_page": {"title": title}} for i in range(2)]
                if mode == "unconfirmed": query = RoundQuery(False)
                if mode == "missing": client.blocks["root"] = []
                if mode == "duplicate": client.blocks["root"] *= 2
                if mode == "heading": client.blocks["root"] = [block("Active Opportunities", "heading_1")]
                result = self.run_evidence(client, parent=parent, query=query)
                self.assertIn(code, result["why"])
                self.assertEqual(client.posts, [])

    def test_unrelated_malformed_parent_allows_creation(self):
        client = WritableFake()
        client.blocks["active"].append({"id": "unrelated", "type": "child_page", "child_page": {"title": "Other Manager"}})
        self.assertEqual(self.run_evidence(client)["parents_created"], 1)

    def test_round_fail_closed_gates(self):
        for mode, code in (("human", "human_page"), ("unknown", "ownership_unknown"), ("incomplete", "child_scan_incomplete"), ("ambiguous", "child_ambiguous"), ("unconfirmed", "round_unconfirmed"), ("date", "round_date_missing"), ("identity", "round_identity_incomplete")):
            with self.subTest(mode=mode):
                client = WritableFake()
                parent_id = machine_parent(client)
                query = ROUND
                if mode == "human": client.blocks[parent_id][0] = block("human")
                if mode == "unknown": client.fail.add(f"/blocks/{parent_id}/children?page_size=1")
                if mode == "incomplete": client.fail.add(f"/blocks/{parent_id}/children?page_size=100")
                if mode == "ambiguous":
                    self.run_evidence(client)
                    client.blocks[parent_id].append(copy.deepcopy(client.blocks[parent_id][-1]))
                if mode == "unconfirmed": query = RoundQuery(False, "2026-10-01", ordinal=1)
                if mode == "date": query = RoundQuery(True, ordinal=1)
                if mode == "identity": query = RoundQuery(True, "2026-10-01")
                previous = len(client.posts)
                result = self.run_evidence(client, query=query)
                self.assertIn(code, result["why"])
                self.assertEqual(len(client.posts), previous)
                self.assertEqual(result["writes"], 0)

    def test_write_and_readback_failures_never_retry(self):
        for mode, code in (("post", "write_failed"), ("readback", "readback_marker_missing"), ("identity", "readback_identity_mismatch"), ("protected", "readback_protected_changed")):
            with self.subTest(mode=mode):
                client = WritableFake()
                if mode == "protected": machine_parent(client)
                client.post_error = mode == "post"
                client.bad_readback = mode == "readback"
                client.hide_created = mode == "identity"
                client.change_notes = mode == "protected"
                previous = len(client.posts)
                result = self.run_evidence(client, copies=2)
                self.assertIn(code, result["why"])
                self.assertEqual(len(client.posts), previous + 1)
                self.assertEqual(result["writes"], 0 if mode == "post" else 1)

    def test_machine_identity_invalid_blocks_even_explicit_lookup(self):
        client = WritableFake()
        machine_parent(client)
        self.run_evidence(client)
        marker = client.blocks["created-2"][0]
        valid = create.identity_text(ROUND)
        bad = [None, "not JSON", valid.replace(":1;", ":2;"), valid.replace("2026-10-01", "2026-99-99"), valid.replace('"ordinal":1', '"ordinal":0'), valid.replace('"ordinal":1', '"ordinal":null'), valid.replace('"ordinal":1', '"ordinal":true')]
        for value in bad:
            with self.subTest(value=value):
                client.blocks["created-2"] = [marker] + ([block(value)] if value else [])
                child = notion.explicit_child(client, "created-2")
                for query in (ROUND, RoundQuery(True, explicit_child_page_id="created-2")):
                    result = resolve_child(Resolution(State.MATCH, "parent_match", "created-1"), query, Scan((child,), True), child)
                    self.assertEqual(result.code, "round_identity_incomplete")
        self.assertEqual(notion.ownership(client, "created-2", "round").value, "MACHINE")

    def test_human_legacy_reference_and_counts_privacy(self):
        client = WritableFake()
        parent_id = machine_parent(client)
        client.pages["legacy"] = page("Interview 1", parent_id)
        client.blocks["legacy"] = [block("human notes")]
        client.blocks[parent_id].append({"id": "legacy", "type": "child_page", "child_page": {"title": "Interview 1"}})
        result = self.run_evidence(client, query=RoundQuery(True, explicit_child_page_id="legacy"))
        self.assertIn("child_match", result["why"])
        result = self.run_evidence(client)
        self.assertIn("round_identity_incomplete", result["why"])
        self.assertEqual(len(client.posts), 1)
        for private in ("New Example", "Program Manager", "Interview 1", "created-1", "human notes", "v7-interview-identity"):
            self.assertNotIn(private, json.dumps(result))


class ActiveShapeTests(unittest.TestCase):
    def probe(self, client):
        from lifeos.platform.runtime import RunContext
        return notion.active_shape(client, "root", RunContext.start(60))

    def test_legal_dedicated_page_parent_supported(self):
        client = ColumnFake("page_id", "active")
        result = self.probe(client)
        self.assertEqual(result["code"], "active_shape_supported")
        self.assertEqual(result["active_heading_count"], 1)
        self.assertEqual(result["active_heading_parent_type"], "column")
        self.assertTrue(result["active_pages_same_parent"])
        self.assertTrue(result["active_pages_parent_verified"])

    def test_missing_duplicate_and_different_parent(self):
        for mode, code in (("missing", "active_shape_incomplete"), ("duplicate", "active_shape_ambiguous"), ("different", "active_shape_ambiguous")):
            with self.subTest(mode=mode):
                client = ColumnFake()
                if mode == "missing": client.blocks["left"][0] = block("Other Heading", "heading_2")
                if mode == "duplicate": client.blocks["right"].insert(0, block("Active Opportunities", "heading_2"))
                if mode == "different":
                    client.blocks["left"].append({"id": "another", "type": "child_page", "child_page": {"title": "Synthetic Other — Manager"}})
                    client.pages["another"] = page()
                    client.pages["another"]["parent"] = {"type": "block_id", "block_id": "elsewhere"}
                self.assertEqual(self.probe(client)["code"], code)

    def test_unreadable_parent_metadata(self):
        client = ColumnFake()
        client.fail.add("/pages/opportunity")
        self.assertEqual(self.probe(client)["code"], "active_shape_unreadable")

    def test_column_block_and_root_page_parents_unsupported(self):
        for kind in ("block_id", "page_id"):
            with self.subTest(kind=kind):
                client = ColumnFake(kind)
                result = self.probe(client)
                self.assertEqual(result["code"], "active_shape_not_page_parent")
                self.assertFalse(result["insertion_supported"])
                result = stage.run(10, True, environ=ENV, client=client, evidence=[(PARENT, ROUND)])
                self.assertIn("active_target_incomplete", result["why"])
                self.assertEqual(client.posts, [])

    def test_retired_not_evidence_and_probe_public_get_only(self):
        client = ColumnFake()
        client.fail.add("/pages/retired")
        result = stage.probe(10, True, environ=ENV, client=client)
        self.assertEqual(result["active_page_count"], 1)
        self.assertIn("active_shape_not_page_parent", result["why"])
        self.assertTrue(all(method == "GET" for method, _ in client.calls))
        self.assertNotIn(("GET", "/pages/retired"), client.calls)
        serialized = json.dumps(result)
        for private in ("Synthetic", "Manager", "root", "left", "opportunity", "http"):
            self.assertNotIn(private, serialized)

    def test_incomplete_enumeration_and_retired_crossover(self):
        client = ColumnFake()
        client.more = True
        self.assertEqual(self.probe(client)["code"], "active_shape_incomplete")
        client = ColumnFake("page_id", "active")
        client.blocks["right"] = [block("Retired Opportunities", "heading_2")]
        self.assertEqual(self.probe(client)["code"], "active_shape_ambiguous")
