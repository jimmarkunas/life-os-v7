"""The proposed restore guard on fabricated snapshots: live tombstones are re-applied before a restored copy serves
recall, revoked lineage goes with its source, and anything unprovable is quarantined. No network, no real data."""

import copy
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("um_restore_guard", ROOT / "tools" / "um_restore_guard.py")
guard = importlib.util.module_from_spec(spec)
sys.modules["um_restore_guard"] = guard
spec.loader.exec_module(guard)


def doc(i, ws="personal", level="explicit", kind="fact", of=None):
    env = {"kind": kind, **({"of": of} if of else {})}
    return {"id": i, "workspace": ws, "level": level, "envelope": env}


def snapshot():
    """A restored copy taken BEFORE fact F2 was revoked live: F2 is there with its text and no tombstone; C1 was derived
    from message M2 (F2's source message) and C2 from C1; a dream run R1 created D1 from C1 and removed X1 (text kept)
    and edited the peer card of t-alpha."""
    return {
        "documents": [
            doc("F1"),
            doc("F2"),
            doc("T0", kind="tombstone", of="F0"),  # an older revoke the dump already holds
            doc("C1", level="deductive"),
            doc("C2", level="deductive"),
            doc("D1", level="inductive"),
            doc("K1", level="deductive"),  # derived from F1 only: unrelated, must stay
            doc("G1", ws="um-t2-fixture-v1"),
        ],
        "conclusion_source_messages": [
            {"conclusion_id": "C1", "message_id": "F2"},
            {"conclusion_id": "K1", "message_id": "F1"},
        ],
        "document_sources": [{"derived_id": "C2", "source_id": "C1"}],
        "dream_run_changes": [
            {
                "id": "R1a",
                "run_id": "R1",
                "workspace": "personal",
                "change": "created",
                "document_id": "D1",
                "source_ids": ["C1"],
                "has_text": True,
            },
            {
                "id": "R1b",
                "run_id": "R1",
                "workspace": "personal",
                "change": "removed",
                "document_id": "X1",
                "source_ids": ["C1"],
                "has_text": True,
            },
            {
                "id": "R9",
                "run_id": "R9",
                "workspace": "personal",
                "change": "removed",
                "document_id": "X9",
                "source_ids": ["F1"],
                "has_text": True,
            },
        ],
        "peer_cards": [
            {"peer": "t-alpha", "workspace": "personal", "run_ids": ["R1"]},
            {"peer": "t-beta", "workspace": "personal", "run_ids": ["R9"]},
        ],
    }


LIVE = {"complete": True, "tombstones": {"personal": ["F2", "F0"]}}


def apply(snap, p):
    out = copy.deepcopy(snap)
    gone = set(p["delete_documents"])
    out["documents"] = [d for d in out["documents"] if d["id"] not in gone]
    out["conclusion_source_messages"] = [r for r in out["conclusion_source_messages"] if r["conclusion_id"] not in gone]
    out["document_sources"] = [r for r in out["document_sources"] if r["derived_id"] not in gone]
    scrub = set(p["scrub_dream_changes"])
    for r in out["dream_run_changes"]:
        if r["id"] in scrub or r["document_id"] in gone:
            r["has_text"], r["document_id"] = False, None
    cards = set(p["quarantine_peer_cards"])
    out["peer_cards"] = [c for c in out["peer_cards"] if (c["workspace"], c["peer"]) not in cards]
    return out


class ReApply(unittest.TestCase):
    def test_a_live_revoke_the_dump_predates_is_applied_and_its_whole_lineage_goes(self):
        p = guard.plan(snapshot(), LIVE)
        self.assertEqual(p["delete_documents"], ["C1", "C2", "D1", "F2"])
        self.assertNotIn("F1", p["delete_documents"])
        self.assertNotIn("K1", p["delete_documents"])  # derived from an unrevoked fact: kept
        self.assertNotIn("T0", p["delete_documents"])  # the audit record of an older revoke stays
        self.assertEqual(p["scrub_dream_changes"], ["R1b"])  # the removed change that kept revoked lineage text
        self.assertEqual(p["quarantine_peer_cards"], [("personal", "t-alpha")])  # edited by the touched run only
        self.assertEqual(p["servable_workspaces"], ["personal", "um-t2-fixture-v1"])

    def test_after_the_plan_is_applied_the_copy_is_servable_and_before_it_is_not(self):
        snap = snapshot()
        self.assertFalse(guard.servable(snap, LIVE))
        self.assertTrue(guard.servable(apply(snap, guard.plan(snap, LIVE)), LIVE))

    def test_a_tombstone_in_one_workspace_never_deletes_in_another(self):
        snap = snapshot()
        snap["documents"].append(doc("F2", ws="um-t2-fixture-v1"))
        p = guard.plan(snap, {"complete": True, "tombstones": {"um-t2-fixture-v1": []}})
        self.assertEqual(p["delete_documents"], [])

    def test_a_lineage_cycle_ends(self):
        snap = snapshot()
        snap["document_sources"] += [{"derived_id": "C1", "source_id": "C2"}]
        self.assertIn("C2", guard.plan(snap, LIVE)["delete_documents"])


class FailClosed(unittest.TestCase):
    def test_no_or_incomplete_live_export_quarantines_every_workspace(self):
        for export in (None, {}, {"complete": False, "tombstones": {"personal": ["F2"]}}, "x"):
            p = guard.plan(snapshot(), export)
            self.assertEqual((p["servable_workspaces"], p["delete_documents"]), ([], []), export)
            self.assertEqual(set(p["reasons"].values()), {"tombstone_export_missing"})

    def test_a_derived_row_with_no_lineage_quarantines_its_workspace_when_anything_was_revoked(self):
        snap = snapshot()
        snap["documents"].append(doc("U1", level="inductive"))  # no source record at all
        p = guard.plan(snap, LIVE)
        self.assertEqual((p["quarantine_workspaces"], p["reasons"]), (["personal"], {"personal": "lineage_missing"}))
        self.assertNotIn("personal", p["servable_workspaces"])

    def test_missing_lineage_is_tolerated_only_where_nothing_was_revoked(self):
        snap = snapshot()
        snap["documents"].append(doc("U2", ws="um-t2-fixture-v1", level="inductive"))
        self.assertIn("um-t2-fixture-v1", guard.plan(snap, LIVE)["servable_workspaces"])


class Inventory(unittest.TestCase):
    def test_a_dump_between_a_store_and_its_revoke_is_flagged_and_others_are_not(self):
        dumps = [{"stamp": 100}, {"stamp": 200}, {"stamp": 300}]
        events = [{"stored": 150, "revoked": 250}, {"stored": 50, "revoked": 120}]
        self.assertEqual(guard.straddling(dumps, events), {100: 1, 200: 1, 300: 0})


if __name__ == "__main__":
    unittest.main()
