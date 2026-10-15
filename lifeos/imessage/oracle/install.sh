#!/bin/bash
# COMM-1.4b on Oracle: install, verify or remove the private iMessage alert-metadata endpoint (approved in LIFE-31, Oct 13 2026).
#   bash install.sh install    copy the endpoint and its two user units, create the event key once, enable the loopback socket, add ONE Serve TCP port
#   bash install.sh verify     print fixed checks only
#   bash install.sh uninstall  remove exactly what install added (the event key is kept unless --purge-key)
# Never touches the OpenClaw Gateway, its allowlist or denies, Luna, the Mac, Funnel or any other Serve port.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LIB="$HOME/.local/lib/lifeos-imessage"
UNITS="$HOME/.config/systemd/user"
KEY="$HOME/.config/openclaw-imessage/event-key"
LOCAL_PORT=47342
TAILNET_PORT=47341
say() { printf '%s\n' "$*"; }

serve_state() {   # ours | free | other
  sudo -n tailscale serve status --json 2>/dev/null | python3 -I -c '
import json, sys
cfg = json.loads(sys.stdin.read() or "{}") or {}
h = (cfg.get("TCP") or {}).get(sys.argv[1])
funnel = any(v and str(k).endswith(":" + sys.argv[1]) for k, v in (cfg.get("AllowFunnel") or {}).items())
print("free" if h is None and not funnel else "ours" if h == {"TCPForward": "127.0.0.1:" + sys.argv[2], "ProxyProtocol": 2} and not funnel else "other")' \
    "$TAILNET_PORT" "$LOCAL_PORT"
}

case "${1:-}" in
  install)
    case "$(serve_state)" in free|ours) ;; *) say "STOP: tailnet port $TAILNET_PORT has another Serve/Funnel configuration"; exit 1;; esac
    install -d -m 700 "$LIB" "$(dirname "$KEY")"
    install -d -m 755 "$UNITS"
    install -m 644 "$HERE/alerts_endpoint.py" "$LIB/alerts_endpoint.py"
    install -m 644 "$HERE/oracle/lifeos-imessage-alerts.socket" "$HERE/oracle/lifeos-imessage-alerts@.service" "$UNITS/"
    [ -s "$KEY" ] || (umask 077; python3 -I -c 'import secrets; print(secrets.token_hex(32))' > "$KEY")
    systemctl --user daemon-reload
    systemctl --user enable --now lifeos-imessage-alerts.socket >/dev/null
    [ "$(serve_state)" = ours ] || sudo -n tailscale serve --bg --tcp "$TAILNET_PORT" --proxy-protocol 2 "tcp://127.0.0.1:$LOCAL_PORT" >/dev/null
    "$0" verify ;;
  verify)
    ok=0
    check() { if eval "$2"; then say "  [PASS] $1"; else say "  [FAIL] $1"; ok=1; fi; }
    check "endpoint installed" '[ -f "$LIB/alerts_endpoint.py" ]'
    check "event key present, 0600" '[ "$(stat -c %a "$KEY")" = 600 ]'
    check "socket active" 'systemctl --user is-active --quiet lifeos-imessage-alerts.socket'
    check "listener on 127.0.0.1 only" '[ "$(ss -Hltn "sport = :$LOCAL_PORT" | awk "{print \$4}" | sort -u)" = "127.0.0.1:$LOCAL_PORT" ]'
    check "Serve forwards tailnet $TAILNET_PORT with PROXY v2, no Funnel" '[ "$(serve_state)" = ours ]'
    exit $ok ;;
  uninstall)
    if [ "$(serve_state)" = ours ]; then sudo -n tailscale serve --tcp "$TAILNET_PORT" off >/dev/null || true; fi
    systemctl --user disable --now lifeos-imessage-alerts.socket >/dev/null 2>&1 || true
    rm -f "$UNITS/lifeos-imessage-alerts.socket" "$UNITS/lifeos-imessage-alerts@.service" "$LIB/alerts_endpoint.py"
    systemctl --user daemon-reload
    if [ "${2:-}" = "--purge-key" ]; then rm -f "$KEY"; fi
    say "Removed the endpoint, its units and its Serve port." ;;
  *) say "usage: bash install.sh install | verify | uninstall [--purge-key]"; exit 2 ;;
esac
