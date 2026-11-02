# Buzz remote MCP on lifeos-edge-01 (LIFE-216 / BUZZ-16)

One loopback service, one public HTTPS URL, two clients (Claude Chat and ChatGPT Chat), the same four tools:

```
Claude Chat ─┐                                                     ┌─ life-os community (Block-hosted)
             ├─ HTTPS /mcp + OAuth token ─ TLS proxy ─ 127.0.0.1:8787 ─ lifeos.buzz_mcp ─ NIP-98 ─┤
ChatGPT Chat ┘                                                     └─ bytalos community (Block-hosted)
```

Status: **code only. Nothing is deployed, no public ingress exists, and no real Claude/ChatGPT connection has been made.** Fixture tests are not client acceptance.

## What the code does
- `/mcp` Streamable HTTP, JSON replies, stateless (no `Mcp-Session-Id`), protocol `2025-06-18` with `2025-03-26`/`2024-11-05` negotiated, `MCP-Protocol-Version`, `Accept` and `Origin` checks, notifications and client replies answered `202`, GET/DELETE `405`.
- `/.well-known/oauth-protected-resource` (and `/mcp` suffix form) names the authorization server; every `401` carries `WWW-Authenticate: Bearer resource_metadata=...`; wrong scope is `403 insufficient_scope`.
- `lifeos/buzz_auth.py` validates RS256 JWTs: signature against the issuer's JWKS, `iss`, `aud` = this resource, `exp`/`nbf`, and the signed `client_id`/`azp` against `BUZZ_OAUTH_CLIENTS`. That mapping (`<client_id>:claude|chatgpt`) is the only source of the audit label; caller-sent actor strings are ignored.
- Scopes: `buzz:read` for the three read tools, `buzz:write` for `buzz_post_message`. Writes additionally need the exact `(community, channel)` in `BUZZ_WRITE_CHANNELS`, which ships empty.
- Idempotency survives restarts through the relay: every posted event carries `k` (hash of community|channel|actor|key) and `i` (hash including text) tags. A retry reads `#k` first: same text returns `already_delivered`, different text returns `idempotency_conflict`. The in-memory cache only reuses an already-signed event.
- Audit: one JSON line per tool call on stderr (journald) with client label, token `sub`, tool, community, outcome code. Never tokens, keys or message text.

## Not supplied by this repo (gates)
1. **Authorization server.** ChatGPT requires AS metadata advertising `S256` PKCE and sends `resource=` that must land in `aud`; it registers by CIMD (`https://chatgpt.com/oauth/client.json`), DCR or a predefined client. Claude offers its published identity, automatic registration (DCR) or your own client. No identity provider is installed on the host and none is approved. Jim must pick one that issues RS256 JWTs, supports the `resource` indicator, PKCE S256 and DCR or CIMD, and costs $0. This service validates tokens only.
2. **Public HTTPS.** The host is tailnet-only (public iptables allows only SSH). Claude requires the server to be reachable from Anthropic's IP ranges, so tailnet-only will not work. The supported existing option is Tailscale Funnel on port 8443 (443 is the tailnet-only OpenClaw Serve): `tailscale funnel --bg --https=8443 http://127.0.0.1:8787`. It needs Funnel enabled in the tailnet policy and OCI security-list review. No second gateway is added.
3. **Member keys.** Two non-owner scoped Buzz member keys (life-os, bytalos), encrypted with `systemd-creds` into the `.cred` files named in the unit.

## Deploy (all protected; run by `opc`, only after the gates)
```
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin buzz-mcp
sudo install -d -o root -g root /opt/lifeos-buzz-mcp/releases/<sha> /etc/lifeos-buzz-mcp
# copy lifeos/__init__.py lifeos/buzz_*.py (nothing else) into releases/<sha>/lifeos/ ; ln -sfn releases/<sha> /opt/lifeos-buzz-mcp/current
# /etc/lifeos-buzz-mcp/config.env (non-secret): BUZZ_MCP_PUBLIC_URL, BUZZ_OAUTH_ISSUER, BUZZ_OAUTH_JWKS_URL, BUZZ_OAUTH_CLIENTS, BUZZ_URL_LIFE_OS, BUZZ_URL_BYTALOS, BUZZ_WRITE_CHANNELS=
printf %s "$KEY" | sudo systemd-creds encrypt --name=buzz_key_life_os - /etc/lifeos-buzz-mcp/buzz_key_life_os.cred   # same for bytalos
sudo cp lifeos/edge/buzz/oracle/lifeos-buzz-mcp.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now lifeos-buzz-mcp
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8787/mcp -d '{}'      # expect 401
```
Capacity: unit caps memory at 256 MB and CPU at 50% of one core; the host has about 20 GB available. Does not restart OpenClaw or LIFE OS Close.

## Rollback
`sudo tailscale funnel --https=8443 off` (closes public exposure first), `sudo systemctl disable --now lifeos-buzz-mcp`, remove the unit, `/etc/lifeos-buzz-mcp`, `/opt/lifeos-buzz-mcp` and the `buzz-mcp` user; revoke the two member keys in Buzz and the OAuth client at the identity provider. To roll back only a release, repoint `current` to the previous `releases/<sha>` and restart the unit.

## Enabling writes (separate approval, in order)
1. Real read acceptance from both chats passes.
2. Verify against the real relay that the `#k`/`#i` tag filters are indexed and a just-posted event is immediately visible to a `#k` query. If the relay cannot do this, leave writes off: cross-restart conflict rejection is then unproven. No new datastore is allowed.
3. Confirm the ChatGPT account's custom-MCP write entitlement (the docs do not state which plans allow it).
4. Put one synthetic test `community:channel` in `BUZZ_WRITE_CHANNELS`, post once, read back event id, content, channel and `actor` from the other client.

Known limits: one process only (the write lock is in-process); the relay's read-after-write consistency is unverified; a new IdP client registration (DCR) changes the `client_id`, so `BUZZ_OAUTH_CLIENTS` must be updated to match.
