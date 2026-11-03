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
- Authorization is two checks, both fail closed: the OAuth client must be allowlisted (`BUZZ_OAUTH_CLIENTS`, a list of client ids) AND the token `sub` must be allowlisted (`BUZZ_OAUTH_USERS`, `<sub>:<claude|chatgpt>:<community>+<community>`) with a product label and the communities that user may reach. An unlisted user gets `403 user_not_authorized` (the denied `sub` is logged so the owner can allowlist it); a community outside the user's list is denied before any relay request.
- `lifeos/buzz_auth.py` validates RS256 JWTs: signature against the issuer's JWKS, `iss`, `aud` = this resource, `exp`/`nbf`, the signed `client_id`/`azp` against `BUZZ_OAUTH_CLIENTS`, and the signed `sub` against `BUZZ_OAUTH_USERS`. The product label (claude or chatgpt) comes ONLY from that verified `sub`; caller-sent actor strings, headers and arguments are ignored. Scopes may carry the audience prefix Oracle IAM adds (`<audience>buzz:read`) and no other prefix.
- Scopes: `buzz:read` for the three read tools, `buzz:write` for `buzz_post_message`. Writes additionally need the exact `(community, channel)` in `BUZZ_WRITE_CHANNELS`, which ships empty.
- Idempotency survives restarts through the relay: every posted event carries `k` (hash of community|channel|actor|key) and `i` (hash including text) tags. A retry reads `#k` first: same text returns `already_delivered`, different text returns `idempotency_conflict`. The in-memory cache only reuses an already-signed event.
- **Four identities, no fallback.** The signing key is chosen by (the token's verified client label, the requested community): `buzz-claude-lifeos`, `buzz-claude-bytalos`, `buzz-chatgpt-lifeos`, `buzz-chatgpt-bytalos`, each its own keypair (`BUZZ_KEY_<CLAUDE|CHATGPT>_<LIFE_OS|BYTALOS>`). A missing key is `key_not_configured` for that pairing only; a client with no key at all is `client_identity_not_configured`; the caller cannot name a client or actor; the old shared `BUZZ_KEY_<COMMUNITY>` and any key reused across two slots are refused at startup. This needs two distinct IdP USERS, one per product (for example `jimmarkunas+claude@gmail.com` and `jimmarkunas+chatgpt@gmail.com`): each product's connector must be authorized by its own user, so the verified `sub` tells the products apart. One registered OAuth client can serve both. Authorizing a connector with the wrong user makes it sign as the other product's identity.
- Audit: one JSON line per tool call on stderr (journald) with client label, the signing identity's pubkey prefix, token `sub`, the token's `iat` and `exp`, `tid` (first 8 hex of a hash of the token signature, so a renewed token shows as a NEW tid without exposing the token), tool, community, outcome code. An expired token is logged as `invalid_token` with reason `expired` and the verified `sub` (evidence of whether a client renews silently); other bad tokens log `invalid_token` with no subject; a refused user logs `user_not_authorized` with its `sub`. Never tokens, keys or message text.

## Not supplied by this repo (gates)
1. **Authorization server.** ChatGPT requires AS metadata advertising `S256` PKCE and sends `resource=` that must land in `aud`; it registers by CIMD (`https://chatgpt.com/oauth/client.json`), DCR or a predefined client. Claude offers its published identity, automatic registration (DCR) or your own client. No identity provider is installed on the host and none is approved. Jim must pick one that issues RS256 JWTs, supports the `resource` indicator, PKCE S256 and DCR or CIMD, and costs $0. This service validates tokens only.
2. **Public HTTPS.** The host is tailnet-only (public iptables allows only SSH). Claude requires the server to be reachable from Anthropic's IP ranges, so tailnet-only will not work. The supported existing option is Tailscale Funnel on port 8443 (443 is the tailnet-only OpenClaw Serve): `tailscale funnel --bg --https=8443 http://127.0.0.1:8787`. It needs Funnel enabled in the tailnet policy and OCI security-list review. No second gateway is added.
3. **Four member keys.** Four non-owner Buzz identities, each enrolled in its own community and encrypted with `systemd-creds` into the four `.cred` files named in the unit. See "Onboarding the four identities".
4. **Two IdP users, one OAuth client.** Oracle IAM has no dynamic registration and rejects both products' client-ID-document routes today, so one registered client (the resource application itself, configured as a client) serves both. Its sign-in for these users did not demand a second factor (tested for the Claude user with a direct authorization request); the console sign-in does, and that policy was not changed.

## Deploy (all protected; run by `opc`, only after the gates)
```
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin buzz-mcp
sudo install -d -o root -g root /opt/lifeos-buzz-mcp/releases/<sha> /etc/lifeos-buzz-mcp
# copy lifeos/__init__.py lifeos/buzz_*.py (nothing else; tests/buzz/test_remote_mcp.py::Startup runs the unit's ExecStart from exactly this layout) into releases/<sha>/lifeos/ ; ln -sfn releases/<sha> /opt/lifeos-buzz-mcp/current
# /etc/lifeos-buzz-mcp/config.env (non-secret): BUZZ_MCP_PUBLIC_URL, BUZZ_OAUTH_ISSUER, BUZZ_OAUTH_JWKS_URL, BUZZ_OAUTH_CLIENTS, BUZZ_OAUTH_AS_URL (only if the IdP publishes its metadata away from the issuer, as Oracle IAM does), BUZZ_OAUTH_USERS (<sub>:<claude|chatgpt>:life-os+bytalos,...; the owner-approved users), BUZZ_URL_LIFE_OS, BUZZ_URL_BYTALOS, BUZZ_WRITE_CHANNELS=
# the four key files are encrypted straight from stdin (no plaintext file on the VM): see "Onboarding the four identities", step 4
sudo cp lifeos/edge/buzz/oracle/lifeos-buzz-mcp.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now lifeos-buzz-mcp
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8787/mcp -d '{}'      # expect 401
```
Capacity: unit caps memory at 256 MB and CPU at 50% of one core; the host has about 20 GB available. Does not restart OpenClaw or LIFE OS Close.

## Rollback
`sudo tailscale funnel --https=8443 off` (closes public exposure first), `sudo systemctl disable --now lifeos-buzz-mcp`, remove the unit, `/etc/lifeos-buzz-mcp`, `/opt/lifeos-buzz-mcp` and the `buzz-mcp` user; remove the four member identities in Buzz and the OAuth client at the identity provider. To roll back only a release, repoint `current` to the previous `releases/<sha>` and restart the unit.

## Enabling writes (separate approval, in order)
1. Real read acceptance from both chats passes.
2. Verify against the real relay that the `#k`/`#i` tag filters are indexed and a just-posted event is immediately visible to a `#k` query. If the relay cannot do this, leave writes off: cross-restart conflict rejection is then unproven. No new datastore is allowed.
3. Confirm the ChatGPT account's custom-MCP write entitlement (the docs do not state which plans allow it).
4. Put one synthetic test `community:channel` in `BUZZ_WRITE_CHANNELS`, post once, read back event id, content, channel and `actor` from the other client.

## Onboarding the four identities (Jim authorizes each step; nothing here is done by the code)
Names: `buzz-chatgpt-lifeos`, `buzz-chatgpt-bytalos`, `buzz-claude-lifeos`, `buzz-claude-bytalos`. No owner key is ever used or copied.
1. **Generate (your Mac, trusted).** Save the helper below as `keygen.py` and run `cd <life-os-v7 checkout> && PYTHONPATH=. python3 -s keygen.py ~/buzz-keys` (do not use `-I`). It writes four secret files (mode 0600, directory 0700) and prints ONLY the four public `npub` values. Never paste a `.key` file or its contents into chat, Jira or GitHub.
2. **Back up.** Put the four secrets in your existing Bitwarden vault (one secure note each, named as above). No new Bitwarden project or machine account.
3. **Invite by npub (Buzz app, community owner).** In the LIFE OS community invite `buzz-claude-lifeos` and `buzz-chatgpt-lifeos`; in Bytalos invite `buzz-claude-bytalos` and `buzz-chatgpt-bytalos`, each by pasting its npub with role **Member** (never Owner or Admin). Block's app source shows this pubkey-invite flow (mobile "Search npub", role defaults to Member); exact menu labels on your build are UNVERIFIED. Then add each identity only to the approved channels (BUZZ-09); Ted and Carlos are not members of the synthetic Bytalos test. Cross-check each npub against step 1's printed value before approving.
4. **Install on the VM (protected; after the gates above).** For each name, pipe the key file over ssh into `systemd-creds` so no plaintext lands on the VM, e.g. `ssh opc@lifeos-edge-01 'sudo systemd-creds encrypt --name=buzz_key_claude_life_os - /etc/lifeos-buzz-mcp/buzz_key_claude_life_os.cred' < ~/buzz-keys/buzz-claude-lifeos.key`. Mapping: claude-lifeos to `buzz_key_claude_life_os`, claude-bytalos to `buzz_key_claude_bytalos`, chatgpt-lifeos to `buzz_key_chatgpt_life_os`, chatgpt-bytalos to `buzz_key_chatgpt_bytalos`. Then delete `~/buzz-keys`.
5. **Verify (read first).** Through the MCP, each of the four pairings lists channels in its own community and gets `community_not_authorized` for a community outside the user's list or `key_not_configured` if its key is absent. Writes stay off until the "Enabling writes" gates pass.

Key helper (stdlib only; prints public values only; tested with throwaway keys and the published NIP-19 vector):
```python
import os, sys
from pathlib import Path
from lifeos import buzz_nip98 as nip98

def pm(values):
    chk = 1
    for v in values:
        top, chk = chk >> 25, ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            chk ^= nip98.GEN[i] if (top >> i) & 1 else 0
    return chk

def npub(pub_hex):
    bits = "".join(f"{b:08b}" for b in bytes.fromhex(pub_hex))
    bits += "0" * (-len(bits) % 5)
    data = [int(bits[i:i + 5], 2) for i in range(0, len(bits), 5)]
    mod = pm([ord(c) >> 5 for c in "npub"] + [0] + [ord(c) & 31 for c in "npub"] + data + [0] * 6) ^ 1
    return "npub1" + "".join(nip98.B32[x] for x in data + [(mod >> 5 * (5 - i)) & 31 for i in range(6)])

out = Path(sys.argv[1]).expanduser()
out.mkdir(mode=0o700, parents=True, exist_ok=False)
for name in ("buzz-chatgpt-lifeos", "buzz-chatgpt-bytalos", "buzz-claude-lifeos", "buzz-claude-bytalos"):
    while True:
        raw = os.urandom(32)
        if 1 <= int.from_bytes(raw, "big") < nip98.N:
            break
    fd = os.open(out / (name + ".key"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(raw.hex() + "\n")
    print(name, npub(nip98.pubkey(raw)))   # only the PUBLIC npub is printed; the secret stays in the 0600 file
```

Oracle IAM (Default identity domain) notes: `iss` is `https://identity.oraclecloud.com/` (a trailing slash is treated as equivalent); metadata and signing keys live at the domain URL (`BUZZ_OAUTH_AS_URL`, `BUZZ_OAUTH_JWKS_URL`, the latter needs "Configure client access" on); no dynamic registration, and neither Claude's nor ChatGPT's client-ID-document route is accepted by Oracle today, so one registered client (confidential, authorization code, the products' callback URLs) serves both and the two products are told apart by their IdP user. Scope names are `<audience>buzz:read` and `<audience>buzz:write`; the access token `sub` format must be confirmed from the first real login (the server logs a denied `sub`).

## Addressed messages (LIFE-249, off by default)
A message can carry one signed recipient `p` tag (Buzz's native mention form: kind 9, `p` = the recipient's hex pubkey), chosen by NAME, never by key: `buzz_post_message` takes an optional `recipient` from `ChatGPT-Chat | Claude-Chat | Claude-Code | Codex`. The name must be in `BUZZ_RECIPIENTS=<alias>:<community>:<hex pubkey>,...` for that community (owner-approved, existing identities only), the calling product must be in `BUZZ_MENTION_SENDERS=claude,chatgpt`, and that key must already be in the destination channel's current member list (Buzz's mention contract; nothing is invited or added, so a Public channel whose list omits the agent refuses until an owner adds it). Anything else is refused before an event exists. The recipient is part of the dedup digest, so a retry cannot retarget. Unaddressed posts are unchanged.
`buzz_get_addressed` reads events whose `p` tag names the caller's own identity from ordinary relay history; it is stateless (no inbox, nothing marked read or acknowledged) and pages newest-first: pass the returned `next_before` as `before` to reach older messages (the boundary second may repeat; dedupe by event id). A `p` tag makes a message findable; it does NOT wake ChatGPT Chat or Claude Chat, and a native agent answers only if its own authorized harness (for example `buzz-acp` with `require_mention`) is running and accepts the sender. Not deployed: the server's `config.env` is unchanged until separately approved.

Known limits: one process only (the write lock is in-process); the relay's read-after-write consistency is unverified; a new IdP client registration changes the `client_id`, so `BUZZ_OAUTH_CLIENTS` must be updated to match.
