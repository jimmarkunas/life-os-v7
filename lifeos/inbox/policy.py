"""The action matrix, grants, and the exact authorization every protected action needs.

Answers are (allowed, reason) with a fixed lower-case reason word; fail closed. Three gates must all pass: the matrix,
the principal's grant for that exact source, workspace, account and action (a grant only narrows the matrix), and the
record's eligibility proven by its adapter.
"""
import hashlib
import hmac
from dataclasses import dataclass, replace
from datetime import timedelta

from lifeos.inbox.model import UNRELEASED_WORKSPACES, WORKSPACES, SOURCES, InboxError

READ_ACTIONS = frozenset({"view", "open"})
MATRIX = {
    "jira": READ_ACTIONS | {"accept_uat", "reject_uat"},
    "todoist": READ_ACTIONS | {"complete_task"},
    "concord": READ_ACTIONS | {"approve", "reject", "request_changes", "resume_work"},
    "followup": READ_ACTIONS | {"confirm_resolution", "update_checkpoint"},
    "calendar": READ_ACTIONS,
    "asana": READ_ACTIONS,
}
# What the source's read-back must show before anything is called done (synthetic until verified)
EXPECTED_STATUS = {
    "accept_uat": {"Done"}, "reject_uat": {"In Progress", "Agent Go"}, "complete_task": {"completed"}, "approve": {"approved"}, "reject": {"rejected"},
    "request_changes": {"changes_requested"}, "resume_work": {"running"}, "confirm_resolution": {"resolved"}, "update_checkpoint": {"open", "waiting"},
}
SOURCE_OF_ACTION = {action: source for source, actions in MATRIX.items() for action in actions - READ_ACTIONS}
# Sources whose EXPECTED_STATUS matches the real workflow; empty: no live completion can be claimed
VERIFIED_COMPLETION = frozenset()
LIVE_WRITES_ENABLED = False
AUTH_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class Grant:
    """One source, workspace and account, the actions allowed, and the credential NAME (never its value)."""
    source: str
    workspace: str
    account: str
    actions: frozenset
    credential_ref: str
    expires_at: object
    revoked: bool = False


@dataclass(frozen=True)
class Principal:
    principal_id: str
    grants: tuple = ()


def find_grant(principal, source, workspace, account, action, now):
    seen = None
    for grant in principal.grants:
        if (grant.source, grant.workspace, grant.account) != (source, workspace, account):
            continue
        seen = grant
        if grant.revoked:
            return None, "grant_revoked"
        if grant.expires_at <= now:
            return None, "grant_expired"
        if action in grant.actions:
            return grant, None
    return None, "action_not_granted" if seen else "no_grant"


def _eligible(record, action):
    if action in READ_ACTIONS:
        return None
    if action in ("accept_uat", "reject_uat"):
        return None if record.source == "jira" and "human_uat" in record.flags else "not_human_uat"
    if action == "complete_task":
        return None if record.workspace == "personal" and "personal_task" in record.flags else "not_personal_task"
    if action in ("approve", "reject", "request_changes"):
        return None if record.kind == "approval_request" and record.approval_required else "no_pending_approval"
    if action == "resume_work":
        return None if "resumable" in record.flags else "not_resumable"
    return None if record.kind == "checkpoint" else "not_a_checkpoint"


def decide(principal, record, action, now):
    if record.workspace in UNRELEASED_WORKSPACES:
        return False, "workspace_not_released"
    if record.workspace not in WORKSPACES or record.source not in SOURCES:
        return False, "unknown_scope"
    if action not in MATRIX[record.source]:
        return False, "action_not_permitted"
    grant, reason = find_grant(principal, record.source, record.workspace, record.account, action, now)
    if grant is None:
        return False, reason
    reason = _eligible(record, action)
    return (False, reason) if reason else (True, "ok")


def permitted_actions(principal, record, now):
    return tuple(a for a in sorted(MATRIX[record.source]) if decide(principal, record, a, now)[0])


def read_grant(principal, source, workspace, account, now):
    if workspace in UNRELEASED_WORKSPACES:
        return None, "workspace_not_released"
    if workspace not in WORKSPACES:
        return None, "unknown_scope"
    return find_grant(principal, source, workspace, account, "view", now)


@dataclass(frozen=True)
class ExactAuthorization:
    """ONE protected action on ONE record revision by ONE principal: single-use, short-lived, signed, minted after
    human confirmation.
    """
    principal_id: str
    record_id: str
    action: str
    revision: str
    nonce: str
    expires_at: object
    mac: str
    source: str = ""
    workspace: str = ""
    account: str = ""


def _mac(key, parts):
    return hmac.new(key, "\x1f".join(str(p) for p in parts).encode(), hashlib.sha256).hexdigest()


def _parts(auth):
    return (auth.principal_id, auth.source, auth.workspace, auth.account, auth.record_id, auth.action, auth.revision, auth.nonce, auth.expires_at.isoformat())


def sign(key, principal_id, record, action, nonce, expires_at):
    draft = ExactAuthorization(principal_id, record.record_id, action, record.revision, nonce, expires_at, "", record.source, record.workspace, record.account)
    return replace(draft, mac=_mac(key, _parts(draft)))


def verify(key, auth, principal, record, action, now):
    if not isinstance(auth, ExactAuthorization):
        return "authorization_missing"
    if not hmac.compare_digest(_mac(key, _parts(auth)), auth.mac):
        return "authorization_forged"
    bound = (auth.principal_id, auth.source, auth.workspace, auth.account, auth.record_id, auth.action, auth.revision)
    if bound != (principal.principal_id, record.source, record.workspace, record.account, record.record_id, action, record.revision):
        return "authorization_mismatch"
    return "authorization_expired" if auth.expires_at <= now else None


def completion_proven(record, action, readback, synthetic):
    """True only when the read-back is for this record on a NEW revision with the status this action must end in."""
    if not synthetic and SOURCE_OF_ACTION.get(action) not in VERIFIED_COMPLETION:
        return False
    return (isinstance(readback, dict) and readback.get("record_id") == record.record_id and readback.get("revision") not in (None, record.revision)
            and readback.get("source_status") in EXPECTED_STATUS.get(action, ()))


def mint(key, principal, record, action, nonce, now, ttl=AUTH_TTL):
    allowed, reason = decide(principal, record, action, now)
    if not allowed or action in READ_ACTIONS:
        raise InboxError(reason if not allowed else "action_not_protected")
    return sign(key, principal.principal_id, record, action, nonce, now + ttl)
