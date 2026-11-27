"""One attributed, receipted read of one source by one principal.

Order is the contract: decide from the grant (`policy.read_grant`), write the receipt, and only then read, using the
grant's credential NAME. A refused request has a receipt and no read; if the receipt cannot be written, nothing is
read. A repeated request id is a replay and never reads again, even after a restart. Receipts hold ids and fixed words
only.
"""

from dataclasses import dataclass

from lifeos.inbox import policy


@dataclass(frozen=True)
class Access:
    decision: str
    # Fixed word: ok | no_grant | grant_revoked | grant_expired | action_not_granted | workspace_not_released |
    # unknown_scope | replayed.
    reason: str
    rows: tuple = ()
    replayed: bool = False


def read_source(ledger, principal, source, now, request_id):
    """`source` has .source .workspace .account and .fetch(credential_ref). Never raises for a refused or failed read;
    a ledger failure raises LedgerError before any read.
    """
    grant, reason = policy.read_grant(principal, source.source, source.workspace, source.account, now)
    decision = "allow" if grant else "deny"
    fields = {
        "request_id": request_id,
        "principal_id": principal.principal_id,
        "source": source.source,
        "workspace": source.workspace,
        "account": source.account,
        "decision": decision,
        "reason": "ok" if grant else reason,
    }
    if not ledger.record_access(fields, now):
        return Access(decision, "replayed", replayed=True)
    if grant is None:
        return Access("deny", reason)
    try:
        rows = tuple(source.fetch(grant.credential_ref))
    except Exception as error:  # noqa: BLE001 - a connector failure is recorded as its type only, never its message
        ledger.settle_access(request_id, type(error).__name__.lower()[:24])
        return Access("allow", "ok")
    ledger.settle_access(request_id, "ok")
    return Access("allow", "ok", rows)
