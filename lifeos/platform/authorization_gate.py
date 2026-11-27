"""The central authorization gate in front of one side effect. Pure functions: no network, no store, no credential.

It joins capability_catalog.resolve, access_policy.decide, a tenant grant (principal, tenant, account, tool, purpose,
actions, expiry, revoke) and a human approval for protected actions, in that order, then writes a redacted receipt and
only then runs the effect. Every deny happens before `effect` and writes its receipt first; if the receipt cannot be
written there is no effect. A grant names a credential by NAME; the gate never reads one. Simulated enforcement only:
`live_proven` is always False and every receipt says mode=synthetic.
"""
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from lifeos.platform import access_policy, capability_catalog

TENANTS = ("personal", "bytalos", "tgp")
ACTIONS = ("read", "write", "send", "delete", "permission", "production")
PROTECTED = ("send", "delete", "permission", "production")  # a human approves each one; the agent never approves itself
KIND = {"read": "read", "write": "write", "send": "write", "permission": "write", "production": "write", "delete": "delete"}
APPROVAL_TTL = timedelta(minutes=15)
SKEW = timedelta(seconds=60)
NAME = re.compile(r"[a-z][a-z0-9_.:-]{1,63}")
SECRET = re.compile(r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,}|-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:ghp_|sk-|xox[abprs]-)[A-Za-z0-9_-]{10,}")


@dataclass(frozen=True)
class Session:
    principal: str
    policy_key: str
    tenant: str
    trusted: bool


@dataclass(frozen=True)
class Grant:
    id: str
    principal: str
    tenant: str
    account: str
    tool: str
    purpose: str
    actions: tuple
    credential_ref: str  # a NAME, never a value
    expires: datetime
    revoked: bool = False


@dataclass(frozen=True)
class Approval:
    principal: str
    tenant: str
    tool: str
    action: str
    request_id: str
    approver: str  # a human id the host knows; never the requesting agent
    approved_at: datetime


@dataclass(frozen=True)
class Request:
    tool: str
    action: str
    account: str
    purpose: str
    service: str
    request_id: str


@dataclass(frozen=True)
class Outcome:
    decision: str
    reason: str
    executed: bool = False
    result: object = None
    live_proven: bool = False


def fingerprint(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:8]


def revoke(grants, grant_id=None, principal=None):
    return tuple(Grant(**{**g.__dict__, "revoked": True}) if (grant_id is not None and g.id == grant_id) or (principal is not None and g.principal == principal) else g for g in grants)


def _clean(value, size):
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value))[:size]


def _receipt(session, request, outcome, reason, now, grant=None, approval=None, synthetic=True):
    return {"t": access_policy.audit_line(session.policy_key, request.service, request.action, outcome, reason, now=now)["t"], "mode": "synthetic" if synthetic else "live",
            "principal_fp": fingerprint(session.principal), "tenant": _clean(session.tenant, 16), "tool": _clean(request.tool, 40), "action": _clean(request.action, 16),
            "purpose": _clean(request.purpose, 40), "request_id": _clean(request.request_id, 40), "grant_fp": fingerprint(grant.id) if grant else None,
            "approver_fp": fingerprint(approval.approver) if approval else None, "outcome": outcome, "reason": reason}


def _aware(*values):
    return all(isinstance(v, datetime) and v.tzinfo is not None for v in values)


def _shape_ok(session, request, now):
    return (isinstance(session, Session) and isinstance(request, Request) and _aware(now)
            and all(isinstance(v, str) and v for v in (session.principal, session.policy_key, request.tool, request.account, request.purpose, request.service, request.request_id)))


def execute(session, request, catalog, policy, grants, approvals, human_approvers, effect, audit, now, excluded_tenants=(), pregrant_tools=()):
    """-> Outcome. `effect()` runs only after every check passed and the allow receipt was written. `audit(receipt)`
    raises when it cannot write. `human_approvers` is the host's set of approver ids. `excluded_tenants` lets a caller
    refuse a tenant outright.
    """
    if not _shape_ok(session, request, now):
        raise ValueError("bad_request")

    def refuse(reason, grant=None, approval=None):
        audit(_receipt(session, request, "deny", reason, now, grant, approval))
        return Outcome("deny", reason)
    if request.action not in ACTIONS:
        return refuse("unknown_action")
    if session.tenant not in TENANTS:
        return refuse("unknown_tenant")
    if session.tenant in excluded_tenants:
        return refuse("tenant_excluded")
    if not session.trusted:
        return refuse("untrusted_session")
    # A known operation can require a grant before catalog resolution: an auditable no_grant refusal with no provider
    # call.
    if request.tool in pregrant_tools and not any(g.principal == session.principal and g.tool == request.tool and g.account == request.account for g in grants):
        return refuse("no_grant")
    found = capability_catalog.resolve(catalog, session.principal, request.tool, request.action, session.tenant, now)
    if not found["ok"]:
        return refuse("catalog_" + found["code"].removeprefix("catalog_"))
    if found["tenant"] is not None and found["tenant"] != session.tenant:
        return refuse("tenant_mismatch")
    decision, _ = access_policy.decide(policy, session.policy_key, request.service, KIND[request.action])
    if decision == "deny":
        return refuse("policy_denied")
    mine = [g for g in grants if g.principal == session.principal and g.tool == request.tool and g.account == request.account]
    if not mine:
        return refuse("no_grant")
    same = [g for g in mine if g.tenant == session.tenant]
    if not same:
        return refuse("tenant_mismatch")
    live = [g for g in same if not g.revoked]
    if not live:
        return refuse("grant_revoked", same[0])
    fresh = [g for g in live if _aware(g.expires) and g.expires > now]
    if not fresh:
        return refuse("grant_expired", live[0])
    on_purpose = [g for g in fresh if g.purpose == request.purpose]
    if not on_purpose:
        return refuse("purpose_mismatch", fresh[0])
    grant = next((g for g in on_purpose if request.action in g.actions), None)
    if grant is None:
        return refuse("action_not_granted", on_purpose[0])
    approval = None
    if request.action in PROTECTED:
        match = [a for a in approvals if (a.principal, a.tenant, a.tool, a.action, a.request_id) == (session.principal, session.tenant, request.tool, request.action, request.request_id)]
        if not match:
            return refuse("approval_missing", grant)
        approval = match[0]
        if approval.approver not in human_approvers or approval.approver == session.principal:
            return refuse("approval_not_human", grant, approval)
        if not _aware(approval.approved_at) or now - approval.approved_at > APPROVAL_TTL or approval.approved_at - now > SKEW:
            return refuse("approval_stale", grant, approval)
    elif decision == "pending":
        return refuse("approval_missing", grant)  # the policy wants an approval this action's class does not carry: refuse, never invent one
    audit(_receipt(session, request, "allow", "ok", now, grant, approval, found["synthetic"]))  # no receipt, no effect
    try:
        result = effect()
    except Exception:                                                                       # noqa: BLE001 - a provider failure is recorded by outcome only, never its message
        audit(_receipt(session, request, "error", "effect_failed", now, grant, approval, found["synthetic"]))
        return Outcome("allow", "effect_failed", True)
    if SECRET.search(repr(result)):
        audit(_receipt(session, request, "refused", "output_has_secret", now, grant, approval, found["synthetic"]))
        return Outcome("allow", "output_has_secret", True)
    audit(_receipt(session, request, "ok", "ok", now, grant, approval, found["synthetic"]))
    return Outcome("allow", "ok", True, result)
