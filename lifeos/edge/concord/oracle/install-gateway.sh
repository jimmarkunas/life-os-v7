#!/bin/bash
# LIFE-987 installer, run on Oracle as opc with sudo (Jim, comments 14334 and 14344: one systemd unit and its socket for the Concord gateway as `concord`, a socket group for `buzz-mcp`, and AF_UNIX on the bridge unit
# with the other host sockets blocked). Four commands, each with its own rollback, nothing else touched (no credential, no timer, no scheduler, not the Gateway, Tailscale Serve, Close, the Honcho meter or any other service):
#   sudo bash install-gateway.sh install <dir>        <dir> holds gateway.py, lifeos-concord-gateway.socket and lifeos-concord-gateway@.service
#   sudo bash install-gateway.sh rollback             stop and disable the gateway socket, delete the two unit files and /opt/lifeos-concord/gateway.py
#   sudo bash install-gateway.sh bridge-install <dir> <dir> holds orchestrator-socket.conf (the drop-in); adds AF_UNIX to lifeos-buzz-mcp and restarts it
#   sudo bash install-gateway.sh bridge-rollback      delete the drop-in, daemon-reload, restart lifeos-buzz-mcp (the unit goes back to AF_INET AF_INET6 only)
# Record the rollback command in the ticket BEFORE running an install. The smoke test of `install` must PASS (one agent_status request through the socket answers a JSON line with an `ok` field and no bad_frame) or
# the script says "GATEWAY INSTALL FAILED", rolls back and exits non-zero. `bridge-install` must leave lifeos-buzz-mcp active or it rolls the drop-in back.
set -euo pipefail
TREE=/opt/lifeos-concord
UNITS=/etc/systemd/system
DROPIN_DIR=$UNITS/lifeos-buzz-mcp.service.d
SOCK=/run/lifeos-concord-gateway/orchestrator.sock
say() { printf '%s\n' "$*"; }
die() { say "STOP: $*"; exit 1; }

rollback() {
  systemctl disable --now lifeos-concord-gateway.socket >/dev/null 2>&1 || true
  rm -f "$UNITS/lifeos-concord-gateway.socket" "$UNITS/lifeos-concord-gateway@.service" "$TREE/gateway.py"
  systemctl daemon-reload
  say "gateway stopped; its two unit files and $TREE/gateway.py are removed (the LIFE-977 helper in $TREE is untouched)"
}

bridge_rollback() {
  rm -f "$DROPIN_DIR/orchestrator-socket.conf"
  rmdir "$DROPIN_DIR" 2>/dev/null || true
  systemctl daemon-reload
  systemctl restart lifeos-buzz-mcp
  say "bridge drop-in removed; lifeos-buzz-mcp restarted with its original address families"
}

smoke() {
  python3 - <<'PY'
import json, socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(35)
s.connect("/run/lifeos-concord-gateway/orchestrator.sock")
s.sendall(b'{"args":{},"client":"claude","op":"agent_status"}\n')
s.shutdown(socket.SHUT_WR)
data = b""
while True:
    chunk = s.recv(4096)
    if not chunk:
        break
    data += chunk
doc = json.loads(data)
print("gateway reply: ok=%s reason=%s" % (doc.get("ok"), doc.get("reason")))
raise SystemExit(0 if isinstance(doc.get("ok"), bool) and doc.get("reason") not in ("bad_frame", "bad_request") else 1)
PY
}

install_gateway() {
  local src=${1:?usage: install-gateway.sh install <dir>}
  [ "$(id -u)" -eq 0 ] || die "run with sudo"
  for f in gateway.py lifeos-concord-gateway.socket lifeos-concord-gateway@.service; do [ -f "$src/$f" ] || die "missing $src/$f"; done
  id concord >/dev/null 2>&1 || die "the concord user is missing"
  getent group buzz-mcp >/dev/null || die "the buzz-mcp group is missing"
  [ -x /home/concord/.npm-global/bin/concord-mcp ] || die "no concord-mcp for the concord user"
  install -d -o root -g root -m 755 "$TREE"
  install -o root -g root -m 644 "$src/gateway.py" "$TREE/gateway.py"
  install -o root -g root -m 644 "$src/lifeos-concord-gateway.socket" "$UNITS/lifeos-concord-gateway.socket"
  install -o root -g root -m 644 "$src/lifeos-concord-gateway@.service" "$UNITS/lifeos-concord-gateway@.service"
  systemctl daemon-reload
  systemctl enable --now lifeos-concord-gateway.socket
  say "installed: gateway program, socket and service (rollback: sudo bash install-gateway.sh rollback)"
  smoke || { say "GATEWAY INSTALL FAILED"; rollback; exit 1; }
  say "GATEWAY INSTALL OK"
}

bridge_install() {
  local src=${1:?usage: install-gateway.sh bridge-install <dir>}
  [ "$(id -u)" -eq 0 ] || die "run with sudo"
  [ -f "$src/orchestrator-socket.conf" ] || die "missing $src/orchestrator-socket.conf"
  [ -S "$SOCK" ] || die "the gateway socket is not there; run install first"
  install -d -o root -g root -m 755 "$DROPIN_DIR"
  install -o root -g root -m 644 "$src/orchestrator-socket.conf" "$DROPIN_DIR/orchestrator-socket.conf"
  systemctl daemon-reload
  systemctl restart lifeos-buzz-mcp
  sleep 3
  systemctl is-active --quiet lifeos-buzz-mcp || { say "BRIDGE INSTALL FAILED: the bridge is not active"; bridge_rollback; exit 1; }
  say "BRIDGE INSTALL OK (rollback: sudo bash install-gateway.sh bridge-rollback)"
}

case "${1:-}" in
  install) install_gateway "${2:-}" ;;
  rollback) [ "$(id -u)" -eq 0 ] || die "run with sudo"; rollback ;;
  bridge-install) bridge_install "${2:-}" ;;
  bridge-rollback) [ "$(id -u)" -eq 0 ] || die "run with sudo"; bridge_rollback ;;
  *) die "usage: install-gateway.sh install <dir> | rollback | bridge-install <dir> | bridge-rollback" ;;
esac
