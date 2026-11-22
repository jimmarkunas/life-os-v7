"""LIFE-865 test data: the real seeded mcp_access.json and capability catalog, widened for tests that exercise a server's own behavior (publish grants for both chat clients), with knobs for the gate's refusal reasons."""
import copy
import json
from datetime import datetime, timezone

from lifeos.platform import mcp_access

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
DOC = json.loads(mcp_access.FILE.read_text())
CATALOG = json.loads(mcp_access.CATALOG.read_text())


def build(grants=None, clients=None, catalog_rows=()):
    doc, cat = copy.deepcopy(DOC), copy.deepcopy(CATALOG)
    cat["capabilities"] += list(catalog_rows)
    if grants is not None:
        doc["grants"] = grants(doc["grants"]) if callable(grants) else grants
    if clients is not None:
        doc["clients"] = clients
    return mcp_access.load(json.dumps(doc), json.dumps(cat))


def principal(client, tenant):
    return client if tenant == "personal" else f"{client}-{tenant}"


def publish_rows():
    """Grants and catalog rows that let both chat clients publish in both tenants (a test widening only; the seed grants reads)."""
    grants, rows = [], []
    for client in ("claude-chat", "chatgpt-chat"):
        for tenant in ("personal", "bytalos"):
            who = principal(client, tenant)
            grants.append({"id": f"t-{who}-publish", "principal": who, "tenant": tenant, "account": "buzz", "tool": "buzz_publish", "purpose": "buzz-publish", "actions": ["write"], "credential_ref": "buzz-scoped-member-key", "expires": "2027-02-22", "revoked": False})
            rows.append({"principal": who, "tool": "buzz_publish", "action": "write", "provider": "buzz", "account_alias": None, "host": "oracle-edge", "source": "lifeos/buzz/mcp.py", "as_of": "2026-11-22", "max_age_days": 90, "status": "synthetic"})
    return grants, rows


def open_access():
    extra, rows = publish_rows()
    return build(grants=lambda seeded: seeded + extra, catalog_rows=rows)
