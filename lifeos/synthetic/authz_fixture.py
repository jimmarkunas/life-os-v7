"""The fake world for the policy-gated tool contract (LIFE-374, fixture plan LIFE-372). Fake data only; nothing here talks to a network or a provider.

AuthzWorld extends the LIFE-373 world with the things a tool call is judged against: agents, a verified host, sessions, tasks, accounts, grants, approvals,
a writer-claim table and one fixed clock. The V7 services (jira, notion, github) take their level from the real LIFE-403 policy document
(lifeos/platform/access_policy.py); the synthetic services (gmail, drive, calendar, ...) take it from the grant."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from lifeos.platform import access_policy
from lifeos.synthetic.fixture import TENANT_A, TENANT_B, TENANT_DECOY, World

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)      # the one fixed clock; decisions are in UTC, shown in Central only by the audit line
CANARY = "CANARY-7F3A-FAKE"                                    # a fake secret-shaped marker; it is not a real secret
KEY_A, KEY_B = "a1" * 32, "b2" * 32                            # invented 64-hex agent keys
BULK_LIMIT = 10
SYNTHETIC_SERVICES = ("gmail", "drive", "calendar", "social", "finance", "composio", "buzz")
V7_SERVICES = access_policy.SERVICES
# account id -> (tenant, service)
ACCOUNTS = {"acct-a-gmail": (TENANT_A, "gmail"), "acct-a-drive": (TENANT_A, "drive"), "acct-a-calendar": (TENANT_A, "calendar"), "acct-a-social": (TENANT_A, "social"),
            "acct-a-finance": (TENANT_A, "finance"), "acct-a-composio": (TENANT_A, "composio"), "acct-a-buzz": (TENANT_A, "buzz"), "acct-a-jira": (TENANT_A, "jira"),
            "acct-a-notion": (TENANT_A, "notion"), "acct-b-gmail": (TENANT_B, "gmail"), "acct-b-drive": (TENANT_B, "drive"), "acct-b-notion": (TENANT_B, "notion")}
POLICY_DOC = {"version": 1, "bulk_limit": BULK_LIMIT, "default": {},
              "agents": {KEY_A: {"name": "Fake Agent A", "jira": "read", "notion": "write", "github": "read"},
                         KEY_B: {"name": "Fake Agent B", "jira": "off", "notion": "write", "github": "off"}}}


def ref_hash(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def approval_hash(tools, account, tenant, host, task):
    """The exact-action hash a Jim approval must carry: what, on which account and tenant, from which host and task."""
    return ref_hash("approval", "+".join(tools), account, tenant, host, task)


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
        self.req_nonces, self.approval_nonces, self.writer_claims = set(), set(), {}
        self.outbox = []                                                                  # committed fake side effects: (tool, account hash)
        self.ledger_log = []                                                              # local emergency-revoke log, nothing else

    def revoke_grant(self, grant_id):
        """Emergency revoke (LIFE-372 N15): changes only the fake local grant ledger, never provider state."""
        self.grants[grant_id]["revoked"] = True
        self.ledger_log.append(("revoke", grant_id))

    def provider_hash(self):
        body = {"base": super().state_hash(), "outbox": self.outbox}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]

    def ledger_hash(self):
        grants = {k: {**v, "expires": v["expires"].isoformat(), "review_due": v["review_due"].isoformat()} for k, v in self.grants.items()}
        body = {"grants": grants, "mgmt": sorted(self.management_agents), "req": sorted(self.req_nonces), "appr": sorted(self.approval_nonces), "claims": self.writer_claims, "consent": sorted(self.consent_needed), "log": self.ledger_log}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]

