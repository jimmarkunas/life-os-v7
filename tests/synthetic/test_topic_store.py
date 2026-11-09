"""LIFE-373 / GWT-T04-01 and GWT-T04-02: one version-checked topic update, refusals with zero effect, revoked-source quarantine. Fake data only."""
import json
import unittest

from lifeos.synthetic import topic_store
from lifeos.synthetic.fixture import TENANT_A, TENANT_B, TENANT_DECOY, TOPIC, World

THREE = [("SYN-1", 3), ("MTG-1", 2), ("DOC-1", 5)]


def update(w, wanted=THREE, tenant=TENANT_A, base=1, writer="w1", nonce="n1"):
    return topic_store.update(w, tenant, TOPIC, wanted, base, writer, nonce)


class OneVersionCheckedUpdate(unittest.TestCase):
    def test_citations_lineage_and_relations_match_and_one_update_commits(self):
        w = World()
        rcpt = update(w)
        topic = w.topics[TOPIC]
        self.assertEqual((rcpt["decision_code"], rcpt["commits"], rcpt["mutation_calls"], topic["version"]), ("ALLOW", 1, 1, 2))
        self.assertEqual([c["source_id"] for c in topic["citations"]], ["SYN-1", "MTG-1", "DOC-1"])
        self.assertEqual([c["revision"] for c in topic["citations"]], [3, 2, 5])
        self.assertEqual(topic["lineage"], [("SYN-1", 3), ("MTG-1", 2), ("DOC-1", 5)])
        self.assertEqual(topic["relations"], [(TOPIC, "cites", "SYN-1"), (TOPIC, "cites", "MTG-1"), (TOPIC, "cites", "DOC-1")])
        self.assertNotEqual(rcpt["provider_state_hash_before"], rcpt["provider_state_hash_after"])

    def test_the_same_request_cannot_commit_twice(self):
        w = World()
        update(w)
        again = update(w, base=2)                      # same nonce, correct new version
        self.assertEqual((again["decision_code"], again["commits"], w.topics[TOPIC]["version"]), ("DENY_REPLAY_OR_WRITER", 0, 2))


class RefusalsHaveZeroEffect(unittest.TestCase):
    def refused(self, code, w=None, **kw):
        w = w or World()
        before = w.state_hash()
        rcpt = update(w, **kw)
        self.assertEqual(rcpt["decision_code"], code)
        self.assertEqual((rcpt["mutation_calls"], rcpt["commits"], rcpt["read_handler_calls"]), (0, 0, 0))
        self.assertEqual((w.state_hash(), rcpt["provider_state_hash_after"]), (before, before))
        self.assertEqual(w.topics[TOPIC]["version"], 1)
        return rcpt

    def test_a_forbidden_tenant_source_leaks_nothing(self):
        for sid in ("B-DOC-1", "DECOY-DOC-1"):
            with self.subTest(source=sid):
                text = json.dumps(self.refused("DENY_TENANT", wanted=[("SYN-1", 3), (sid, 1)]))
                for secret in (sid, "Fake tenant B", "Fake decoy", TENANT_B, TENANT_DECOY):
                    self.assertNotIn(secret, text)

    def test_a_topic_of_another_tenant_is_refused(self):
        self.refused("DENY_TENANT", tenant=TENANT_B, wanted=[("B-DOC-1", 1)])

    def test_a_stranger_learns_nothing_about_a_topic(self):
        # a foreign caller must get the same code whether the topic exists, has another version, or does not exist at all
        codes = {self.refused("DENY_TENANT", tenant=TENANT_B, wanted=[("B-DOC-1", 1)], base=base)["decision_code"] for base in (1, 7)}
        w = World()
        before = w.state_hash()
        missing = topic_store.update(w, TENANT_B, "NO-SUCH-TOPIC", [("B-DOC-1", 1)], 1, "w1", "n1")
        self.assertEqual((codes, missing["decision_code"], missing["commits"], w.state_hash()), ({"DENY_TENANT"}, "DENY_TENANT", 0, before))
        self.assertNotIn("NO-SUCH-TOPIC", json.dumps(missing))

    def test_stale_and_revoked_content(self):
        rcpt = self.refused("DENY_STALE_SOURCE", wanted=[("SYN-1", 2), ("MTG-1", 2)])
        self.assertEqual(rcpt["source_failure_kind"], "SOURCE_STALE")
        w = World()
        w.revoke("MTG-1")
        self.refused("DENY_SOURCE_REVOKED", w=w)

    def test_a_concurrent_writer_or_a_moved_version_is_refused(self):
        w = World()
        w.topics[TOPIC]["writer"] = "other-writer"
        self.refused("DENY_REPLAY_OR_WRITER", w=w)
        self.refused("DENY_REPLAY_OR_WRITER", base=7)
        w2 = World()
        w2.seen_nonces.add("n1")
        self.refused("DENY_REPLAY_OR_WRITER", w=w2)

    def test_the_earliest_step_wins_when_two_things_are_wrong(self):
        w = World()
        w.revoke("SYN-1")
        self.refused("DENY_REPLAY_OR_WRITER", w=w, base=9)                                # writer (4) beats revoked
        self.refused("DENY_SOURCE_REVOKED", w=w, wanted=[("SYN-1", 1)])                    # revoked beats stale
        w2 = World()
        w2.revoke("B-DOC-1")
        self.refused("DENY_TENANT", w=w2, wanted=[("SYN-1", 3), ("B-DOC-1", 1)])           # a revoked FOREIGN source is just "wrong tenant"
        self.refused("DENY_TENANT", wanted=[("B-DOC-1", 1), ("SYN-1", 1)])                 # tenant beats stale


class RevokedSourceQuarantine(unittest.TestCase):
    def test_revoking_a_source_quarantines_the_topic_built_from_it(self):
        w = World()
        update(w)
        self.assertEqual(topic_store.read_topic(w, TENANT_A, TOPIC)[1], "ALLOW")
        w.revoke("DOC-1")
        self.assertEqual(w.quarantine, [TOPIC])
        topic, code = topic_store.read_topic(w, TENANT_A, TOPIC)
        self.assertEqual((topic, code), (None, "DENY_SOURCE_REVOKED"))
        self.assertIn("DOC-1", w.sources)                      # nothing is deleted
        self.assertEqual(topic_store.read_topic(w, TENANT_B, TOPIC)[1], "DENY_TENANT")

    def test_a_quarantined_topic_is_rebuilt_from_clean_sources_and_then_readable(self):
        w = World()
        update(w)
        w.revoke("DOC-1")
        again = update(w, wanted=[("SYN-1", 3), ("MTG-1", 2)], base=2, nonce="n2")
        self.assertEqual((again["decision_code"], again["commits"], w.quarantine), ("ALLOW", 1, []))
        self.assertEqual(topic_store.read_topic(w, TENANT_A, TOPIC)[1], "ALLOW")
        self.assertNotIn("DOC-1", [s for s, _ in w.topics[TOPIC]["lineage"]])

    def test_a_quarantined_topic_cannot_be_rebuilt_from_the_revoked_source(self):
        w = World()
        update(w)
        w.revoke("DOC-1")
        before = w.state_hash()
        rcpt = update(w, base=2, nonce="n2")                       # still cites the revoked DOC-1
        self.assertEqual((rcpt["decision_code"], rcpt["commits"], w.quarantine, w.state_hash()), ("DENY_SOURCE_REVOKED", 0, [TOPIC], before))

    def test_an_unrelated_revocation_leaves_the_topic_readable(self):
        w = World()
        update(w, wanted=[("SYN-1", 3)])
        w.revoke("DOC-1")
        self.assertEqual((w.quarantine, topic_store.read_topic(w, TENANT_A, TOPIC)[1]), ([], "ALLOW"))


if __name__ == "__main__":
    unittest.main()
