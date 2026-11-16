"""LIFE-721: one policy-controlled read that two client identities call for the same Jira source. Pure, no network, no store, no cache.

The caller never holds a credential: a grant names a credential (a NAME), and the injected `fetch` (the host broker side) is the only code that uses it. Order: validate, decide, write the receipt, then read; a receipt that cannot be written means no read.
Fail closed with fixed words. Synthetic until the live proof in docs/platform/shared-read-acceptance.md runs."""
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from lifeos.platform import access_policy

TOOLS = {"jira.get_issue": ("jira", "read")}                                  # the closed list; a name outside it, or any write, is refused
TENANTS = ("personal", "bytalos")                                            # TGP is excluded by design; anything else is refused
MAX_AGE = timedelta(seconds=300)
FIELDS = ("key", "summary", "status", "updated")                              # data minimization: nothing else leaves
SECRET = re.compile(r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,}|-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:ghp_|sk-|xox[abprs]-)[A-Za-z0-9_-]{10,}")


@dataclass(frozen=True)
class Session:
    client: str           # claude-code | codex (a label for the receipt; identity is the principal)
    principal: str        # 64 hex, the same key the access policy uses
    tenant: str
    trusted: bool         # set only by the host side after it authenticated the session


@dataclass(frozen=True)
class Grant:
    client: str
    principal: str
    tenant: str
    account: str
    tool: str
    credential_ref: str   # a NAME, never a value
    expires: datetime
    revoked: bool = False


@dataclass(frozen=True)
class Answer:
    decision: str         # allow | deny
    reason: str           # fixed word
    data: dict | None = None


def fingerprint(principal):
    return hashlib.sha256(str(principal).encode()).hexdigest()[:8]


def revoke(grants, client):
    """A new tuple with every grant of `client` revoked; other clients and sources are untouched."""
    return tuple(Grant(**{**g.__dict__, "revoked": True}) if g.client == client else g for g in grants)


def _receipt(session, tool, request_id, outcome, reason, now, source_request_id=None, age=None):
    return {"t": access_policy.audit_line(session.principal, "jira", tool, outcome, reason, now=now)["t"], "client": session.client, "principal_fp": fingerprint(session.principal),
            "tenant": session.tenant, "tool": tool, "request_id": request_id, "source_request_id": source_request_id, "outcome": outcome, "reason": reason, "age_s": age}


def read(session, tool, account, args, grants, policy, fetch, audit, now, request_id):
    """`fetch(credential_ref, tool, args)` -> {"data": dict, "request_id": str, "fetched_at": datetime}. `audit(receipt)` raises when it cannot write. Never raises for a refused or failed read."""
    def refuse(reason):
        _write(audit, _receipt(session, str(tool)[:40], request_id, "deny", reason, now))
        return Answer("deny", reason)
    if tool not in TOOLS:
        return refuse("unknown_tool")
    if TOOLS[tool][1] != "read":
        return refuse("write_refused")
    if session.tenant not in TENANTS:
        return refuse("tenant_excluded" if str(session.tenant).lower() == "tgp" else "unknown_tenant")
    if not session.trusted:
        return refuse("untrusted_session")
    decision, _ = access_policy.decide(policy, session.principal, TOOLS[tool][0], "read")
    if decision != "allow":
        return refuse("policy_denied")
    mine = [g for g in grants if g.principal == session.principal and g.client == session.client and g.tenant == session.tenant and g.account == account and g.tool == tool]
    if not mine:
        return refuse("no_grant")
    grant = mine[0]
    if grant.revoked:
        return refuse("grant_revoked")
    if grant.expires <= now:
        return refuse("grant_expired")
    _write(audit, _receipt(session, tool, request_id, "allow", "ok", now))                    # the receipt first: no receipt, no read
    try:
        got = fetch(grant.credential_ref, tool, dict(args))
        data, source_id, at = got["data"], got["request_id"], got["fetched_at"]
    except Exception:                                                                       # noqa: BLE001 - a connector failure is recorded by outcome only, never its message
        _write(audit, _receipt(session, tool, request_id, "error", "fetch_failed", now))
        return Answer("allow", "fetch_failed")
    age = now - at
    if age > MAX_AGE or age < -timedelta(seconds=60):
        _write(audit, _receipt(session, tool, request_id, "stale", "stale_response", now, source_id, int(age.total_seconds())))
        return Answer("allow", "stale_response")
    clean = {k: data[k] for k in FIELDS if isinstance(data, dict) and k in data}
    if SECRET.search(repr(clean)):
        _write(audit, _receipt(session, tool, request_id, "refused", "output_has_secret", now, source_id))
        return Answer("allow", "output_has_secret")
    _write(audit, _receipt(session, tool, request_id, "ok", "ok", now, source_id, int(age.total_seconds())))
    return Answer("allow", "ok", clean)


def _write(audit, receipt):
    audit(receipt)
