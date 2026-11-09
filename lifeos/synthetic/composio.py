"""A simulation of Composio "bounded hybrid custody" (LIFE-380, LOS-86 T09). Fake data only: no Composio project, no API key, no spend, no network, no real account.

The picture being tested (KCAP-Q67, still a plan): the fake vendor holds the tokens of newly authorized connected accounts; V7 decides who may do what with which account, through the
merged gate `authz.dispatch`. Routine upkeep (refresh, health, account state, revoke or disable) is done by an approved Oracle or GitHub admin principal with no human login, and Buzz
only carries scoped status and approval requests. An expired or revoked grant needs the OWNER to re-consent at the provider; this module denies provider execution until then and
never tries to get around it. The vendor's admin key and tokens never leave the fake vendor: not into a Buzz notice, a receipt or a result.

`CUSTODY_EXCEPTION` is the record for the architecture review: the exception stated exactly, what stays unchanged, what is unverified, how to roll back, and what still needs a
separate approval. Planning consent is NOT live provider authorization."""
import hashlib
import json
from datetime import timedelta

from lifeos.synthetic import authz
from lifeos.synthetic.authz_fixture import NOW, AuthzWorld, TENANT_A, TENANT_B, ref_hash

PROJECT = "proj-fake-1"
ADMIN_KEY = "fake-composio-admin-key-not-real"                      # invented; the fake vendor's own key, used only inside _vendor_call
MANAGER_KEYS = {"oracle-admin": "fake-manager-key-oracle", "github-admin": "fake-manager-key-github"}       # invented signing keys of the approved admin principals
ALLOWED_ACTIONS = ("health", "refresh", "disable")                   # the routine management actions; anything else is out of scope
NEEDS_CONSENT = ("EXPIRED", "REVOKED")
REFRESH_WINDOW = timedelta(minutes=15)
BUZZ_FIELDS = ("kind", "counts", "account", "state", "needs", "note")          # a Buzz notice may carry only these: status and approval requests
READ_TOOL = {"gmail": "gmail_read_message", "drive": "drive_read_file"}
WHO = {"agent-a": {"session": "sess-a", "task": "task-a"}, "agent-b": {"session": "sess-b", "task": "task-b"}}

CUSTODY_EXCEPTION = {
    "exception": "Composio-cloud custody of the tokens of newly authorized, Composio-managed connected accounts only (KCAP-Q67).",
    "scope": "future_managed_accounts_only",
    "v7_keeps": ["principal", "tenant", "account", "action", "grant", "approval decisions"],
    "unchanged_paths": ["existing V7 and Bitwarden direct credentials", "native ChatGPT connections"],
    "vendor_scope_and_cost": "unverified",
    "routine_login_free_operation": "simulated_only, not proven with the real vendor",
    "rollback": ["disable every managed connection (revoke its grants)", "revoke the connections at the provider",
                 "the accounts are still on their existing direct paths, none of which was migrated"],
    "protected_approval_prerequisites": ["a distinct Jim approval for any real Composio project or API key", "a distinct Jim approval for any spend",
                                         "a distinct Jim approval for the first real provider consent"],
    "planning_consent_is_live_authorization": False,
    "managed_oauth_rollout": "blocked",
}


class ComposioWorld(AuthzWorld):
    """The authorization world plus a fake vendor: connections, a private vault, a Buzz outbox and counters for what must stay at zero."""

    def __init__(self):
        super().__init__()
        self.project = PROJECT
        self.connections = {"ca-a1": {"account": "acct-a-gmail", "tenant": TENANT_A, "state": "ACTIVE", "expires": NOW + timedelta(minutes=5), "refreshes": 0},
                            "ca-a2": {"account": "acct-a-drive", "tenant": TENANT_A, "state": "ACTIVE", "expires": NOW + timedelta(hours=6), "refreshes": 0},
                            "ca-b1": {"account": "acct-b-gmail", "tenant": TENANT_B, "state": "EXPIRED", "expires": NOW - timedelta(days=1), "refreshes": 0},
                            "ca-b2": {"account": "acct-b-drive", "tenant": TENANT_B, "state": "REVOKED", "expires": NOW - timedelta(days=2), "refreshes": 0}}
        self._vault = {cid: f"fake-vendor-token-{cid}" for cid in self.connections}          # the vendor's; nothing outside this class reads it
        self.by_account = {c["account"]: cid for cid, c in self.connections.items()}         # the exact mapping: no default, no latest
        self.provider_calls = self.admin_calls = self.human_logins = 0
        self.buzz_outbox, self.manager_nonces, self.notified, self.vendor_log = [], set(), set(), []


def sign(key, body):
    """A keyed digest with invented keys standing in for a Buzz command signature. Not a real signature."""
    return hashlib.sha256(f"fake-command|{key}|{json.dumps(body, sort_keys=True)}".encode()).hexdigest()


def command(world, sender, account, action, nonce, project=None, tenant=None, key=None):
    """A fake signed management command as it would arrive over Buzz."""
    acct = world.accounts.get(account)
    body = {"sender": sender, "project": project or world.project, "tenant": tenant or (acct[0] if acct else TENANT_A), "account": account, "action": action, "nonce": nonce}
    return {**body, "sig": sign(key or MANAGER_KEYS.get(sender, "no-such-key"), body)}


def _notice(world, **fields):
    if not set(fields) <= set(BUZZ_FIELDS):
        raise ValueError("a Buzz notice carries status and approval requests only")
    world.buzz_outbox.append(fields)


def _vendor_call(world, op, cid):
    """The only place the vendor's admin key is used. It records a fingerprint, never the key, and returns only ok or not."""
    world.admin_calls += 1
    world.vendor_log.append((op, cid, ref_hash(ADMIN_KEY)))
    return world.connections[cid]["state"] == "ACTIVE"                   # a dead refresh token and a disabled connection both refuse


def _try_refresh(world, cid, now):
    if not _vendor_call(world, "refresh", cid):
        return False                                                  # the refresh token is dead: only the owner can fix this
    c = world.connections[cid]
    c["expires"], c["refreshes"] = now + timedelta(hours=1), c["refreshes"] + 1
    return True


def _approval_request(world, account, state):
    if account not in world.notified:                                 # one request per account, not one per run
        world.notified.add(account)
        _notice(world, kind="approval_request", account=account, state=state, needs="owner_reconsent",
                note="Routine recovery failed; the owner must re-consent at the provider.")


def execute(world, who, account_id, nonce="n1", source_id=None, now=NOW):
    """-> (receipt, result). Through the merged gate. The connected account is chosen only by the exact account id; a missing one, an alias or an account with no connection selects nothing.
    A grant that needs the owner's re-consent is denied here before any provider call."""
    cid = world.by_account.get(account_id) if isinstance(account_id, str) else None
    acct = world.accounts.get(account_id) if isinstance(account_id, str) else None
    conn = world.connections.get(cid)
    # the gate reads the owner-consent flag, so it is raised for the gate only, and a call that does not end in that denial leaves the ledger as it found it
    flagged = bool(conn and conn["state"] in NEEDS_CONSENT and account_id not in world.consent_needed)
    ledger = world.ledger_hash()
    if flagged:
        world.consent_needed.add(account_id)
    ctx, agent = WHO.get(who, {"session": "no-session", "task": "no-task"}), world.agents.get(who)
    src = world.sources.get(source_id) if source_id else None
    # the tool follows what is being read: a document needs the drive tool, a message the gmail tool, anything else has no composio read tool at all
    tool = (READ_TOOL.get(src["kind"], "no_such_tool") if src else "no_such_tool") if source_id else READ_TOOL.get(acct[1] if acct else None, "gmail_read_message")
    req = {"case": "composio", "tenant": agent["tenant"] if agent else "no-tenant", "agent_id": who, "host_id": "fake-mac", "session_id": ctx["session"], "task_id": ctx["task"],
           "tool": tool, "nonce": nonce, "channel": "v7_adapter", "object_id": source_id or "obj"}
    if cid is not None:
        req.update(account_id=account_id, grant_id=f"g-{account_id}-read")
    if source_id:
        req.update(source_id=source_id, source_revision=src["revision"] if src else 0)
    receipt, results = authz.dispatch(world, req, now)
    if flagged:
        world.consent_needed.discard(account_id)
        receipt["policy_ledger_hash_before"] = receipt["policy_ledger_hash_after"] = ledger      # a refusal changes nothing, and the receipt says so truthfully
    if receipt["decision_code"] != "ALLOW":
        return receipt, None
    world.provider_calls += 1
    return receipt, {"connected_account_id": cid, "account": account_id, "provider": acct[1], "text": results[0]["text"]}


def maintain(world, runner, now=NOW):
    """The routine upkeep run by an approved Oracle or GitHub admin principal: refresh what is about to expire, report what needs the owner. No human login, ever."""
    if runner not in MANAGER_KEYS:
        return {"outcome": "refused", "code": "DENY_MANAGEMENT_SCOPE"}
    counts = {"refreshed": 0, "healthy": 0, "needs_reconsent": 0, "disabled": 0}
    for cid in sorted(world.connections):
        c = world.connections[cid]
        if c["state"] in NEEDS_CONSENT:
            _try_refresh(world, cid, now)                             # unattended recovery is tried, and fails
            world.consent_needed.add(c["account"])
            _approval_request(world, c["account"], c["state"])
            counts["needs_reconsent"] += 1
        elif c["state"] == "DISABLED":
            counts["disabled"] += 1
        elif c["state"] == "ACTIVE" and c["expires"] - now <= REFRESH_WINDOW and _try_refresh(world, cid, now):
            counts["refreshed"] += 1
        else:
            counts["healthy"] += 1
    _notice(world, kind="status", counts=dict(counts))
    return {"outcome": "done", "code": None, "runner": runner, "counts": counts, "human_logins": world.human_logins}


def owner_reconsent(world, cid, now=NOW):
    """The ONE step only a person can do: the owner signs in at the provider and consents again. This function stands for that human act; nothing automatic calls it."""
    c = world.connections[cid]
    c["state"], c["expires"] = "ACTIVE", now + timedelta(hours=1)
    world.consent_needed.discard(c["account"])
    world.notified.discard(c["account"])
    world.human_logins += 1


def manage(world, cmd, now=NOW):
    """The management interface behind Buzz: one signed command in, one scoped result out. Nothing a refusal returns, and nothing it posts, holds a key or a token."""
    fields = ("sender", "project", "tenant", "account", "action", "nonce", "sig")
    if not isinstance(cmd, dict) or any(not (isinstance(cmd.get(k), str) and cmd[k]) for k in fields):
        return {"outcome": "refused", "code": "DENY_INPUT"}
    key = MANAGER_KEYS.get(cmd["sender"])
    if key is None or sign(key, {k: v for k, v in cmd.items() if k != "sig"}) != cmd["sig"]:
        return {"outcome": "refused", "code": "DENY_PRINCIPAL"}      # a rogue agent, or a command that is forged or was changed
    if (cmd["sender"], cmd["nonce"]) in world.manager_nonces:
        return {"outcome": "refused", "code": "DENY_REPLAY_OR_WRITER"}
    if cmd["action"] not in ALLOWED_ACTIONS:
        return {"outcome": "refused", "code": "DENY_MANAGEMENT_SCOPE"}
    acct = world.accounts.get(cmd["account"])
    if cmd["project"] != world.project:
        return {"outcome": "refused", "code": "DENY_TENANT"}
    if acct is None or cmd["account"] not in world.by_account:
        return {"outcome": "refused", "code": "DENY_ACCOUNT_AMBIGUOUS"}
    if acct[0] != cmd["tenant"]:
        return {"outcome": "refused", "code": "DENY_TENANT"}
    cid = world.by_account[cmd["account"]]
    c = world.connections[cid]
    if cmd["action"] == "refresh" and c["state"] == "DISABLED":
        return {"outcome": "refused", "code": "DENY_GRANT"}               # a disabled connection is not refreshed or revived by upkeep
    world.manager_nonces.add((cmd["sender"], cmd["nonce"]))
    result = {"outcome": "done", "code": None, "action": cmd["action"], "account": cmd["account"]}
    if cmd["action"] == "refresh":
        result["refreshed"] = _try_refresh(world, cid, now)
        if not result["refreshed"] and c["state"] in NEEDS_CONSENT:
            _approval_request(world, cmd["account"], c["state"])
    elif cmd["action"] == "disable":
        _vendor_call(world, "disable", cid)
        for level in ("read", "write"):
            world.revoke_grant(f"g-{cmd['account']}-{level}")
        c["state"] = "DISABLED"
    else:
        _vendor_call(world, "health", cid)
    result["state"] = c["state"]
    _notice(world, kind="status", account=cmd["account"], state=c["state"])
    return result
