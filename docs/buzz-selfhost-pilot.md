# Pinned private Buzz relay pilot on Oracle (LIFE-870)

Pack: `lifeos/edge/buzz/selfhost/` (compose file, control script, two systemd units). Scope approved by Jim (LIFE-887 comment 13630, LIFE-870 comment 13744): a bounded private pilot, **loopback and tailnet only, synthetic posts only**; no public or phone ingress and no production community cutover (separate later decisions). Installed under `/opt/lifeos-buzz-selfhost/`, apart from Honcho (`/opt/lifeos-honcho`) and the MCP service (`/opt/lifeos-buzz-mcp`).

**What runs:** relay `ghcr.io/block/buzz:0.3.0@sha256:84f34c5b…` (index; linux/arm64 `sha256:1d2ecf5f…`), Postgres 17, Redis 7, a MinIO-compatible store (`pgsty/silo`) and its one-shot bucket init, each pinned by digest (all have a linux/arm64 manifest, checked Nov 22 2026), each with a memory and CPU limit. The compose file is adapted from block/buzz `deploy/compose/compose.yml` at main commit `42dc2a8c580c4ee0b25af042b757c42d700b1d90`. **Compatibility of the main-branch compose with the 0.3.0 relay image is unproven until the loopback test below passes.** Only `127.0.0.1:3100` (relay) and `127.0.0.1:3180` (health) are published; no admin console, no push gateway.

**Secrets:** never in a file in this repository. The relay key is `lifeos/edge/BUZZ_SELFHOST_RELAY_KEY`, generated on Oracle by `store-credential` and placed with `place-credentials` as `/etc/lifeos-buzz-mcp/buzz_selfhost_relay_key.cred` (not loaded by the MCP service). The five service secrets (Postgres, Redis, storage access and secret key, git-hook HMAC) are generated on the host by `init` and kept as encrypted credentials in `/etc/lifeos-buzz-selfhost`; while the relay runs, the compose file reads them from a tmpfs file (`/run/lifeos-buzz-selfhost/env`, 0600) written at start. The owner **public** key (`RELAY_OWNER_PUBKEY`) is recorded by `init`.

**Disk guard:** the host had about 17 GB free on `/` (Nov 22 2026). `preflight` and `up` refuse to start below 8 GB free on the Docker disk (`SELFHOST_FLOOR_GB`), and the five-minute health timer reports `disk_low` rather than letting the disk fill.

## Install (root, each step proved before the next)
1. `place-credentials lifeos/edge/BUZZ_SELFHOST_RELAY_KEY` (names only printed).
2. Copy the pack to `/opt/lifeos-buzz-selfhost/`; `lifeos-buzz-selfhost init <owner public key>`; `lifeos-buzz-selfhost preflight`.
3. Install the two units and the timer, `systemctl daemon-reload`, `systemctl start lifeos-buzz-selfhost`; `lifeos-buzz-selfhost health`.
4. Loopback compatibility test: our `lifeos/buzz/relay.py` client (NIP-98; kinds 9, 39000, 39002) against `http://127.0.0.1:3100` with one synthetic post and a read back; then a restart of the unit and the same read again.

## Rollback
`systemctl stop lifeos-buzz-selfhost` (volumes kept), disable the units, remove `/opt/lifeos-buzz-selfhost`; to discard all pilot data also `docker volume rm` the four `lifeos-buzz-selfhost_*` volumes and delete `/etc/lifeos-buzz-selfhost`. The existing Block-hosted communities, `lifeos-buzz-mcp`, Honcho and every `BUZZ_URL_*` setting are not touched by this pack.

## Proving membership, authentication, post and read (LIFE-870)
`sudo /opt/lifeos-buzz-mcp/current/bin/selfhost-probe` runs as `ubuntu` (like `store-credential`), reads the pilot owner and Claude agent keys from Bitwarden into memory only, and drives `lifeos/buzz/relay.py` against the loopback relay: `health`, `unauth_refused`, `non_member_refused`, `member_add` (the owner publishes kind 9030 for the agent: NIP-43, from `crates/sprout-relay/src/handlers/relay_admin.rs` at v0.3.0), `member_query_ok`, `channel_create` (kind 9007, then kind 9000 adds the agent), `post_ok`, `read_back`, `repeat_idempotent`. It prints only step names, HTTP codes, event ids and public-key prefixes, with synthetic text only.

## The pilot as a community of the governed MCP service, behind `BUZZ_RELAY` (LIFE-870, Jim comment 13867)
The first agent on the pilot is the governed MCP connector (the Buzz bridge Claude and ChatGPT use); the Hermes developer does not read Buzz, so the earlier Oracle-side Hermes sender was removed.
**One setting:** `BUZZ_RELAY=hosted|pilot` in the service's `config.env` (`lifeos/platform/relay_choice.py`). `hosted` (also empty or absent) is today's behaviour: the community name `pilot` is `community_not_authorized`. `pilot` adds the private relay as a third community (`lifeos/platform/pilot_community.py`, wired at start-up): fixed address `http://127.0.0.1:3100` (never a setting), the Claude identity's own pilot member key (`BUZZ_KEY_CLAUDE_PILOT`, a systemd credential, the pilot's `BUZZ_SELFHOST_AGENT_CLAUDE`, placed by `place-credentials`), and exactly one channel (`BUZZ_PILOT_CHANNEL`) it may post to. The two hosted communities are not touched. A user reaches `pilot` only when its `BUZZ_OAUTH_USERS` entry lists it. **Rollback:** set `BUZZ_RELAY=hosted` (or remove the line) and restart the service; the pilot relay keeps running on its own.
**Channel and membership:** `sudo /opt/lifeos-buzz-mcp/current/bin/selfhost-probe channel` has the owner add the Claude pilot member and create the one durable channel; it prints `pilot_channel <uuid>` (a public id) and nothing secret.

## One tailnet address (LIFE-927, Jim comments 13971 and 13973)
The relay answers only to its configured `RELAY_URL`, so it has one address: **`https://lifeos-edge-01.tail96b07f.ts.net:3100`** (Tailscale Serve to `127.0.0.1:3100`, tailnet only, no Funnel; Jim's tailnet policy allows `tcp:3100` from his devices to `tag:lifeos-edge`). Loopback is no longer accepted by the relay: the MCP service (`relay_choice.PILOT_URL`) and the Hermes firewall use the tailnet address. Rollback: `tailscale serve --https=3100 off`, restore the previous `lifeos-buzz-selfhost` control script (`RELAY_URL=ws://127.0.0.1:3100`) and restart the unit, revert the MCP release, restore `hermes_eval.nft.pre-life927b`.

