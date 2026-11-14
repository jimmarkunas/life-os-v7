"""Run one Inbox action exactly once and claim completion only from the source's own read-back (LIFE-685).

Gates in order: replay of the same request, policy, record freshness, exact authorization (single use), already applied to this revision, execute, read-back. The executor is injected.
Outcome.status: completed (read-back shows the expected status on a new revision), unverified (nothing is claimed), failed (the executor raised), refused (a gate said no, nothing ran).
Audit and log lines carry ids, action and reason words only: never a title, description, credential or exception text."""
import json
import logging
from dataclasses import dataclass

from lifeos.inbox import policy

LOG = logging.getLogger("lifeos.inbox")


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    replayed: bool = False


class ActionService:
    def __init__(self, key, executor, audit=None):
        self._key, self._executor = key, executor
        self.audit = audit if audit is not None else []
        self._by_request = {}                    # idempotency key -> (principal_id, record_id, action, Outcome)
        self._nonces = set()                     # authorizations already spent
        self._applied = set()                    # (record_id, action, revision) already executed

    def _note(self, principal, record, action, outcome):
        line = {"principal": principal.principal_id, "record": record.record_id, "action": action, "status": outcome.status, "reason": outcome.reason}
        self.audit.append(line)
        LOG.info(json.dumps(line, sort_keys=True))
        return outcome

    def request(self, principal, record, action, auth, idempotency_key, now):
        prior = self._by_request.get(idempotency_key)
        if prior:
            if prior[:3] != (principal.principal_id, record.record_id, action):
                return self._note(principal, record, action, Outcome("refused", "idempotency_key_reused"))
            return Outcome(prior[3].status, prior[3].reason, replayed=True)               # the same request again: the stored answer, no second execution
        outcome = self._run(principal, record, action, auth, now)
        if outcome.status != "refused":
            self._by_request[idempotency_key] = (principal.principal_id, record.record_id, action, outcome)
        return self._note(principal, record, action, outcome)

    def _run(self, principal, record, action, auth, now):
        allowed, reason = policy.decide(principal, record, action, now)
        if not allowed:
            return Outcome("refused", reason)
        if action in policy.READ_ACTIONS:
            return Outcome("completed", "read_only")
        if record.freshness(now) != "fresh":
            return Outcome("refused", "record_stale")
        reason = policy.verify(self._key, auth, principal, record, action, now)
        if reason:
            return Outcome("refused", reason)
        if auth.nonce in self._nonces:
            return Outcome("refused", "authorization_used")
        if (record.record_id, action, record.revision) in self._applied:
            return Outcome("refused", "already_applied")
        self._nonces.add(auth.nonce)
        self._applied.add((record.record_id, action, record.revision))
        try:
            readback = self._executor(record, action)
        except Exception:                                                                    # noqa: BLE001 - never repeated, never quoted; the source read-back decides what is true
            return Outcome("failed", "executor_failed")
        proven = (isinstance(readback, dict) and readback.get("record_id") == record.record_id and readback.get("revision") not in (None, record.revision)
                  and readback.get("source_status") in policy.EXPECTED_STATUS[action])
        return Outcome("completed", "read_back_verified") if proven else Outcome("unverified", "read_back_not_proven")
