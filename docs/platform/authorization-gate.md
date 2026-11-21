# Central authorization gate (LIFE-811 / F02) — synthetic only

`lifeos/platform/authorization_gate.py` is one pure function, `execute`, in front of one side effect. It reuses `capability_catalog.resolve` (LIFE-810), `access_policy.decide` (LIFE-374/403) and a grant shaped like LIFE-707/721's. It adds no service, store, credential or endpoint.

Order: validate, tenant (personal, bytalos, tgp; a caller may exclude one), trusted session, catalog row, policy level, grant (principal, tenant, account, tool), revoke, expiry, purpose, exact action, human approval for send, delete, permission and production, receipt, then the effect. Every deny is before the effect and writes a redacted receipt (fixed words, fingerprints, Central time). No receipt, no effect.

Approvals are one request each, from a human the host names (never the agent), and go stale after 15 minutes. An approval never widens a grant, never beats a revoked grant or a policy deny.

**What this proves:** the rules above, with fabricated agents and a fake provider (`tests/platform/test_authorization_gate.py`). **What it does not prove:** any live adapter, tenant, grant or client boundary. `live_proven` is always False and receipts say `mode=synthetic` unless the catalog row says `working`. The live proof stays with Jim's separate protected approval.
