# Honcho on lifeos-edge-01 (LIFE-889): the one durable stack

Pinned Honcho v3.6.1, private (loopback only), authenticated (LIFE-867), every Anthropic call through a spend guard that refuses at $0.90. Jim, Nov 21 2026: "If we build it, and it works, it stays."

| Piece | Where | Notes |
|---|---|---|
| Honcho API + deriver | Docker, `ghcr.io/plastic-labs/honcho:v3.6.1` (arm64) | API on `127.0.0.1:8000` only |
| Postgres + pgvector, Redis | Docker, `pgvector/pgvector:pg15`, `redis:8.2` | no host ports; volumes `honcho_pgdata`, `honcho_redis-data` |
| Metering proxy | systemd `lifeos-honcho-meter.service`, `/opt/lifeos-honcho/meter_proxy.py` | listens on `172.28.0.1:8081` (the `honcho-net` gateway); ledger and audit in `/var/lib/lifeos-honcho-meter/`; a file named `STOP` there refuses everything |
| Anthropic key | encrypted credential `honcho_proof_anthropic_api_key` (broker store) | loaded by systemd into the proxy only; Honcho holds a dummy key |
| Embeddings | Ollama `nomic-embed-text` (768 dimensions) through the proxy's free `/v1/embeddings` route | |
| Settings | `/opt/lifeos-honcho/honcho.env` (no secrets), `/etc/lifeos-honcho/db.env` (root 0600: database password) | |

## Install (done once on Nov 21 2026; kept)
1. `sudo apt-get install -y docker.io docker-compose-v2`; `ollama pull nomic-embed-text`.
2. `docker network create --subnet 172.28.0.0/24 --gateway 172.28.0.1 honcho-net`.
3. Copy this folder to `/opt/lifeos-honcho/`; create `/etc/lifeos-honcho/db.env` with a generated `POSTGRES_PASSWORD` and the matching `DB_CONNECTION_URI`.
4. Install `lifeos-honcho-meter.service`, `systemctl enable --now` it; check `GET http://172.28.0.1:8081/health`.
5. One-time schema (embedding size 768): `docker compose run --rm --entrypoint sh api -c "/app/.venv/bin/python scripts/provision_db.py && /app/.venv/bin/python scripts/configure_embeddings.py --yes"`, then `docker compose up -d`.

## Stop spending at once
`touch /var/lib/lifeos-honcho-meter/STOP` (the proxy refuses every request; nothing else changes). Remove the file to resume. Nothing is deleted without Jim's say-so.

## Backup and scratch restore (LIFE-867)
`backup.sh` (run as root) writes a logical dump of the running database, the settings file and the image digest to `/var/backups/lifeos-honcho/<UTC stamp>/` (0700 root), keeps the 7 newest and refuses under 3 GB free; nothing is stopped. `restore_check.sh` restores a backup into a throwaway container with no network and no volume, prints the table row counts, and removes the container; it never touches `honcho_pgdata`. No timer exists: a recurring backup is a new schedule and needs Jim's approval.

## Hardening (LIFE-867)
- **Authentication on.** `AUTH_USE_AUTH=true` in `honcho.env`; the signing secret is only in `/etc/lifeos-honcho/auth.env` (root 0600, read by `api` and `deriver` through `compose.yml`). No token: 401. A workspace-scoped token reaches only its own workspace. Mint with `docker exec honcho-api-1 /app/.venv/bin/python scripts/generate_jwt.py --admin --expires 1y` (or `--workspace NAME`); the one service credential is the Bitwarden item `HONCHO_ADMIN_JWT` (project `LIFE OS EDGE/OPENCLAW`). Rollback: restore `honcho.env.pre-auth` and `compose.yml.pre-auth`, `docker compose up -d`.
- **Nightly backup.** `lifeos-honcho-backup.timer` (3:30 AM Central) runs `lifeos-honcho-backup.service` (`backup.sh`, 7 copies, 3 GB headroom check); a failure runs `lifeos-honcho-backup-failed.service`, which leaves `/var/lib/lifeos-honcho-backup/FAILED`. A backup only: it never starts an agent. Rollback: `systemctl disable --now lifeos-honcho-backup.timer`, remove the three unit files.
- **Meter fails closed.** An unreadable, missing-with-spend or unwritable ledger, or any request after the promotional credits end (STOP_AFTER, Dec 6 2026 00:00 UTC), refuses every Anthropic request (embeddings stay free; memory untouched) and leaves one `EXHAUSTED` file next to the ledger. Rollback: `meter_proxy.py.pre-867`.
- **Alerts (LIFE-876 H1).** Both signals also reach Jim's Agent Inbox through the one command `/opt/lifeos-buzz-mcp/current/bin/honcho-alert send <subject> [details] [--urgency ...]` (MCP client `honcho-alerts`, credential `/etc/lifeos-honcho/honcho_alerts_mcp.cred`, root 0600). `lifeos-honcho-backup-failed.service` writes the `FAILED` marker, then calls it ("Honcho nightly backup failed", Today). The meter never runs a command: `lifeos-honcho-meter-alert.path` watches the `EXHAUSTED` file and starts `lifeos-honcho-meter-alert.service` ("Honcho budget stopped (EXHAUSTED)", Today). Both alert calls start with `-`, so an alert failure never blocks a backup or the meter; the marker files stay as the durable signal. The same subject updates the same Inbox row. **Rollback:** `sudo systemctl disable --now lifeos-honcho-meter-alert.path`, remove the three alert lines/units, `sudo systemctl daemon-reload` (the `.pre-alert` copy of the failed unit is next to it).
