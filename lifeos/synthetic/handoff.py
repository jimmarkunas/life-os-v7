"""A synthetic signed handoff and a fake receiver (LIFE-375, LOS-86 T06). Fake data only: no Buzz relay, no network, no agent is started.

The envelope has the form of the existing Hermes request (LIFE-307): a community, a recipient alias, the event type handoff_request, a case id derived from the task,
and a nonce. It carries a task key, a scope and cited reference ids ONLY: never a transcript, a token or any message content. The signature is a keyed digest with invented
keys that stands in for the real relay signature (the real one is LIFE-216, still unverified).

The receiver checks, in this order: the shape (and that nothing but ids arrived), the signature, the recipient, the task and scope, the references, the nonce. A repeat of
an accepted event is acknowledged again with the SAME acknowledgment and writes nothing. A failed receiver writes nothing and acknowledges nothing, so a later retry is
accepted exactly once. The receiver has no way to start an agent; `agent_starts` stays 0 and a test holds it there."""
import hashlib
import json

from lifeos.synthetic.authz_fixture import CANARY, NOW, ref_hash

COMMUNITY, ETYPE = "life-os", "handoff_request"
KEYS = {"agent-a": "fake-signing-key-a", "agent-b": "fake-signing-key-b", "agent-rogue": "fake-signing-key-rogue"}          # invented; not secrets
FIELDS = ("community", "type", "sender", "recipient", "case", "task_key", "scope", "refs", "nonce", "sig")
CONTENT_NAMES = ("transcript", "token", "secret", "password", "body", "text", "message", "content")      # an extra field with one of these names is raw content


def ref_ok(value):
    """A reference id is 1 to 32 characters of capital letters, digits and dashes, not starting with a dash (SYN-1, DOC-1)."""
    return isinstance(value, str) and 0 < len(value) <= 32 and value[0] != "-" and all(c.isdigit() or c == "-" or (c.isalpha() and c.isupper() and c.isascii()) for c in value)


def case_of(task_key):
    return f"{task_key}-HANDOFF"


def signature(key, body):
    """A keyed digest over the canonical body. Stands in for the relay signature; it is deliberately not called a real one."""
    return hashlib.sha256(f"fake-signature|{key}|{json.dumps(body, sort_keys=True)}".encode()).hexdigest()


def emit(world, sender, recipient, task_key, refs, nonce, scope=None, key=None):
    """The fake signed event for one authorized task. `scope` defaults to the sender's tenant; `key` defaults to the sender's own key."""
    body = {"community": COMMUNITY, "type": ETYPE, "sender": sender, "recipient": recipient, "case": case_of(task_key), "task_key": task_key,
            "scope": scope or world.agents[sender]["tenant"], "refs": list(refs), "nonce": nonce}
    return {**body, "sig": signature(key or KEYS[sender], body)}


def _result(outcome, code=None, ack=None, extra=None):
    return {"outcome": outcome, "code": code, "ack": ack, **(extra or {})}


class Receiver:
    """The fake Orca or Codex side. It records one inbox entry per accepted event and nothing else."""

    def __init__(self, world, name="orca-fake"):
        self.world, self.name = world, name
        self.inbox, self.acks, self.agent_starts, self.fail_next = {}, {}, 0, False

    def receive(self, env):
        if not isinstance(env, dict):
            return _result("refused", "DENY_INPUT")
        if any(name in str(key).lower() for key in env for name in CONTENT_NAMES) or CANARY in json.dumps(env, default=str):
            return _result("refused", "DENY_SECRET_EXPOSURE")                         # raw content or a secret-shaped value: refused before anything else is read
        refs = env.get("refs")
        if (set(env) != set(FIELDS) or not all(isinstance(env[k], str) and env[k] for k in FIELDS if k != "refs") or not isinstance(refs, list) or not refs
                or not all(ref_ok(r) for r in refs) or env["community"] != COMMUNITY or env["type"] != ETYPE):
            return _result("refused", "DENY_INPUT")
        key = KEYS.get(env["sender"])
        if key is None or env["sender"] not in self.world.agents:
            return _result("refused", "DENY_PRINCIPAL")
        body = {k: v for k, v in env.items() if k != "sig"}
        if signature(key, body) != env["sig"]:
            return _result("refused", "DENY_SIGNATURE")                                  # forged, or changed after signing
        if env["recipient"] != self.name:
            return _result("refused", "DENY_RECIPIENT")                                  # misaddressed
        task = self.world.tasks.get(env["task_key"])
        tenant = self.world.agents[env["sender"]]["tenant"]
        if (task is None or task["agent"] != env["sender"] or task["blocked"] or env["scope"] != tenant or env["case"] != case_of(env["task_key"])
                or any(r not in self.world.sources or self.world.sources[r]["tenant"] != tenant for r in refs)):
            return _result("refused", "DENY_TASK_SCOPE")                                 # off scope: not this sender's task, another tenant, or a reference outside the scope
        ident = (env["sender"], env["case"], env["nonce"])
        if ident in self.acks:
            return _result("duplicate", "DENY_REPLAY_OR_WRITER", self.acks[ident], {"writes": len(self.inbox)})
        if self.fail_next:
            self.fail_next = False
            return _result("failed", "RECEIVER_FAILED")                                  # nothing written, nothing acknowledged: a retry will be accepted once
        ack = ref_hash("ack", self.name, *ident)
        self.inbox[ident] = {"task_key": env["task_key"], "scope": env["scope"], "refs": tuple(refs), "at": NOW.isoformat()}
        self.acks[ident] = ack
        return _result("ack", None, ack, {"case": env["case"], "nonce": env["nonce"], "recipient": self.name, "refs": len(refs), "writes": len(self.inbox)})

