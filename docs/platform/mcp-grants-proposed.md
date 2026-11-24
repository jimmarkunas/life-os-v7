# Buzz publish grants (LIFE-865) — ACTIVE in main from the activation PR

Status: **active.** Jim decided option A (chat, Nov 22, 2026, 6:14 PM CT, LIFE-865 comment 13707): publish grants for `claude-chat` and `chatgpt-chat` only in the communities they can write to today, expiring Feb 22, 2027; the relay's channel allowlist still limits channels. The rows below were moved into `lifeos/platform/mcp_access.json` and `capability_catalog.json` (catalog status stays `synthetic` until the live proof). The release is built from that merge commit.

## Why these, and no more

Read on Oracle Nov 22 2026, 5:50 PM CT (the one authorized root read, LIFE-873 comment 13696; community and client names only):

- `BUZZ_OAUTH_USERS`: one user for client `claude` and one for `chatgpt`, each allowed `life-os` and `bytalos`.
- `BUZZ_WRITE_CHANNELS`: five allowlisted channels, in `life-os` (3) and `bytalos` (2).

So today both chat clients can publish in both communities, limited by the relay's channel allowlist, which the governed release does not change. Community `life-os` is tenant `personal`; `bytalos` is tenant `bytalos` (`mcp_access.json` `communities`). The proposed grants are exactly that and nothing wider: two clients x two tenants, the one tool `buzz_publish` (the legacy `buzz_post_message` is gated as `buzz_publish`), action `write`, purpose `buzz-publish`, expiry 2027-02-22, revocable by one field. No other tool, tenant, client or channel.

**Cannot be verified from the configuration read:** whether each client's OAuth token carries the `buzz:write` scope (set at the identity provider, not in this file); which of the five channels each client actually posts to (not read; channel ids were deliberately not printed); whether anyone published through this MCP recently (the journal was not read). These grants therefore preserve what is configured, not what is used.

## Proposed grants (`mcp_access.json` `grants`)

```json
[
 {
  "id": "g-claude-chat-personal-buzz_publish",
  "principal": "claude-chat",
  "tenant": "personal",
  "account": "buzz",
  "tool": "buzz_publish",
  "purpose": "buzz-publish",
  "actions": [
   "write"
  ],
  "credential_ref": "buzz-scoped-member-key",
  "expires": "2027-02-22",
  "revoked": false
 },
 {
  "id": "g-claude-chat-bytalos-buzz_publish",
  "principal": "claude-chat-bytalos",
  "tenant": "bytalos",
  "account": "buzz",
  "tool": "buzz_publish",
  "purpose": "buzz-publish",
  "actions": [
   "write"
  ],
  "credential_ref": "buzz-scoped-member-key",
  "expires": "2027-02-22",
  "revoked": false
 },
 {
  "id": "g-chatgpt-chat-personal-buzz_publish",
  "principal": "chatgpt-chat",
  "tenant": "personal",
  "account": "buzz",
  "tool": "buzz_publish",
  "purpose": "buzz-publish",
  "actions": [
   "write"
  ],
  "credential_ref": "buzz-scoped-member-key",
  "expires": "2027-02-22",
  "revoked": false
 },
 {
  "id": "g-chatgpt-chat-bytalos-buzz_publish",
  "principal": "chatgpt-chat-bytalos",
  "tenant": "bytalos",
  "account": "buzz",
  "tool": "buzz_publish",
  "purpose": "buzz-publish",
  "actions": [
   "write"
  ],
  "credential_ref": "buzz-scoped-member-key",
  "expires": "2027-02-22",
  "revoked": false
 }
]
```

## Proposed catalog rows (`capability_catalog.json` `capabilities`, status stays `synthetic` until the live proof)

```json
[
 {
  "principal": "claude-chat",
  "tool": "buzz_publish",
  "action": "write",
  "provider": "buzz",
  "account_alias": null,
  "host": "oracle-edge",
  "source": "lifeos/buzz/mcp.py",
  "as_of": "2026-11-22",
  "max_age_days": 90,
  "status": "synthetic"
 },
 {
  "principal": "claude-chat-bytalos",
  "tool": "buzz_publish",
  "action": "write",
  "provider": "buzz",
  "account_alias": null,
  "host": "oracle-edge",
  "source": "lifeos/buzz/mcp.py",
  "as_of": "2026-11-22",
  "max_age_days": 90,
  "status": "synthetic"
 },
 {
  "principal": "chatgpt-chat",
  "tool": "buzz_publish",
  "action": "write",
  "provider": "buzz",
  "account_alias": null,
  "host": "oracle-edge",
  "source": "lifeos/buzz/mcp.py",
  "as_of": "2026-11-22",
  "max_age_days": 90,
  "status": "synthetic"
 },
 {
  "principal": "chatgpt-chat-bytalos",
  "tool": "buzz_publish",
  "action": "write",
  "provider": "buzz",
  "account_alias": null,
  "host": "oracle-edge",
  "source": "lifeos/buzz/mcp.py",
  "as_of": "2026-11-22",
  "max_age_days": 90,
  "status": "synthetic"
 }
]
```
