#!/usr/bin/env bash
# LIFE-637: start tailscaled (state in memory) and join as an ephemeral, pre-authorized node. Counts and fixed words only; the OAuth secret is masked before anything else and never echoed.
# Test hooks (env): TS_SUDO (default sudo; empty in tests), TS_SOCKET, TS_HOSTNAME, TS_RETRY_PAUSE, TS_READY_SECONDS.
set -euo pipefail
secret="${TS_OAUTH_SECRET:?the OAuth secret is required}"
tags="${TS_TAGS:?the tag is required}"
echo "::add-mask::$secret"
run=()
if [ -n "${TS_SUDO-sudo}" ]; then run=("${TS_SUDO-sudo}" -E); fi
limit=()
if command -v timeout >/dev/null 2>&1; then limit=(timeout 120); fi
socket="${TS_SOCKET:-/var/run/tailscale/tailscaled.sock}"
host="${TS_HOSTNAME:-github-$(hostname)}"
host="${host:0:63}"

nohup ${run[@]+"${run[@]}"} tailscaled --state=mem: > "$HOME/tailscaled.log" 2>&1 &
disown || true
waited=0
until [ -S "$socket" ]; do
  waited=$((waited + 1))
  if [ "$waited" -gt "${TS_READY_SECONDS:-30}" ]; then echo "::error::tailscale join: the daemon did not come up"; exit 1; fi
  sleep 1
done

attempt=0
while :; do
  attempt=$((attempt + 1))
  if ${limit[@]+"${limit[@]}"} ${run[@]+"${run[@]}"} tailscale up --advertise-tags="$tags" --hostname="$host" --accept-routes --authkey="${secret}?preauthorized=true&ephemeral=true" >/dev/null 2>&1; then
    break
  fi
  echo "tailscale join: attempt $attempt failed"
  if [ "$attempt" -ge 5 ]; then echo "::error::tailscale join: no join after 5 attempts"; exit 1; fi
  sleep "${TS_RETRY_PAUSE:-$((attempt * 2))}"
done

if ! ${run[@]+"${run[@]}"} tailscale status --json 2>/dev/null | grep -q '"BackendState": *"Running"'; then
  echo "::error::tailscale join: joined but the backend is not Running"
  exit 1
fi
echo "tailscale join: running as an ephemeral node with the requested tag"
