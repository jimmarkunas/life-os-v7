"""Adversarial tests for the fail-closed source authorization: each defect found in review has a test that fails without its fix."""

import copy
import importlib.util
import json
import pathlib
import socket
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("um_retrieval", ROOT / "tools" / "um_retrieval.py")
rt = importlib.util.module_from_spec(spec)
sys.modules["um_retrieval"] = rt
spec.loader.exec_module(rt)
um = rt.um
BEH = json.loads(rt.BEHAVIORAL_FIXTURE.read_text())
CORPUS = json.loads(um.CORPUS.read_text())
QUERY = "What does Avery prefer for meeting times?"
ITEM = {"source_ref": "FAKE-MEM-001"}


def refuse(*args, **kwargs):
    raise AssertionError("network used")


def retriever(doc=BEH, items=(ITEM,), grants="doc", graph=None, **kwargs):
    world = rt.build_world(doc)
    client = rt.CaseClient({"mode": "returns", "items": list(items)})
    grants = doc.get("grants") if grants == "doc" else grants
    return rt.Retriever(world, client, graph, grants, doc.get("messages"), **kwargs), client


def edit(doc, rec_id, **fields):
    out = copy.deepcopy(doc)
    next(s for s in out["sources"] if s["id"] == rec_id).update(fields)
    return out


class Offline(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(socket, "socket", refuse)
        patcher.start()
        self.addCleanup(patcher.stop)


class Grants(Offline):
    def test_a_missing_grants_table_denies_everything_before_any_source_is_asked(self):
        r, client = retriever(grants=None)
        out = r.answer("alpha", QUERY)
        self.assertEqual((out["abstain"], out["degraded"], client.calls), (True, "grants_unavailable", 0))

    def test_expired_revoked_and_malformed_grants_deny(self):
        cases = {
            "grant_expired": {"alpha": {"perms": ["read"], "expires": "2026-01-01T00:00:00+00:00"}},
            "grant_revoked": {"alpha": {"perms": ["read"], "revoked": True}},
            "grant_malformed": {"alpha": {"perms": "read"}},
            "tenant_not_granted": {"alpha": ["write"]},
        }
        for reason, grants in cases.items():
            r, client = retriever(grants=grants, clock=lambda: 1_800_000_000)
            out = r.answer("alpha", QUERY)
            self.assertEqual((out["abstain"], out["degraded"], client.calls), (True, reason, 0), reason)

    def test_a_grant_still_valid_before_expiry_allows(self):
        r, _ = retriever(grants={"alpha": {"perms": ["read"], "expires": "2999-01-01T00:00:00+00:00"}})
        self.assertFalse(r.answer("alpha", QUERY)["abstain"])

    def test_a_grant_revoked_while_the_source_was_answering_gives_no_output(self):
        grants = {"alpha": {"perms": ["read"]}}
        r, client = retriever(grants=grants)
        recall = client.recall

        def recall_then_revoke(*args):
            out = recall(*args)
            grants["alpha"]["revoked"] = True
            return out

        client.recall = recall_then_revoke
        out = r.answer("alpha", QUERY)
        self.assertEqual((out["abstain"], out["answers"], out["degraded"]), (True, [], "grant_revoked"))

    def test_a_record_that_stops_being_current_before_output_is_not_shown(self):
        r, client = retriever()
        recall = client.recall

        def recall_then_stale(*args):
            out = recall(*args)
            r.world.src["FAKE-MEM-001"]["status"] = "stale"
            return out

        client.recall = recall_then_stale
        r.world = um.World(copy.deepcopy(BEH))
        client.recall = lambda *a: (
            r.world.src.__setitem__("FAKE-MEM-001", {**r.world.src["FAKE-MEM-001"], "status": "stale"}),
            [ITEM],
        )[1]
        out = r.answer("alpha", QUERY)
        self.assertTrue(out["abstain"])


class Provenance(Offline):
    def test_a_memory_record_with_no_provenance_is_not_proof_of_itself(self):
        r, _ = retriever(doc=edit(BEH, "FAKE-MEM-001", provenance=[]))
        out = r.answer("alpha", QUERY)
        self.assertEqual((out["abstain"], out["stats"]["unsupported"]), (True, 1))

    def test_a_memory_record_that_lists_itself_as_its_source_is_unsupported(self):
        r, _ = retriever(doc=edit(BEH, "FAKE-MEM-001", provenance=["FAKE-MEM-001"]))
        self.assertTrue(r.answer("alpha", QUERY)["abstain"])

    def test_a_source_of_another_tenant_does_not_resolve(self):
        r, _ = retriever(doc=edit(BEH, "FAKE-MEM-001", provenance=["FAKE-MSG-020"]))
        self.assertTrue(r.answer("alpha", QUERY)["abstain"])

    def test_a_canonical_record_is_its_own_system_of_record(self):
        r, _ = retriever()
        out = r.answer("alpha", "What is the approved release day?")
        self.assertEqual([a["ref"] for a in out["answers"]], ["FAKE-CAN-001"])


class Revision(Offline):
    def test_a_record_marked_current_but_superseded_is_unverified(self):
        r, _ = retriever(doc=edit(BEH, "FAKE-MEM-001", superseded_by="FAKE-MEM-002"))
        out = r.answer("alpha", QUERY)
        self.assertEqual((out["abstain"], out["stats"]["unverified_revision"]), (True, 1))

    def test_a_claimed_revision_that_differs_from_the_registry_is_refused(self):
        r, _ = retriever(items=[{"source_ref": "FAKE-MEM-001", "revision": "r9"}])
        self.assertTrue(r.answer("alpha", QUERY)["abstain"])

    def test_a_supersession_that_crosses_tenants_is_refused_at_the_hop(self):
        doc = edit(BEH, "FAKE-MEM-006", superseded_by="FAKE-MEM-020")
        r, _ = retriever(doc=doc, items=[{"source_ref": "FAKE-MEM-006"}])
        out = r.answer("alpha", "When does Avery prefer demos?")
        self.assertEqual((out["abstain"], out["stats"]["cross_tenant_supersession"]), (True, 1))


class Live(Offline):
    def test_a_live_tombstone_hides_an_item_the_registry_still_calls_current(self):
        r, _ = retriever(items=[{"source_ref": "FAKE-MEM-001", "live_id": "c1", "text": "x", "live_state": "revoked"}])
        out = r.answer("alpha", QUERY)
        self.assertEqual((out["abstain"], out["stats"]["tombstoned"]), (True, 1))

    def test_live_text_that_differs_from_the_registry_is_refused(self):
        r, _ = retriever(items=[{"source_ref": "FAKE-MEM-001", "live_id": "c1", "text": "tampered"}])
        out = r.answer("alpha", QUERY)
        self.assertEqual((out["abstain"], out["stats"]["text_mismatch"]), (True, 1))

    def test_live_text_that_matches_is_admitted_and_traced(self):
        text = "Avery prefers morning meeting times before ten."
        r, _ = retriever(items=[{"source_ref": "FAKE-MEM-001", "live_id": "c1", "text": text}])
        out = r.answer("alpha", QUERY)
        self.assertEqual([a["ref"] for a in out["answers"]], ["FAKE-MEM-001"])
        self.assertIn({"ref": "FAKE-MEM-001", "decision": "admitted"}, out["trace"])


class Malformed(Offline):
    def test_bad_metadata_is_dropped_and_counted_never_a_crash(self):
        bad = {
            "date_none": edit(BEH, "FAKE-MEM-001", at=None),
            "subject_list": edit(BEH, "FAKE-MEM-001", subject=["x"]),
            "text_number": edit(BEH, "FAKE-MEM-001", text=5),
            "provenance_text": edit(BEH, "FAKE-MEM-001", provenance="FAKE-MSG-001"),
        }
        for name, doc in bad.items():
            try:
                r, _ = retriever(doc=doc)
                out = r.answer("alpha", QUERY)
            except Exception as error:  # noqa: BLE001
                self.fail(f"{name} crashed: {type(error).__name__}")
            self.assertIn(out["degraded"], (None,), name)

    def test_a_date_that_is_not_text_still_answers_without_a_crash(self):
        r, _ = retriever(doc=edit(BEH, "FAKE-MEM-001", at=None))
        out = r.answer("alpha", QUERY)
        self.assertEqual([a["ref"] for a in out["answers"]], ["FAKE-MEM-001"])


class Dependencies(Offline):
    def setUp(self):
        super().setUp()
        self.world = um.World(CORPUS)
        self.grants = rt.grants_for(self.world)
        self.case = CORPUS["dependency_cases"][0]

    def graph(self, ids):
        class G:
            def deps(self, case):
                return ids, 1

            def answer(self, tenant, text):
                return [], 1

        return G()

    def test_an_exact_set_is_accepted(self):
        r = rt.Retriever(self.world, None, self.graph(list(self.case["expect_ids"])), self.grants)
        out = r.answer_case(self.case)
        self.assertEqual(sorted(a["ref"] for a in out["answers"]), sorted(self.case["expect_ids"]))

    def test_a_set_with_a_foreign_or_missing_member_is_never_shown_as_complete(self):
        for extra in ("FAKE-REQ-101", "FAKE-REQ-777"):
            r = rt.Retriever(self.world, None, self.graph([*self.case["expect_ids"], extra]), self.grants)
            out = r.answer_case(self.case)
            self.assertEqual(
                (out["abstain"], out["answers"], out["degraded"]), (True, [], "incomplete_dependencies"), extra
            )

    def test_a_stale_member_is_refused(self):
        stale = next(i for i, s in self.world.src.items() if s["status"] == "stale")
        r = rt.Retriever(self.world, None, self.graph([stale]), self.grants)
        out = r.answer_case(self.case)
        self.assertEqual((out["abstain"], out["degraded"]), (True, "incomplete_dependencies"))

    def test_a_valid_but_wrong_set_is_a_lineage_mismatch(self):
        other = next(c for c in CORPUS["dependency_cases"] if c["expect_ids"] != self.case["expect_ids"])
        r = rt.Retriever(self.world, None, self.graph(list(other["expect_ids"])), self.grants)
        out = r.answer_case(self.case)
        self.assertEqual((out["abstain"], out["degraded"]), (True, "lineage_mismatch"))

    def test_a_stale_node_on_the_path_breaks_the_lineage(self):
        doc = copy.deepcopy(CORPUS)
        start = self.case["intent"]["start"]
        via = next(e["to"] for e in doc["edges"] if e["from"] == start)
        next(s for s in doc["sources"] if s["id"] == via)["status"] = "stale"
        world = um.World(doc)
        r = rt.Retriever(world, None, self.graph(list(self.case["expect_ids"])), self.grants)
        out = r.answer_case(self.case)
        self.assertTrue(out["abstain"])
        self.assertIn(out["degraded"], ("lineage_mismatch", "incomplete_dependencies"))

    def test_a_non_list_set_is_malformed(self):
        r = rt.Retriever(self.world, None, self.graph("FAKE-REQ-001"), self.grants)
        self.assertEqual(r.answer_case(self.case)["degraded"], "malformed_dependency_set")

    def test_dependency_cases_need_a_grant_like_everything_else(self):
        r = rt.Retriever(self.world, None, None, None)
        self.assertEqual(r.answer_case(self.case)["degraded"], "grants_unavailable")


class Intent(Offline):
    def test_a_dependency_question_with_a_preference_word_is_still_a_dependency_question(self):
        for query in (
            "If the compression library upgrade slips, which requirement do we prefer to fix first?",
            "Which requirement is at risk if the upgrade slips and we chose to wait?",
        ):
            self.assertEqual(rt.route(query), "dependency", query)

    def test_a_preference_question_that_mentions_slipping_stays_behavioral(self):
        self.assertEqual(rt.route("What does Avery prefer when a release slips?"), "behavioral")


class Floor(Offline):
    def test_the_floor_is_a_parameter_and_a_lower_one_admits_the_lunch_time_note(self):
        items = [{"source_ref": "FAKE-MEM-010"}]
        low, _ = retriever(items=items, floor=0.5)
        high, _ = retriever(items=items, floor=0.75)
        self.assertEqual([a["ref"] for a in low.answer("alpha", QUERY)["answers"]], ["FAKE-MEM-010"])
        self.assertTrue(high.answer("alpha", QUERY)["abstain"])


if __name__ == "__main__":
    unittest.main()
