# Honcho on lifeos-edge-01 (LIFE-889): the one durable stack

Pinned Honcho v3.6.1, private (loopback only), every Anthropic call through a spend guard that refuses at $0.90. Jim, Nov 21 2026: "If we build it, and it works, it stays."

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
