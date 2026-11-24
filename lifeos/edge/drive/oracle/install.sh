#!/bin/bash
# LIFE-945 installer, run on Oracle as opc with sudo. It puts the read-only Drive weekly-count helper in place and swaps in the private endpoint that knows the drive1 request:
#   sudo bash install.sh install <dir>   <dir> holds drive_helper.py, drive_week.py, google_read.py, alerts_endpoint.py and the two unit files
#   sudo bash install.sh rollback        put the previous endpoint back and stop the helper
# Adds no credential: the unit loads the three existing encrypted Drive files. Never touches the Gateway, Tailscale Serve, the Close service, the Buzz MCP service or any other credential. The smoke test
# must PASS (one call through the socket answers a JSON line) or the script says "DRIVE INSTALL FAILED", rolls the endpoint back, and exits non-zero.
set -euo pipefail
TREE=/opt/lifeos-drive
ENDPOINT=/home/ubuntu/.local/lib/lifeos-imessage/alerts_endpoint.py
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
say() { printf '%s\n' "$*"; }
die() { say "STOP: $*"; exit 1; }

rollback() {
  local prev
  prev=$(ls -1t "$ENDPOINT".bak-before-drive1-* 2>/dev/null | head -1 || true)
  systemctl disable --now lifeos-drive.socket >/dev/null 2>&1 || true
  [ -n "$prev" ] && install -o ubuntu -g ubuntu -m 600 "$prev" "$ENDPOINT" && say "endpoint restored from $prev"
  say "helper stopped; the tree $TREE is left in place"
}

smoke() {
  python3 - <<'PY'
import json, socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(50)
s.connect("/run/lifeos-drive/drive.sock")
s.sendall(b"drive1 3\n")
s.shutdown(socket.SHUT_WR)
data = b""
while True:
    chunk = s.recv(4096)
    if not chunk:
        break
    data += chunk
doc = json.loads(data)
print("helper reply: ok=%s code=%s counts=%s" % (doc.get("ok"), doc.get("code"), {k: doc[k] for k in ("new", "changed", "unfiled", "duplicates") if k in doc}))
raise SystemExit(0 if doc.get("ok") is True else 1)
PY
}

install_all() {
  local src=${1:?usage: install.sh install <dir>}
  [ "$(id -u)" -eq 0 ] || die "run with sudo"
  for f in drive_helper.py drive_week.py google_read.py alerts_endpoint.py lifeos-drive.socket lifeos-drive@.service; do [ -f "$src/$f" ] || die "missing $src/$f"; done
  id buzz-mcp >/dev/null 2>&1 || die "the buzz-mcp user is missing"
  for c in google_read_client_id google_read_client_secret google_read_personal_drive_refresh; do [ -f "/etc/lifeos-buzz-mcp/$c.cred" ] || die "missing credential file $c"; done
  [ -f "$ENDPOINT" ] || die "no private endpoint at $ENDPOINT"
  install -d -o root -g root -m 755 "$TREE"
  for f in drive_helper.py drive_week.py google_read.py; do install -o root -g root -m 644 "$src/$f" "$TREE/$f"; done
  install -o root -g root -m 644 "$src/lifeos-drive.socket" /etc/systemd/system/lifeos-drive.socket
  install -o root -g root -m 644 "$src/lifeos-drive@.service" /etc/systemd/system/lifeos-drive@.service
  cp -p "$ENDPOINT" "$ENDPOINT.bak-before-drive1-$STAMP"
  install -o ubuntu -g ubuntu -m 600 "$src/alerts_endpoint.py" "$ENDPOINT"
  systemctl daemon-reload
  systemctl enable --now lifeos-drive.socket
  say "installed: helper tree, units, endpoint (previous kept as .bak-before-drive1-$STAMP)"
  smoke || { say "DRIVE INSTALL FAILED"; rollback; exit 1; }
  say "DRIVE INSTALL OK"
}

case "${1:-}" in
  install) install_all "${2:-}" ;;
  rollback) [ "$(id -u)" -eq 0 ] || die "run with sudo"; rollback ;;
  *) die "usage: install.sh install <dir> | rollback" ;;
esac
