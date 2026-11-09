"""LIFE-373 / GWT-T04-01 and the read side of GWT-T04-02: cited reads, tenant isolation, revision checks, revoked sources. Fake data only."""
import json
import unittest

from lifeos.synthetic import knowledge
from lifeos.synthetic.fixture import TENANT_A, TENANT_B, TENANT_DECOY, World

THREE = [("SYN-1", 3), ("MTG-1", 2), ("DOC-1", 5)]


class CitedReads(unittest.TestCase):
    def test_three_permitted_sources_return_cited_records(self):
        w = World()
        out, rcpt = knowledge.read(w, TENANT_A, THREE)
        self.assertEqual([(r["source_id"], r["revision"], r["tenant"]) for r in out], [("SYN-1", 3, TENANT_A), ("MTG-1", 2, TENANT_A), ("DOC-1", 5, TENANT_A)])
        self.assertEqual((rcpt["decision_code"], rcpt["read_handler_calls"], rcpt["commits"], rcpt["mutation_calls"]), ("ALLOW", 3, 0, 0))
        self.assertEqual(rcpt["provider_state_hash_before"], rcpt["provider_state_hash_after"])

    def refused(self, tenant, wanted, code, w=None):
        w = w or World()
        before = w.state_hash()
        out, rcpt = knowledge.read(w, tenant, wanted)
        self.assertIsNone(out)
        self.assertEqual(rcpt["decision_code"], code)
        self.assertEqual((rcpt["read_handler_calls"], rcpt["mutation_calls"], rcpt["commits"]), (0, 0, 0))
        self.assertEqual(w.state_hash(), before)
        return rcpt

    def test_another_tenants_source_is_refused_without_a_leak(self):
        for sid in ("B-DOC-1", "DECOY-DOC-1", "NO-SUCH-SOURCE"):
            with self.subTest(source=sid):
                rcpt = self.refused(TENANT_A, [(sid, 1)], "DENY_TENANT")
                text = json.dumps(rcpt)
                for secret in (sid, "Fake tenant B", "Fake decoy", TENANT_B, TENANT_DECOY):
                    self.assertNotIn(secret, text)

    def test_a_stale_revision_is_refused_and_named(self):
        rcpt = self.refused(TENANT_A, [("SYN-1", 2)], "DENY_STALE_SOURCE")
        self.assertEqual(rcpt["source_failure_kind"], "SOURCE_STALE")

    def test_a_revoked_source_is_refused(self):
        w = World()
        w.revoke("DOC-1")
        self.refused(TENANT_A, THREE, "DENY_SOURCE_REVOKED", w)

    def test_the_earliest_failing_step_wins(self):
        w = World()
        w.revoke("MTG-1")
        self.refused(TENANT_A, [("SYN-1", 1), ("MTG-1", 2), ("B-DOC-1", 1)], "DENY_TENANT", w)               # wrong tenant beats revoked and stale
        self.refused(TENANT_A, [("SYN-1", 1), ("MTG-1", 2)], "DENY_SOURCE_REVOKED", w)                        # revoked beats stale
        self.refused(TENANT_A, [("SYN-1", 1)], "DENY_STALE_SOURCE")

    def test_a_revoked_foreign_source_looks_the_same_as_any_foreign_source(self):
        w = World()
        w.revoke("B-DOC-1")
        revoked = self.refused(TENANT_A, [("B-DOC-1", 1)], "DENY_TENANT", w)
        self.refused(TENANT_A, [("NO-SUCH-SOURCE", 1)], "DENY_TENANT")
        self.assertNotIn("revoked", json.dumps(revoked).lower())


if __name__ == "__main__":
    unittest.main()
