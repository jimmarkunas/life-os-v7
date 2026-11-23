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
