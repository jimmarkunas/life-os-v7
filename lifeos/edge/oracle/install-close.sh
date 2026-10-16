#!/bin/bash
# LIFE-47 S2 on Oracle, run as root via `opc` (never `ubuntu`): policy LOGIN LEDGER PIPELINE NODE... | credential notion|link | units DIR |
#   install COMMIT (close_release.py stages and proves the approved commit before anything live changes) | verify | rollback | uninstall.
# Only port 47443 and its own units/tree are touched: never the Gateway (443/18789), iMessage (47341/47342), Funnel, or `tailscale serve reset`.
set -euo pipefail
OPT=/opt/lifeos-close; ETC=/etc/lifeos-close; CREDS=/etc/credstore.encrypted; UNITS=/etc/systemd/system
LOCAL=47344; TAILNET=47443; REL="$(cd "$(dirname "$0")" && pwd)/close_release.py"
say() { printf '%s\n' "$*"; }
[ "$(id -u)" = 0 ] || { say "run as root via opc"; exit 2; }

serve_state() {   # ours | free | other | unknown: a failed, empty or malformed status read is unknown, never free
  local status; status="$(tailscale serve status --json 2>/dev/null)" || status=""
  printf '%s' "$status" | python3 -I "$REL" serve-state || echo unknown
}

units() {
  install -d -m 755 "$1"
  cat > "$1/lifeos-close.socket" <<EOF
[Unit]
Description=LIFE OS Close Opportunity (LIFE-47 S2), loopback only; Tailscale Serve $TAILNET forwards here
[Socket]
ListenStream=127.0.0.1:$LOCAL
Accept=yes
MaxConnections=8
[Install]
WantedBy=sockets.target
EOF
  cat > "$1/lifeos-close@.service" <<'EOF'
[Unit]
Description=LIFE OS Close Opportunity (one connection)
[Service]
ExecStart=/usr/bin/python3 -I -c "import sys; sys.path.insert(0, '/opt/lifeos-close/current'); from lifeos.edge import close_gate; from lifeos.hiring.close_app import handle; sys.exit(close_gate.main(handle))"
StandardInput=socket
StandardOutput=socket
StandardError=null
DynamicUser=yes
LoadCredentialEncrypted=notion:/etc/credstore.encrypted/lifeos-close.notion
LoadCredentialEncrypted=link:/etc/credstore.encrypted/lifeos-close.link
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectProc=invisible
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
SystemCallFilter=@system-service
SystemCallArchitectures=native
RuntimeMaxSec=30
MemoryMax=128M
TasksMax=16
CPUQuota=50%
EOF
}

case "${1:-}" in
  policy)
    shift; [ "$#" -ge 4 ] || { say "policy LOGIN LEDGER_DATA_SOURCE_ID PIPELINE_PAGE_ID NODE_ID..."; exit 2; }
    install -d -m 755 "$ETC"
    tailscale status --json | python3 -I "$REL" policy "$TAILNET" "$@" > "$ETC/policy.json.new" || { rm -f "$ETC/policy.json.new"; say "STOP: policy refused"; exit 1; }
    chmod 644 "$ETC/policy.json.new"; mv "$ETC/policy.json.new" "$ETC/policy.json"; say "policy written for $(( $# - 3 )) node(s)" ;;
  credential)
    install -d -m 700 "$CREDS"
    case "${2:-}" in
      notion) systemd-creds encrypt --name=notion - "$CREDS/lifeos-close.notion" ;;
      link) python3 -I -c 'import secrets; print(secrets.token_hex(32))' | systemd-creds encrypt --name=link - "$CREDS/lifeos-close.link" ;;
      *) say "credential notion|link"; exit 2 ;;
    esac
    chmod 600 "$CREDS/lifeos-close.${2}"; say "encrypted credential ${2} stored (value not shown)" ;;
  units) units "${2:?dir}" ;;
  install)
    commit="${2:?approved full commit sha}"
    for f in "$ETC/policy.json" "$CREDS/lifeos-close.notion" "$CREDS/lifeos-close.link"; do [ -s "$f" ] || { say "STOP: missing $f"; exit 1; }; done
    st="$(serve_state)"; case "$st" in free|ours) ;; *) say "STOP: tailnet port $TAILNET is $st (not verified free or ours)"; exit 1;; esac
    dest="$OPT/${commit:0:12}"; stage="$OPT/.stage-$$"; install -d -m 755 "$OPT"; rm -rf "$stage"
    python3 -I "$REL" stage "$commit" "$stage" || { rm -rf "$stage"; say "STOP: release not verified; nothing changed"; exit 1; }
    chown -R root:root "$stage"; chmod -R go-w,a-w+rX "$stage"
    python3 -I "$REL" check "$stage" || { rm -rf "$stage"; say "STOP: staged tree differs; nothing changed"; exit 1; }
    if [ -e "$dest" ]; then python3 -I "$REL" check "$dest" || { rm -rf "$stage"; say "STOP: existing $dest differs"; exit 1; }; rm -rf "$stage"; else mv -T "$stage" "$dest"; fi
    [ -L "$OPT/current" ] && [ "$(readlink "$OPT/current")" != "$dest" ] && ln -sfn "$(readlink "$OPT/current")" "$OPT/previous"
    ln -sfn "$dest" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"
    units "$UNITS"; systemctl daemon-reload; systemctl enable --now lifeos-close.socket >/dev/null
    [ "$st" = ours ] || tailscale serve --bg --tls-terminated-tcp "$TAILNET" --proxy-protocol 2 "tcp://127.0.0.1:$LOCAL" >/dev/null || { say "FAIL: Serve $TAILNET not added (run uninstall)"; exit 1; }
    "$0" verify ;;
  verify)
    ok=0
    check() { if eval "$2"; then say "  [PASS] $1"; else say "  [FAIL] $1"; ok=1; fi; }
    check "tree is exactly its approved-commit manifest" 'python3 -I "$REL" check "$OPT/current" >/dev/null 2>&1'
    check "tree root-owned and read-only" '[ -z "$(find -L "$OPT/current" ! -user root -o -perm /022 2>/dev/null)" ]'
    check "credentials root-only" '[ "$(stat -c %a:%U "$CREDS/lifeos-close.notion" "$CREDS/lifeos-close.link" | sort -u)" = "600:root" ]'
    check "ubuntu cannot read the credentials" '! runuser -u ubuntu -- cat "$CREDS/lifeos-close.notion" >/dev/null 2>&1'
    check "ubuntu has no sudo" '! runuser -u ubuntu -- sudo -n true >/dev/null 2>&1'
    check "socket active" 'systemctl is-active --quiet lifeos-close.socket'
    check "listener on 127.0.0.1 only" '[ "$(ss -Hltn "sport = :$LOCAL" | awk "{print \$4}" | sort -u)" = "127.0.0.1:$LOCAL" ]'
    check "Serve terminates TLS on $TAILNET with PROXY v2, no Funnel" '[ "$(serve_state)" = ours ]'
    exit $ok ;;
  rollback)
    [ -L "$OPT/previous" ] || { say "FAIL: no previous tree"; exit 1; }
    python3 -I "$REL" check "$(readlink "$OPT/previous")" || { say "FAIL: previous tree differs from its manifest; not activated"; exit 1; }
    ln -sfn "$(readlink "$OPT/previous")" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"; "$0" verify ;;
  uninstall)
    bad=0; st="$(serve_state)"
    case "$st" in
      ours) tailscale serve --tls-terminated-tcp "$TAILNET" off >/dev/null || { say "FAIL: Serve $TAILNET not removed"; bad=1; } ;;
      free) ;;
      *) say "FAIL: Serve $TAILNET is $st; left untouched"; bad=1 ;;
    esac
    if systemctl list-unit-files lifeos-close.socket --no-legend 2>/dev/null | grep -q .; then systemctl disable --now lifeos-close.socket >/dev/null || { say "FAIL: socket not stopped"; bad=1; }; fi
    rm -f "$UNITS/lifeos-close.socket" "$UNITS/lifeos-close@.service"; systemctl daemon-reload || bad=1
    [ "$(serve_state)" = free ] || { say "FAIL: Serve $TAILNET not verified free"; bad=1; }
    if [ "$bad" = 0 ]; then say "Removed the socket, units and Serve port $TAILNET; the tree, policy and credentials stay until removed by hand."; else say "UNINSTALL INCOMPLETE"; exit 1; fi ;;
  *) say "usage: install-close.sh policy|credential|units|install|verify|rollback|uninstall"; exit 2 ;;
esac
