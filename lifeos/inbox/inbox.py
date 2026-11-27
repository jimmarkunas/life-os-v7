"""Build the one combined Inbox from independent sources.

Each (source, workspace, account) is read on its own with only its own grant's credential name and fails on its own,
so one broken connector never hides the others. No credential, grant, memory scope or source identity is ever merged.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from lifeos.inbox import adapters, policy
from lifeos.inbox.model import PRESENTATION, WORKSPACES, InboxError

_ORDER = {name: n for n, name in enumerate(PRESENTATION)}
_FAR = datetime(9999, 12, 31, tzinfo=timezone.utc)
FILTERS = ("all", *WORKSPACES)


@dataclass(frozen=True)
class SourceHealth:
    source: str
    workspace: str
    account: str
    state: str
    reason: str
    rejected: int = 0


@dataclass(frozen=True)
class Inbox:
    records: tuple
    health: tuple


def build_inbox(sources, principal, now):
    """`sources` have .source .workspace .account .identity and .fetch(credential_ref) -> native dicts (injected; a
    fake in tests). Returns Inbox(records, health), deterministic.
    """
    records, health = [], []
    for src in sorted(sources, key=lambda s: (s.workspace, s.source, s.account)):
        grant, reason = policy.read_grant(principal, src.source, src.workspace, src.account, now)
        if grant is None:  # fail closed: nothing is fetched without a live grant, and TGP never reaches a fetch
            state = "refused" if reason in ("workspace_not_released", "unknown_scope") else "no_grant"
            health.append(SourceHealth(src.source, src.workspace, src.account, state, reason))
            continue
        try:
            raws = list(src.fetch(grant.credential_ref))
        except Exception as error:  # noqa: BLE001 - a connector failure is shown as its type (or an InboxError's fixed reason word), never its message
            health.append(
                SourceHealth(
                    src.source,
                    src.workspace,
                    src.account,
                    "unavailable",
                    error.reason if isinstance(error, InboxError) else type(error).__name__.lower(),
                )
            )
            continue
        kept, rejected = [], 0
        for raw in raws:
            try:
                kept.append(adapters.normalize(src.source, src.workspace, src.account, raw, src.identity, now, now))
            except (InboxError, KeyError, ValueError, TypeError):
                rejected += 1
        records.extend(kept)
        health.append(
            SourceHealth(
                src.source,
                src.workspace,
                src.account,
                "degraded" if rejected else "ok",
                "records_rejected" if rejected else "ok",
                rejected,
            )
        )
    records.sort(key=lambda r: (_ORDER[r.presentation], r.due or _FAR, r.workspace, r.source, r.record_id))
    return Inbox(tuple(records), tuple(health))


def view(inbox, workspace="all"):
    if workspace not in FILTERS:
        raise InboxError("filter_unknown")
    return tuple(r for r in inbox.records if workspace == "all" or r.workspace == workspace)
