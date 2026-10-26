#!/bin/bash
# LIFE-37 Step 2 installer, run on Oracle as opc with sudo. It puts the Granola helper in place and swaps in the private endpoint that knows the gran1 verb:
#   sudo bash install.sh install <dir>   <dir> holds gran_helper.py, granola_contract.py (lifeos/interview/transcript.py), alerts_endpoint.py and the two unit files
#   sudo bash install.sh rollback        put the previous endpoint back and stop the helper
# Never touches the Gateway, Tailscale Serve, the Close service, the signed-in Granola session or any credential. A smoke test must PASS or the script says
# "GRAN INSTALL FAILED", rolls the endpoint back, and exits non-zero.
set -euo pipefail
TREE=/opt/lifeos-gran
ENDPOINT=/home/ubuntu/.local/lib/lifeos-imessage/alerts_endpoint.py
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
say() { printf '%s\n' "$*"; }
die() { say "STOP: $*"; exit 1; }

rollback() {
  local prev
  prev=$(ls -1t "$ENDPOINT".bak-before-gran1-* 2>/dev/null | head -1 || true)
  systemctl disable --now lifeos-gran.socket >/dev/null 2>&1 || true
  [ -n "$prev" ] && install -o ubuntu -g ubuntu -m 600 "$prev" "$ENDPOINT" && say "endpoint restored from $prev"
  say "helper stopped; the tree $TREE is left in place"
}

install_all() {
  local src=${1:?usage: install.sh install <dir>}
  [ "$(id -u)" -eq 0 ] || die "run with sudo"
  for f in gran_helper.py granola_contract.py alerts_endpoint.py lifeos-gran.socket lifeos-gran@.service; do [ -f "$src/$f" ] || die "missing $src/$f"; done
  id grans >/dev/null 2>&1 && [ -x /home/grans/bin/grans ] || die "the grans user or binary is missing"
  [ -f "$ENDPOINT" ] || die "no private endpoint at $ENDPOINT"
  install -d -o root -g root -m 755 "$TREE"
  install -o root -g root -m 644 "$src/gran_helper.py" "$TREE/gran_helper.py"
  install -o root -g root -m 644 "$src/granola_contract.py" "$TREE/granola_contract.py"
  install -o root -g root -m 644 "$src/lifeos-gran.socket" /etc/systemd/system/lifeos-gran.socket
  install -o root -g root -m 644 "$src/lifeos-gran@.service" /etc/systemd/system/lifeos-gran@.service
  cp -p "$ENDPOINT" "$ENDPOINT.bak-before-gran1-$STAMP"
  install -o ubuntu -g ubuntu -m 600 "$src/alerts_endpoint.py" "$ENDPOINT"
  systemctl daemon-reload
  systemctl enable --now lifeos-gran.socket
  say "installed: helper tree, units, endpoint (previous kept as .bak-before-gran1-$STAMP)"
  smoke || { say "GRAN INSTALL FAILED"; rollback; exit 1; }
  say "GRAN INSTALL OK"
}

smoke() {   # ubuntu (the endpoint's user) asks the helper to list: proves the socket permissions and that the helper runs as grans. Counts only.
  sudo -u ubuntu python3 -I - <<'PY'
import json, socket, sys
body = json.dumps({"op": "list", "nonce": "smoke"}).encode()
s = socket.socket(socket.AF_UNIX); s.settimeout(30); s.connect("/run/lifeos-gran/gran.sock")
s.sendall(b"gran1 %d\n" % len(body) + body); s.shutdown(socket.SHUT_WR)
data = b""
while True:
    c = s.recv(1 << 20)
    if not c:
        break
    data += c
head, _, rest = data.partition(b"\n")
reply = json.loads(rest)
ok = head == b"gran1 %d" % len(rest) and reply.get("ok") is True and reply.get("nonce") == "smoke"
print("  [%s] helper answers ubuntu over the socket: meetings=%s" % ("PASS" if ok else "FAIL", len(reply.get("meetings", []))))
sys.exit(0 if ok else 1)
PY
  local who
  who=$(systemctl show lifeos-gran.socket -p SocketUser 2>/dev/null | cut -d= -f2 || true)
  say "  [INFO] socket user: ${who:-grans}"
}

case "${1:-}" in
  install) install_all "${2:-}" ;;
  rollback) [ "$(id -u)" -eq 0 ] || die "run with sudo"; rollback ;;
  *) die "usage: install.sh install <dir> | rollback" ;;
esac
