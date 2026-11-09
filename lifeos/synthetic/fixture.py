"""The fake world for LIFE-373 (fixture contract: LIFE-372). Tenants use neutral labels; the mapping to the labels in the Jira text is
PERSONAL-TEST = TENANT-A-TEST, BYTALOS-TEST = TENANT-B-TEST, TGP-TEST (denied decoy) = TENANT-DECOY-TEST. Everything lives in memory."""
import hashlib
import json

TENANT_A, TENANT_B, TENANT_DECOY = "TENANT-A-TEST", "TENANT-B-TEST", "TENANT-DECOY-TEST"
TOPIC = "N-TOPIC-1"


class World:
    """Sources, revoked ids, topics, the quarantine ledger and the side-effect counters. state_hash() is the read-back for 'nothing changed'."""

    def __init__(self):
        self.sources = {                                   # id -> tenant, kind, revision, text (invented)
            "SYN-1": {"tenant": TENANT_A, "kind": "jira", "revision": 3, "text": "Fake ticket text."},
            "MTG-1": {"tenant": TENANT_A, "kind": "meeting", "revision": 2, "text": "Fake meeting notes."},
            "DOC-1": {"tenant": TENANT_A, "kind": "drive", "revision": 5, "text": "Fake document body."},
            "B-DOC-1": {"tenant": TENANT_B, "kind": "drive", "revision": 1, "text": "Fake tenant B document."},
            "DECOY-DOC-1": {"tenant": TENANT_DECOY, "kind": "drive", "revision": 1, "text": "Fake decoy document."},
        }
        self.revoked = set()
        self.topics = {TOPIC: {"tenant": TENANT_A, "version": 1, "citations": [], "lineage": [], "relations": [], "writer": None}}
        self.quarantine = []                               # topic ids whose lineage holds a revoked source (a fake ledger, nothing is deleted)
        self.seen_nonces = set()
        self.read_calls = self.mutation_calls = self.commits = 0

    def revoke(self, source_id):
        """Mark a source revoked and quarantine every topic derived from it. Local ledger only."""
        self.revoked.add(source_id)
        for tid, topic in self.topics.items():
            if any(s == source_id for s, _ in topic["lineage"]) and tid not in self.quarantine:
                self.quarantine.append(tid)

    def state_hash(self):
        body = {"sources": self.sources, "revoked": sorted(self.revoked), "topics": self.topics, "quarantine": self.quarantine, "nonces": sorted(self.seen_nonces)}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
