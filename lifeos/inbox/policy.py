"""The approved action matrix (Jim, Nov 13 2026), grants, and the exact authorization every protected action needs (LIFE-685).

Every answer is (allowed, reason) with a fixed lower-case reason word; fail closed. Three gates must all pass: the product matrix, the principal's grant for that exact source,
workspace, account and action (a grant can only narrow the matrix), and the record's own eligibility proven by its adapter. Concord approve and resume are two actions with two grants."""
import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta

from lifeos.inbox.model import UNRELEASED_WORKSPACES, WORKSPACES, SOURCES, InboxError

READ_ACTIONS = frozenset({"view", "open"})
MATRIX = {
    "jira": READ_ACTIONS | {"accept_uat", "reject_uat"},                                   # Human UAT only; never a general edit, assignment or transition
    "todoist": READ_ACTIONS | {"complete_task"},                                           # selected personal tasks only; no edit, reschedule, bulk or delete
    "concord": READ_ACTIONS | {"approve", "reject", "request_changes", "resume_work"},     # resume is its own action, never implied by an approval
    "followup": READ_ACTIONS | {"confirm_resolution", "update_checkpoint"},                # through the canonical Followup authority only
    "calendar": READ_ACTIONS,
    "asana": READ_ACTIONS,
}
EXPECTED_STATUS = {                                                                         # what the source's own read-back must show before anything is called done (synthetic until each source is verified live)
    "accept_uat": {"Done"}, "reject_uat": {"In Progress", "Agent Go"}, "complete_task": {"completed"}, "approve": {"approved"}, "reject": {"rejected"},
    "request_changes": {"changes_requested"}, "resume_work": {"running"}, "confirm_resolution": {"resolved"}, "update_checkpoint": {"open", "waiting"},
}
AUTH_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class Grant:
    """One scoped access: exactly one source, workspace and account, the actions allowed, and the NAME of the credential (never its value). Grants are never merged across workspaces."""
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
    """-> (grant, None) or (None, reason): no_grant | grant_revoked | grant_expired | action_not_granted."""
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
    return None if record.kind == "checkpoint" else "not_a_checkpoint"                      # confirm_resolution, update_checkpoint


def decide(principal, record, action, now):
    """-> (allowed, reason). `ok` when all three gates pass. This is the only place the matrix, the grant and the record's eligibility meet."""
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
    """The actions this principal could request on this record right now, for display; the exact-authorization and freshness gates still apply at request time."""
    return tuple(a for a in sorted(MATRIX[record.source]) if decide(principal, record, a, now)[0])


def read_grant(principal, source, workspace, account, now):
    """-> (grant, None) or (None, reason) for reading one source in one workspace. TGP is refused here, before any credential is looked up."""
    if workspace in UNRELEASED_WORKSPACES:
        return None, "workspace_not_released"
    if workspace not in WORKSPACES:
        return None, "unknown_scope"
    return find_grant(principal, source, workspace, account, "view", now)


@dataclass(frozen=True)
class ExactAuthorization:
    """Permission for ONE protected action on ONE record revision by ONE principal, single-use and short-lived. Minted server-side only after a human confirmed it; signed so it cannot be forged
    or edited. It authorizes the request; the grant still decides whether the underlying source may be touched."""
    principal_id: str
    record_id: str
    action: str
    revision: str
    nonce: str
    expires_at: object
    mac: str


def _mac(key, parts):
    return hmac.new(key, "\x1f".join(str(p) for p in parts).encode(), hashlib.sha256).hexdigest()


def sign(key, principal_id, record_id, action, revision, nonce, expires_at):
    return ExactAuthorization(principal_id, record_id, action, revision, nonce, expires_at, _mac(key, (principal_id, record_id, action, revision, nonce, expires_at.isoformat())))


def verify(key, auth, principal, record, action, now):
    """-> None when the authorization is for exactly this principal, record revision and action and is unexpired and untampered; else a fixed reason."""
    if not isinstance(auth, ExactAuthorization):
        return "authorization_missing"
    expect = _mac(key, (auth.principal_id, auth.record_id, auth.action, auth.revision, auth.nonce, auth.expires_at.isoformat()))
    if not hmac.compare_digest(expect, auth.mac):
        return "authorization_forged"
    if (auth.principal_id, auth.record_id, auth.action, auth.revision) != (principal.principal_id, record.record_id, action, record.revision):
        return "authorization_mismatch"
    return "authorization_expired" if auth.expires_at <= now else None


def mint(key, principal, record, action, nonce, now, ttl=AUTH_TTL):
    """Server-side only. Raises InboxError when the request could never be allowed, so no authorization exists for a forbidden action."""
    allowed, reason = decide(principal, record, action, now)
    if not allowed or action in READ_ACTIONS:
        raise InboxError(reason if not allowed else "action_not_protected")
    return sign(key, principal.principal_id, record.record_id, action, record.revision, nonce, now + ttl)
