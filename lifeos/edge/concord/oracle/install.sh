#!/bin/bash
# LIFE-977 installer, run on Oracle as opc with sudo. It puts the read-only Concord health helper in place and swaps in the private endpoint that knows the concord1 request:
#   sudo bash install.sh install <dir>   <dir> holds concord_helper.py, alerts_endpoint.py and the two unit files
#   sudo bash install.sh rollback        put the previous endpoint back and stop the helper
# Adds no credential, token, timer or scheduler. Never touches the Gateway, Tailscale Serve, the Close service, the Buzz MCP service or any other credential. The smoke test must PASS (one call through the socket
# answers a JSON line with ok true) or the script says "CONCORD INSTALL FAILED", rolls the endpoint back, and exits non-zero.
set -euo pipefail
TREE=/opt/lifeos-concord
ENDPOINT=/home/ubuntu/.local/lib/lifeos-imessage/alerts_endpoint.py
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
say() { printf '%s\n' "$*"; }
die() { say "STOP: $*"; exit 1; }

rollback() {
  local prev
  prev=$(ls -1t "$ENDPOINT".bak-before-concord1-* 2>/dev/null | head -1 || true)
  systemctl disable --now lifeos-concord.socket >/dev/null 2>&1 || true
  [ -n "$prev" ] && install -o ubuntu -g ubuntu -m 600 "$prev" "$ENDPOINT" && say "endpoint restored from $prev"
  say "helper stopped; the tree $TREE and the unit files are left in place"
}

smoke() {
  python3 - <<'PY'
import json, socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(35)
s.connect("/run/lifeos-concord/concord.sock")
s.sendall(b"concord1\n")
s.shutdown(socket.SHUT_WR)
data = b""
while True:
    chunk = s.recv(1024)
    if not chunk:
        break
    data += chunk
doc = json.loads(data)
print("helper reply: ok=%s code=%s messages=%s" % (doc.get("ok"), doc.get("code"), doc.get("messages")))
raise SystemExit(0 if doc.get("ok") is True else 1)
PY
}

install_all() {
  local src=${1:?usage: install.sh install <dir>}
  [ "$(id -u)" -eq 0 ] || die "run with sudo"
  for f in concord_helper.py alerts_endpoint.py lifeos-concord.socket lifeos-concord@.service; do [ -f "$src/$f" ] || die "missing $src/$f"; done
  id concord >/dev/null 2>&1 || die "the concord user is missing"
  [ -x /home/concord/.npm-global/bin/concord ] || die "no concord CLI for the concord user"
  [ -f "$ENDPOINT" ] || die "no private endpoint at $ENDPOINT"
  install -d -o root -g root -m 755 "$TREE"
  install -o root -g root -m 644 "$src/concord_helper.py" "$TREE/concord_helper.py"
  install -o root -g root -m 644 "$src/lifeos-concord.socket" /etc/systemd/system/lifeos-concord.socket
  install -o root -g root -m 644 "$src/lifeos-concord@.service" /etc/systemd/system/lifeos-concord@.service
  cp -p "$ENDPOINT" "$ENDPOINT.bak-before-concord1-$STAMP"
  install -o ubuntu -g ubuntu -m 600 "$src/alerts_endpoint.py" "$ENDPOINT"
  systemctl daemon-reload
  systemctl enable --now lifeos-concord.socket
  say "installed: helper tree, units, endpoint (previous kept as .bak-before-concord1-$STAMP)"
  smoke || { say "CONCORD INSTALL FAILED"; rollback; exit 1; }
  say "CONCORD INSTALL OK"
}

case "${1:-}" in
  install) install_all "${2:-}" ;;
  rollback) [ "$(id -u)" -eq 0 ] || die "run with sudo"; rollback ;;
  *) die "usage: install.sh install <dir> | rollback" ;;
esac
