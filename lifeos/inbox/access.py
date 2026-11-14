"""One attributed, receipted read of one source by one principal (LIFE-707).

It reuses the existing grant check (`policy.read_grant`) and the durable ledger; it adds no gateway, broker or credential store.

The order is the contract: decide from the grant, write the receipt (principal, source, workspace, account, decision, fixed reason), and only then read, with the grant's credential NAME. A refused request
therefore has a receipt and no read; if the receipt cannot be written, nothing is read. A repeated request id is a replay and never reads again, also after a restart. Receipts hold ids and fixed words only."""
from dataclasses import dataclass

from lifeos.inbox import policy


@dataclass(frozen=True)
class Access:
    decision: str                 # allow | deny
    reason: str                   # fixed word: ok | no_grant | grant_revoked | grant_expired | action_not_granted | workspace_not_released | unknown_scope | replayed
    rows: tuple = ()              # native records, only on an allowed first request
    replayed: bool = False


def read_source(ledger, principal, source, now, request_id):
    """`source`: an object with .source .workspace .account and .fetch(credential_ref). Never raises for a refused or failed read; a ledger failure raises LedgerError before any read."""
    grant, reason = policy.read_grant(principal, source.source, source.workspace, source.account, now)
    decision = "allow" if grant else "deny"
    fields = {"request_id": request_id, "principal_id": principal.principal_id, "source": source.source, "workspace": source.workspace, "account": source.account, "decision": decision,
              "reason": "ok" if grant else reason}
    if not ledger.record_access(fields, now):
        return Access(decision, "replayed", replayed=True)                                  # the same request id: the first answer stands, no second read
    if grant is None:
        return Access("deny", reason)
    try:
        rows = tuple(source.fetch(grant.credential_ref))
    except Exception as error:                                                              # noqa: BLE001 - a connector failure is recorded as its type only, never its message
        ledger.settle_access(request_id, type(error).__name__.lower()[:24])
        return Access("allow", "ok")
    ledger.settle_access(request_id, "ok")
    return Access("allow", "ok", rows)
