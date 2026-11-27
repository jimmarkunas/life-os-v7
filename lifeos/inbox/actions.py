"""Run one Inbox action at most once and claim completion only from the source's own read-back.

Gates in order: replay of the same request, policy, live-writes switch, record freshness, exact authorization, then
ONE atomic claim in the ledger, execute, read-back. Executor and source reader are injected. Outcome.status: completed
(read-back proves it), uncertain (the executor raised or the read-back proves nothing: never repeated), not_applied
(record unchanged), in_progress, expired, refused (a gate said no, nothing ran). `reconcile` settles an uncertain
action from the source's state, else leaves it flagged for a person. Logs carry ids and reason words only.
"""

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace

from lifeos.inbox import policy
from lifeos.inbox.ledger import digest

LOG = logging.getLogger("lifeos.inbox")
# older than this: a dead worker's claim or execution; `recover_stale` settles it
LEASE = timedelta(minutes=10)
STATUS_OF_STATE = {
    "completed": "completed",
    "uncertain": "uncertain",
    "not_applied": "not_applied",
    "claimed": "in_progress",
    "in_progress": "in_progress",
    "expired": "expired",
}


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    replayed: bool = False


class ActionService:
    def __init__(self, key, executor, ledger, audit=None, synthetic=False):
        self._key, self._executor, self._ledger, self._synthetic = key, executor, ledger, synthetic
        self.audit = audit if audit is not None else []

    def _note(self, principal, record, action, outcome, request_id):
        line = {
            "principal": principal.principal_id,
            "record": record.record_id,
            "action": action,
            "status": outcome.status,
            "reason": outcome.reason,
            "request": request_id[:12],
        }
        self.audit.append(line)
        LOG.info(json.dumps(line, sort_keys=True))
        return outcome

    def request(self, principal, record, action, auth, idempotency_key, now):
        request_id = digest(idempotency_key)
        prior = self._ledger.action(request_id)
        if prior:
            if (prior["principal_id"], prior["record_id"], prior["action"]) != (
                principal.principal_id,
                record.record_id,
                action,
            ):
                return self._note(principal, record, action, Outcome("refused", "idempotency_key_reused"), request_id)
            return Outcome(STATUS_OF_STATE[prior["state"]], prior["reason"], replayed=True)
        return self._note(
            principal, record, action, self._run(principal, record, action, auth, request_id, now), request_id
        )

    def _run(self, principal, record, action, auth, request_id, now):
        allowed, reason = policy.decide(principal, record, action, now)
        if not allowed:
            return Outcome("refused", reason)
        if action in policy.READ_ACTIONS:
            return Outcome("completed", "read_only")
        if not self._synthetic and not policy.LIVE_WRITES_ENABLED:
            return Outcome("refused", "live_execution_disabled")
        if record.freshness(now) != "fresh":
            return Outcome("refused", "record_stale")
        reason = policy.verify(self._key, auth, principal, record, action, now)
        if reason:
            return Outcome("refused", reason)
        fields = {
            "request_id": request_id,
            "principal_id": principal.principal_id,
            "source": record.source,
            "workspace": record.workspace,
            "account": record.account,
            "record_id": record.record_id,
            "action": action,
            "revision": record.revision,
            "status_before": record.source_status,
            "nonce": auth.nonce,
        }
        claim, detail = self._ledger.claim_action(fields, now)
        if claim == "refused":
            return Outcome("refused", detail)
        if claim == "replay":
            return Outcome(STATUS_OF_STATE[detail["state"]], detail["reason"], replayed=True)
        if not self._ledger.move(request_id, "claimed", "in_progress", "executing", now):
            return Outcome("refused", "claim_lost")
        try:
            readback = self._executor(record, action)
        except Exception:  # noqa: BLE001 - never repeated, never quoted: the source may have applied it
            self._ledger.move(request_id, "in_progress", "uncertain", "executor_outcome_unknown", now)
            return Outcome("uncertain", "executor_outcome_unknown")
        return self._settle(request_id, record, action, readback, "in_progress", now)

    def _settle(self, request_id, record, action, readback, before, now):
        """One verdict from one read-back: proven -> completed; unchanged -> not_applied (slot freed); anything else
        -> uncertain.
        """
        if policy.completion_proven(record, action, readback, self._synthetic):
            self._ledger.move(request_id, before, "completed", "read_back_verified", now)
            return Outcome("completed", "read_back_verified")
        unchanged = (
            isinstance(readback, dict)
            and readback.get("record_id") == record.record_id
            and readback.get("revision") == record.revision
            and readback.get("source_status") == record.source_status
        )
        if unchanged and self._synthetic:
            self._ledger.move(request_id, before, "not_applied", "source_unchanged", now, release=True)
            return Outcome("not_applied", "source_unchanged")
        self._ledger.move(request_id, before, "uncertain", "read_back_not_proven", now)
        return Outcome("uncertain", "read_back_not_proven")

    def reconcile(self, idempotency_key, read_source, now):
        """Settle an uncertain action from `read_source(record_id)`: completed only on proof, else still uncertain.
        Never executes.
        """
        request_id = digest(idempotency_key)
        row = self._ledger.action(request_id)
        if not row or row["state"] != "uncertain":
            return Outcome("refused", "not_reconcilable")
        claimed = SimpleNamespace(
            record_id=row["record_id"], revision=row["revision"], source_status=row["status_before"]
        )
        try:
            readback = read_source(row["record_id"])
        except Exception:  # noqa: BLE001 - an unreadable source settles nothing
            readback = None
        outcome = self._settle(request_id, claimed, row["action"], readback, "uncertain", now)
        if outcome.status == "uncertain":
            self._ledger.needs_human(request_id, now)
            outcome = Outcome("uncertain", "needs_human")
        return self._note_id(row, outcome, request_id)

    def _note_id(self, row, outcome, request_id):
        line = {
            "principal": row["principal_id"],
            "record": row["record_id"],
            "action": row["action"],
            "status": outcome.status,
            "reason": outcome.reason,
            "request": request_id[:12],
        }
        self.audit.append(line)
        LOG.info(json.dumps(line, sort_keys=True))
        return outcome

    def recover_stale(self, now):
        cutoff = now - LEASE
        expired = [
            r
            for r in self._ledger.stale("claimed", cutoff)
            if self._ledger.move(r, "claimed", "expired", "claim_never_started", now, release=True)
        ]
        uncertain = [
            r
            for r in self._ledger.stale("in_progress", cutoff)
            if self._ledger.move(r, "in_progress", "uncertain", "lease_expired_outcome_unknown", now)
        ]
        return len(expired), len(uncertain)
