"""Four exact logical Google routes (LIFE-379, LOS-86 T08): tenant A Gmail, tenant A Drive, tenant B Gmail, tenant B Drive. Fake data only.

Labels are neutral (tenant A is the "personal" side, tenant B the "work" side in the Jira text). A read is routed by the caller's tenant, the provider and the exact pinned account
id, through the merged gate `authz.dispatch`; this module adds no policy of its own and no second gateway. There is no alias, no default and no most-recent account: anything but
an exact account id fails closed before a handler runs, with a receipt that names the denial. Discovery shows a caller only its own tenant's routes."""
from lifeos.synthetic import authz, composio as C, tools as T
from lifeos.synthetic.authz_fixture import NOW, AuthzWorld, TENANT_A, TENANT_B

ROUTES = {"a-gmail": {"tenant": TENANT_A, "provider": "gmail", "account": "acct-a-gmail"}, "a-drive": {"tenant": TENANT_A, "provider": "drive", "account": "acct-a-drive"},
          "b-gmail": {"tenant": TENANT_B, "provider": "gmail", "account": "acct-b-gmail"}, "b-drive": {"tenant": TENANT_B, "provider": "drive", "account": "acct-b-drive"}}
READ_TOOL = {"gmail": "gmail_read_message", "drive": "drive_read_file"}
WHO = {"agent-a": {"session": "sess-a", "task": "task-a"}, "agent-b": {"session": "sess-b", "task": "task-b"}}
# The native ChatGPT connections stay what they are: configured by ChatGPT, never copied, never reused by these routes.
NATIVE_CHATGPT = {provider: {"managed_by": "chatgpt", "account_label": f"native-chatgpt-{provider}", "token_copied": False, "token_reused": False} for provider in ("gmail", "drive")}


class RouteWorld(AuthzWorld):
    """The authorization world plus one fake message and one fake document per side."""

    def __init__(self):
        super().__init__()
        self.sources.update({"P-MSG-1": {"tenant": TENANT_A, "kind": "gmail", "revision": 1, "text": "Fake tenant A message."},
                             "P-DOC-1": {"tenant": TENANT_A, "kind": "drive", "revision": 1, "text": "Fake tenant A document."},
                             "B-MSG-1": {"tenant": TENANT_B, "kind": "gmail", "revision": 1, "text": "Fake tenant B message."}})


def route_of(account_id):
    return next((rid for rid, r in ROUTES.items() if r["account"] == account_id), None)


def _request(world, who, account_id, tool, nonce, source_id=None):
    ctx, agent = WHO.get(who, {"session": "no-session", "task": "no-task"}), world.agents.get(who)
    src = world.sources.get(source_id) if source_id else None
    req = {"case": "route", "tenant": agent["tenant"] if agent else "no-tenant", "agent_id": who, "host_id": "fake-mac", "session_id": ctx["session"], "task_id": ctx["task"], "tool": tool,
           "nonce": nonce, "channel": "v7_adapter", "account_id": account_id, "grant_id": f"g-{account_id}-read" if isinstance(account_id, str) else None, "object_id": source_id or "obj"}
    if source_id:
        req.update(source_id=source_id, source_revision=src["revision"] if src else 0)
    return {k: v for k, v in req.items() if v is not None}


def route_read(world, who, account_id, object_id, nonce="n1", now=NOW):
    """-> (receipt, record). The tool is the one the object needs (a message needs gmail, a document needs drive); the account must be that provider's exact account.
    The record, only on ALLOW, carries the source text with its attribution; nothing from another tenant's source can come back."""
    src = world.sources.get(object_id)
    tool = READ_TOOL.get(src["kind"] if src else None, READ_TOOL["gmail"])
    ledger = world.ledger_hash()
    flagged = C.consent_flag(world, account_id)                        # a managed connection that needs the owner is shown to the gate as NEEDS_OWNER_RECONSENT, exactly as composio.execute does
    try:
        receipt, results = authz.dispatch(world, _request(world, who, account_id, tool, nonce, object_id), now)
    finally:
        if flagged:
            world.consent_needed.discard(account_id)
    if flagged:
        receipt["policy_ledger_hash_before"] = receipt["policy_ledger_hash_after"] = ledger      # the flag was only for the gate: a refusal changes nothing, and the receipt says so
    if receipt["decision_code"] != "ALLOW":
        return receipt, None
    provider = world.accounts[account_id][1]
    return receipt, {"route": route_of(account_id), "tenant": src["tenant"], "provider": provider, "account": account_id, "object_id": object_id, "revision": src["revision"],
                     "text": src["text"], "attribution": f"{provider}/{account_id}"}


def discover(world, who, account_id, nonce="d1", now=NOW):
    """-> (receipt, tool names). Discovery of one exact account: the same gate, no handler. A missing, ambiguous or another tenant's account is refused with a named denial."""
    acct = world.accounts.get(account_id)
    req = _request(world, who, account_id, READ_TOOL.get(acct[1] if acct else None, READ_TOOL["gmail"]), nonce)
    before = (world.provider_hash(), world.ledger_hash(), world.read_calls, world.mutation_calls, world.commits)
    verdict = authz.preflight(world, req, now)
    receipt = authz.build_receipt(world, req, now, verdict, "preflight", before)
    if verdict["code"] != "ALLOW":
        return receipt, []
    return receipt, sorted(n for n, t in T.TOOLS.items() if t["service"] == acct[1] and not t["unsupported"])


def visible_routes(world, who):
    """The route ids a caller may even see: its own tenant's, never another's."""
    agent = world.agents.get(who)
    return sorted(rid for rid, r in ROUTES.items() if agent and r["tenant"] == agent["tenant"])
