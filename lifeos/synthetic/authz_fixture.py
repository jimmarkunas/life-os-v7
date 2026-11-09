"""The fake world for the policy-gated tool contract (LIFE-374, fixture plan LIFE-372). Fake data only; nothing here talks to a network or a provider.

AuthzWorld extends the LIFE-373 world with the things a tool call is judged against: agents, a verified host, sessions, tasks, accounts, grants, approvals,
a writer-claim table and one fixed clock. The services the real LIFE-403 policy knows (jira, notion, github, gmail, drive, calendar) take their level from its policy document
(lifeos/platform/access_policy.py); the others (social, finance, composio, buzz) take it from the grant. Approvals are Jim's, kept in a trusted table here;
a request only names one by id. Nonces and writer claims are kept per tenant so one tenant can never see another's."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from lifeos.platform import access_policy
from lifeos.synthetic.fixture import TENANT_A, TENANT_B, TENANT_DECOY, World

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)      # the one fixed clock; decisions are in UTC, shown in Central only by the audit line
CANARY = "CANARY-7F3A-FAKE"                                    # a fake secret-shaped marker; it is not a real secret
KEY_A, KEY_B = "a1" * 32, "b2" * 32                            # invented 64-hex agent keys
BULK_LIMIT = 10
V7_SERVICES = access_policy.SERVICES
# account id -> (tenant, service)
ACCOUNTS = {"acct-a-gmail": (TENANT_A, "gmail"), "acct-a-drive": (TENANT_A, "drive"), "acct-a-calendar": (TENANT_A, "calendar"), "acct-a-social": (TENANT_A, "social"),
            "acct-a-finance": (TENANT_A, "finance"), "acct-a-composio": (TENANT_A, "composio"), "acct-a-buzz": (TENANT_A, "buzz"), "acct-a-jira": (TENANT_A, "jira"),
            "acct-a-notion": (TENANT_A, "notion"), "acct-b-gmail": (TENANT_B, "gmail"), "acct-b-drive": (TENANT_B, "drive"), "acct-b-notion": (TENANT_B, "notion")}
POLICY_DOC = {"version": 1, "bulk_limit": BULK_LIMIT, "default": {},
              "agents": {KEY_A: {"name": "Fake Agent A", "jira": "read", "notion": "write", "github": "read", "gmail": "read", "drive": "read", "calendar": "read"},
                         KEY_B: {"name": "Fake Agent B", "jira": "off", "notion": "write", "github": "off", "gmail": "read", "drive": "read"}}}


def ref_hash(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def approval_hash(tools, account, tenant, host, task, body="", revision=None):
    """The exact-action hash an approval is bound to: what, on which account and tenant, from which host and task, with which content and source revision."""
    return ref_hash("approval", "+".join(tools), account, tenant, host, task, ref_hash(body), revision)


class AuthzWorld(World):
    def __init__(self):
        super().__init__()
        self.policy = access_policy.load(json.dumps(POLICY_DOC))
        self.agents = {"agent-a": {"tenant": TENANT_A, "key": KEY_A}, "agent-b": {"tenant": TENANT_B, "key": KEY_B}}
        self.hosts = {"fake-mac"}                                                       # verified hosts; anything else is unknown
        self.sessions = {"sess-a": {"agent": "agent-a", "host": "fake-mac", "expires": NOW + timedelta(hours=8)},
                         "sess-b": {"agent": "agent-b", "host": "fake-mac", "expires": NOW + timedelta(hours=8)},
                         "sess-a-old": {"agent": "agent-a", "host": "fake-mac", "expires": NOW - timedelta(hours=1)}}
        self.tasks = {"task-a": {"agent": "agent-a", "blocked": False}, "task-a-blocked": {"agent": "agent-a", "blocked": True}, "task-b": {"agent": "agent-b", "blocked": False}}
        self.accounts = dict(ACCOUNTS)
        self.consent_needed = set()
        self.management_agents = set()                                                    # agents with an approved admin or proxy scope; none by default
        self.grants = {}
        for acct, (tenant, service) in ACCOUNTS.items():
            agent = "agent-a" if tenant == TENANT_A else "agent-b"
            for level in ("read", "write"):
                self.grants[f"g-{acct}-{level}"] = {"agent": agent, "tenant": tenant, "account": acct, "service": service, "level": level, "revoked": False,
                                                    "expires": NOW + timedelta(days=30), "review_due": NOW + timedelta(days=20)}
        self.req_nonces, self.writer_claims = set(), {}                                   # keys carry the tenant: (tenant, nonce) and (tenant, resource)
        self.approvals = {}                                                                # id -> Jim's approval; only this table can approve anything
        self.outbox = []                                                                  # committed fake side effects: (tool, account hash)
        self.ledger_log = []                                                              # local emergency-revoke log, nothing else

    def issue_approval(self, approval_id, req, expires=None, **patch):
        """Jim approves one exact action. This is the only way an approval comes to exist; a request cannot make one."""
        names = req.get("batch") or [req["tool"]]
        rec = {"issuer": "jim", "tenant": req["tenant"], "host": req["host_id"], "task": req["task_id"], "account": req["account_id"], "used": False,
               "action_hash": approval_hash(names, req["account_id"], req["tenant"], req["host_id"], req["task_id"], req.get("body", ""), req.get("source_revision")),
               "expires": expires or NOW + timedelta(hours=1)}
        rec.update(patch)
        self.approvals[approval_id] = rec
        return approval_id

    def revoke_grant(self, grant_id):
        """Emergency revoke (LIFE-372 N15): changes only the fake local grant ledger, never provider state."""
        self.grants[grant_id]["revoked"] = True
        self.ledger_log.append(("revoke", grant_id))

    def provider_hash(self):
        body = {"base": super().state_hash(), "outbox": self.outbox}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]

    def ledger_hash(self):
        grants = {k: {**v, "expires": v["expires"].isoformat(), "review_due": v["review_due"].isoformat()} for k, v in self.grants.items()}
        approvals = {k: {**v, "expires": v["expires"].isoformat()} for k, v in self.approvals.items()}
        claims = {f"{t}|{r}": c for (t, r), c in self.writer_claims.items()}
        body = {"grants": grants, "mgmt": sorted(self.management_agents), "req": sorted([t, n] for t, n in self.req_nonces), "appr": approvals, "claims": claims,
                "consent": sorted(self.consent_needed), "log": self.ledger_log}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]

