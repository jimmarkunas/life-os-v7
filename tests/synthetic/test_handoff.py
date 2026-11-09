"""LIFE-375 / GWT-T06-01 and GWT-T06-02: a synthetic signed handoff and a fake receiver. Fake data only; nothing is sent anywhere and no agent is started."""
import json
import unittest

from lifeos.synthetic import handoff
from lifeos.synthetic.authz_fixture import CANARY, AuthzWorld

TASK, REFS = "task-a", ["SYN-1", "MTG-1", "DOC-1"]


def setup():
    w = AuthzWorld()
    return w, handoff.Receiver(w)


def send(w, **kw):
    args = dict(sender="agent-a", recipient="orca-fake", task_key=TASK, refs=REFS, nonce="n1")
    args.update(kw)
    return handoff.emit(w, **args)


class FirstEvent(unittest.TestCase):
    def test_a_signed_event_is_acknowledged_exactly_and_carries_ids_only(self):
        w, r = setup()
        env = send(w)
        self.assertEqual(set(env), set(handoff.FIELDS))
        self.assertEqual((env["case"], env["type"], env["scope"], env["refs"]), ("task-a-HANDOFF", "handoff_request", w.agents["agent-a"]["tenant"], REFS))
        got = r.receive(env)
        self.assertEqual((got["outcome"], got["code"], got["case"], got["nonce"], got["recipient"], got["refs"], got["writes"]), ("ack", None, "task-a-HANDOFF", "n1", "orca-fake", 3, 1))
        self.assertRegex(got["ack"], r"^[0-9a-f]{12}$")
        self.assertEqual(got["ack"], handoff.Receiver(AuthzWorld()).receive(env)["ack"])               # deterministic: the same event gets the same ack anywhere
        self.assertEqual(r.agent_starts, 0)

    def test_nothing_but_ids_reaches_the_inbox(self):
        w, r = setup()
        r.receive(send(w))
        entry = next(iter(r.inbox.values()))
        self.assertEqual(set(entry), {"task_key", "scope", "refs", "at"})
        for private in ("fake content", "Fake ticket text", "Fake meeting notes"):
            self.assertNotIn(private, json.dumps(entry, default=str))                                 # the source text never travels, only the reference ids


class Refusals(unittest.TestCase):
    def refused(self, env, code, w=None, r=None):
        if w is None:
            w, r = setup()
        got = r.receive(env)
        self.assertEqual((got["outcome"], got["code"], got["ack"]), ("refused", code, None), got)
        self.assertEqual((r.inbox, r.acks, r.agent_starts), ({}, {}, 0))
        return got

    def test_forged_or_changed_events(self):
        w, r = setup()
        good = send(w)
        self.refused({**good, "refs": ["SYN-1"]}, "DENY_SIGNATURE", w, r)                                  # changed after signing
        self.refused({**good, "nonce": "n2"}, "DENY_SIGNATURE", w, r)
        self.refused(send(w, key="the-wrong-key"), "DENY_SIGNATURE", w, r)                                 # signed with a key that is not the sender's
        self.refused({**good, "sig": "0" * 64}, "DENY_SIGNATURE", w, r)
        self.refused(send(w, sender="agent-b"), "DENY_TASK_SCOPE", w, r)                                  # a valid signature for a task that is not agent-b's
        self.refused({**good, "sender": "agent-rogue", "sig": handoff.signature(handoff.KEYS["agent-rogue"], {k: v for k, v in good.items() if k not in ("sig", "sender")} | {"sender": "agent-rogue"})},
                     "DENY_PRINCIPAL", w, r)

    def test_misaddressed(self):
        w, r = setup()
        self.refused(send(w, recipient="someone-else"), "DENY_RECIPIENT", w, r)

    def test_off_scope(self):
        w, r = setup()
        self.refused(send(w, scope="TENANT-B-TEST"), "DENY_TASK_SCOPE", w, r)                             # scope is not the sender's tenant
        self.refused(send(w, refs=["SYN-1", "B-DOC-1"]), "DENY_TASK_SCOPE", w, r)                          # a reference that belongs to another tenant
        self.refused(send(w, refs=["NO-SUCH-REF"]), "DENY_TASK_SCOPE", w, r)
        self.refused(send(w, task_key="task-a-blocked"), "DENY_TASK_SCOPE", w, r)                          # a blocked task
        self.refused(send(w, task_key="no-such-task"), "DENY_TASK_SCOPE", w, r)

    def test_malformed(self):
        w, r = setup()
        good = send(w)
        for bad in ("not a dict", {**good, "refs": []}, {**good, "refs": "SYN-1"}, {**good, "refs": ["lower case ref"]}, {**good, "type": "other_type"}, {**good, "community": "elsewhere"},
                    {k: v for k, v in good.items() if k != "case"}, {**good, "extra_field": "x"}):
            self.refused(bad, "DENY_INPUT", w, r)

    def test_raw_content_or_a_secret_never_gets_in(self):
        w, r = setup()
        good = send(w)
        for name in ("transcript", "token", "password", "body", "message_text"):
            self.refused({**good, name: "anything"}, "DENY_SECRET_EXPOSURE", w, r)
        self.refused({**good, "nonce": f"n-{CANARY}"}, "DENY_SECRET_EXPOSURE", w, r)
        self.refused(send(w, refs=[CANARY]), "DENY_SECRET_EXPOSURE", w, r)


class DuplicatesAndFailures(unittest.TestCase):
    def test_a_replay_is_acknowledged_again_with_the_same_ack_and_writes_nothing(self):
        w, r = setup()
        env = send(w)
        first = r.receive(env)
        for _ in range(5):
            again = r.receive(env)
            self.assertEqual((again["outcome"], again["code"], again["ack"], again["writes"]), ("duplicate", "DENY_REPLAY_OR_WRITER", first["ack"], 1))
        self.assertEqual((len(r.inbox), r.agent_starts), (1, 0))

    def test_the_same_nonce_from_another_sender_is_not_a_duplicate(self):
        w, r = setup()
        r.receive(send(w))
        w.tasks["task-b"]["agent"] = "agent-b"
        other = r.receive(send(w, sender="agent-b", task_key="task-b", refs=["B-DOC-1"]))
        self.assertEqual((other["outcome"], len(r.inbox)), ("ack", 2))

    def test_a_failed_receiver_writes_nothing_and_a_retry_is_accepted_exactly_once(self):
        w, r = setup()
        env = send(w)
        r.fail_next = True
        failed = r.receive(env)
        self.assertEqual((failed["outcome"], failed["code"], failed["ack"], len(r.inbox), len(r.acks)), ("failed", "RECEIVER_FAILED", None, 0, 0))
        retry = r.receive(env)
        self.assertEqual((retry["outcome"], retry["writes"]), ("ack", 1))
        self.assertEqual(r.receive(env)["outcome"], "duplicate")
        self.assertEqual((len(r.inbox), r.agent_starts), (1, 0))

    def test_a_refusal_never_touches_the_world_and_nothing_starts_an_agent(self):
        w, r = setup()
        before = (w.provider_hash(), w.ledger_hash())
        for env in (send(w, recipient="x"), send(w, key="k"), send(w, scope="TENANT-B-TEST")):
            r.receive(env)
        r.receive(send(w))
        self.assertEqual((w.provider_hash(), w.ledger_hash()), before)
        self.assertEqual(r.agent_starts, 0)
        self.assertFalse([n for n in dir(r) if "start" in n.lower() and callable(getattr(r, n))])        # the receiver has no way to start anything


if __name__ == "__main__":
    unittest.main()
