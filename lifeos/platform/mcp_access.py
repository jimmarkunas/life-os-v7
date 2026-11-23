"""LIFE-865: the adapter that puts authorization_gate.execute in front of every LIFE OS MCP tool call.

It serves lifeos/buzz/mcp.py and lifeos.inbox.send_to_jim --mcp, and adds no grant store, policy or gateway.

Identity is derived from a trusted fact only: the OAuth client a verified token names (HTTP), or the OS user of the stdio server process (the kernel's answer, never an argument, header or environment variable the caller sends).
`clients` in mcp_access.json map one such fact to one client id; no match or two matches refuses. The purpose of a call is the tool's own, set server side. A client on a host it shares with another needs a fact only it holds
(its own OS user, tailnet node or scoped credential); two clients with the same fact are ambiguous and refused. Every failure here is a fixed code and refuses: an unreadable file means no call runs."""
import hashlib
import hmac
import json
import os
import re
import pwd
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from lifeos.platform import access_policy, authorization_gate as G, capability_catalog

FILE = Path(__file__).with_name("mcp_access.json")
CATALOG = Path(__file__).with_name("capability_catalog.json")


CRED_ID = re.compile(r"^[a-z][a-z0-9-]{2,31}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CRED_SECRET = re.compile(r"^[A-Za-z0-9_-]{43,86}$")
_DUMMY = hashlib.sha256(b"no-such-credential").hexdigest()


class AccessError(ValueError):
    """A fixed code only: governance_unavailable, identity_unknown, identity_ambiguous, unknown_tool, unknown_tenant."""


def _grant(row):
    return G.Grant(id=row["id"], principal=row["principal"], tenant=row["tenant"], account=row["account"], tool=row["tool"], purpose=row["purpose"], actions=tuple(row["actions"]),
                   credential_ref=row["credential_ref"], expires=datetime.fromisoformat(row["expires"] + "T23:59:59+00:00"), revoked=row["revoked"] is True)


def load(text=None, catalog_text=None):
    """-> the clients, grants, access policy, catalog, tool purposes and community tenants. Any problem is AccessError("governance_unavailable"): never a partial answer."""
    try:
        doc = json.loads(FILE.read_text() if text is None else text)
        catalog = capability_catalog.load(CATALOG.read_text() if catalog_text is None else catalog_text)
        policy = access_policy.load(json.dumps(doc["policy"]))
        if G.SECRET.search(json.dumps({k: v for k, v in doc.items() if k != "note"})):                          # a credential is named in the data, never held in it
            raise ValueError("secret_in_data")
        if doc["version"] != 1 or any(not isinstance(c["facts"], dict) or not c["facts"] or not all(isinstance(v, str) and v for v in c["facts"].values()) for c in doc["clients"]):
            raise ValueError("bad_shape")
        creds = tuple(doc.get("credentials", ()))
        if any(not CRED_ID.match(c["id"]) or not HEX64.match(c["sha256"]) or c.get("revoked") not in (True, False) or datetime.fromisoformat(c["expires"]) is None
               or not isinstance(c["communities"], list) or c["buzz_identity"] not in (None, "claude", "chatgpt") for c in creds) or len({c["id"] for c in creds}) != len(creds):
            raise ValueError("bad_credentials")
        return SimpleNamespace(credentials=creds, clients=tuple(doc["clients"]), grants=tuple(_grant(g) for g in doc["grants"]), policy=policy, catalog=catalog,
                               purpose=dict(doc["tool_purpose"]), communities=dict(doc["communities"]))
    except Exception:                                                                                                  # noqa: BLE001  a fixed code only
        raise AccessError("governance_unavailable") from None


def verify_credential(access, header, now=None):
    """LIFE-873: a scoped `lm1.<id>.<secret>` bearer credential -> its credential row. The service holds only the SHA-256 of the secret (a hash of 256 random bits is not a credential); it is compared in constant time,
    whether or not the id exists, so neither timing nor the reply says which part was wrong. A malformed, unknown, wrong, expired or revoked credential is the one fixed code invalid_credential. Nothing else the caller sends is read."""
    parts = header[len("Bearer "):].split(".") if isinstance(header, str) and header.startswith("Bearer lm1.") else []
    ok = len(parts) == 3 and parts[0] == "lm1" and bool(CRED_ID.match(parts[1])) and bool(CRED_SECRET.match(parts[2]))
    row = next((c for c in access.credentials if ok and c["id"] == parts[1]), None)
    digest = hashlib.sha256(parts[2].encode()).hexdigest() if ok else _DUMMY
    match = hmac.compare_digest(digest, row["sha256"] if row else _DUMMY)
    live = row is not None and row["revoked"] is False and datetime.fromisoformat(row["expires"] + "T23:59:59+00:00") > (now or datetime.now(timezone.utc))
    if not (ok and match and live and row is not None):
        raise AccessError("invalid_credential")
    return row


def granted_tools(access, client, now=None):
    """The gate tool names this client holds a live grant for in any tenant (what its tools/list may show). The gate still decides every call."""
    now = now or datetime.now(timezone.utc)
    principals = {principal_id(client, t) for t in G.TENANTS}
    return {g.tool for g in access.grants if g.principal in principals and not g.revoked and g.expires > now}


def stdio_facts():
    """The one trusted fact of a stdio server: the OS user it runs as, from the kernel. Nothing the caller sends is read."""
    return {"os_user": pwd.getpwuid(os.getuid()).pw_name}


def identify(access, transport, facts):
    """-> the one client row whose transport and every fact match; AccessError identity_unknown or identity_ambiguous otherwise."""
    rows = [c for c in access.clients if c["transport"] == transport and all(facts.get(k) == v for k, v in c["facts"].items())]
    if not rows:
        raise AccessError("identity_unknown")
    if len(rows) > 1:
        raise AccessError("identity_ambiguous")
    return rows[0]


def principal_id(client, tenant):
    """The catalog principal of one client in one tenant (a catalog principal belongs to one workspace): the client id for personal, `<id>-<tenant>` otherwise."""
    if tenant not in G.TENANTS:
        raise AccessError("unknown_tenant")
    return client["id"] if tenant == "personal" else f'{client["id"]}-{tenant}'


def call(access, client, tenant, tool, action, account, service, effect, audit, now=None):
    """-> authorization_gate.Outcome. The purpose and the request id are the server's; `effect()` runs only when the gate allowed the call and wrote its receipt."""
    purpose = access.purpose.get(tool)
    if purpose is None:
        raise AccessError("unknown_tool")
    session = G.Session(principal_id(client, tenant), client["policy_key"], tenant, True)
    request = G.Request(tool, action, account, purpose, service, uuid.uuid4().hex[:16])
    return G.execute(session, request, access.catalog, access.policy, access.grants, (), frozenset(), effect, audit, now or datetime.now(timezone.utc))
