# Concord on Oracle: one shared store for Mac and Oracle agents (LIFE-872)

Concord (`@concord-ai/concord-mcp` 0.20.0, MIT, telemetry off) has no server mode, so the shared store is one SQLite file on Oracle and every agent launches `concord-mcp` there: Mac clients over `ssh`, Oracle agents locally. Everything runs as the OS account `concord` (no sudo, no vault access, no secrets). No Cloud, no port, nothing public. Jim approved the host steps on LIFE-872 comment 13868 (Nov 23, 2026).

## What is on the host

| Item | Where |
| --- | --- |
| Account | `concord` (nologin shell, home mode 750) |
| Node.js 22 (apt, NodeSource `node_22.x`, key `6F71 F525 2828 41EE DAF8 51B4 2F59 B5F9 9B1B E0B4`) | `/etc/apt/sources.list.d/nodesource-life872.list`, `/usr/share/keyrings/nodesource.gpg` |
| Package, pinned | `/home/concord/.npm-global/bin/concord-mcp` (0.20.0; newer releases exist and are not adopted) |
| Repo root and store | `/home/concord/life-os-v7` (`.concord/concord.db`, SQLite WAL, `busy_timeout` 5000; `.concord/` is in `.git/info/exclude`) |

The checkout is pulled, never edited by hand: `sh tools/concord_oracle_pull.sh` (Oracle has no GitHub credential, so `origin/main` travels as a git bundle over the existing ssh).

## Clients

* **Mac OC and MC:** the `concord-oracle` entry in `.mcp.json` runs `ssh opc@lifeos-edge-01 sudo -u concord env ... concord-mcp`. It is a second server beside the unchanged local `concord`, so rolling back is deleting one entry.
* **Oracle Claude Code, Hermes, OpenClaw:** run the same `concord-mcp` binary as `concord` with the same environment (`HOME=/home/concord`, `CONCORD_REPO_ROOT=/home/concord/life-os-v7`, `CONCORD_TELEMETRY_DISABLED=1`).

## Rules the proof showed

1. **Name yourself.** A process started over ssh cannot see the agent's session, so `transfer_work` fails with "Concord cannot tell which session you are" unless the call carries `agent_id` (the id the session start hook prints) or the launch sets `CONCORD_AGENT_ID`. Pass `agent_id` on every call to `concord-oracle`. When `CONCORD_AGENT_ID` is set it wins over the argument.
2. **Declare claim paths repo-relative** (`docs/x.md`). Concord only normalizes the text; a Mac absolute path never matches an Oracle one.
3. **Offline is reported, not hidden.** A live prompt to an agent that is not reachable returns `status: failed, error_code: target_unreachable`; to a registered agent with no live session it returns `sent` with `delivery: queued_pull`, `delivered_at: null` and an expiry. A handoff offer stays `deliveryStatus: pending` until the receiver accepts.
4. **Drops are visible.** ssh uses `ServerAliveInterval 15` and `ServerAliveCountMax 2`; when the link dies the server exits and the client shows the tools as failed. Nothing falls back to local-only by itself, and a claim held by a dropped agent stays until it is finished or released.
5. Jira stays the authoritative claim for work items. Concord adds file-level overlap and handoff evidence.

## Rollback

Remove the `concord-oracle` entry from `.mcp.json` (and any per-agent copy). On Oracle: `sudo userdel -r concord`, `sudo apt-get remove -y nodejs`, remove `/etc/apt/sources.list.d/nodesource-life872.list` and `/usr/share/keyrings/nodesource.gpg`. Nothing else on the host was touched.
