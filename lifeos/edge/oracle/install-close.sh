#!/bin/bash
# LIFE-47 S2 on Oracle: the private Close Opportunity service (Edge package). Run as root through the `opc` administrator; never as `ubuntu`.
#   install-close.sh policy LOGIN NODE_ID...   write /etc/lifeos-close/policy.json (exact login, approved untagged node StableIDs, never this node)
#   install-close.sh credential notion|link    encrypt one credential for the unit (notion: read from stdin, e.g. piped from Bitwarden; link: generated)
#   install-close.sh units DIR                 write the two unit files into DIR (used by install and by `systemd-analyze verify`)
#   install-close.sh install TARBALL SHA256    verified, read-only, commit-pinned tree under /opt/lifeos-close, units, socket, ONE Serve port
#   install-close.sh verify | rollback | uninstall
# Never touches the Gateway (443/18789), the iMessage endpoint (47341/47342), Funnel or any other Serve port, and never runs `tailscale serve reset`.
set -euo pipefail
OPT=/opt/lifeos-close; ETC=/etc/lifeos-close; CREDS=/etc/credstore.encrypted; UNITS=/etc/systemd/system
LOCAL=47344; TAILNET=47443
say() { printf '%s\n' "$*"; }
[ "$(id -u)" = 0 ] || { say "run as root via opc"; exit 2; }

serve_state() {   # ours | free | other
  tailscale serve status --json 2>/dev/null | python3 -I -c '
import json, sys
cfg = json.loads(sys.stdin.read() or "{}") or {}
h = (cfg.get("TCP") or {}).get(sys.argv[1])
funnel = any(v and str(k).endswith(":" + sys.argv[1]) for k, v in (cfg.get("AllowFunnel") or {}).items())
ok = bool(h) and h.get("TCPForward") == "127.0.0.1:" + sys.argv[2] and h.get("ProxyProtocol") == 2 and bool(h.get("TerminateTLS"))
print("free" if h is None and not funnel else "ours" if ok and not funnel else "other")' "$TAILNET" "$LOCAL"
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
    shift; login="${1:?login}"; shift; [ "$#" -ge 1 ] || { say "at least one node id"; exit 2; }
    install -d -m 755 "$ETC"
    tailscale status --json | python3 -I -c '
import json, sys
st, port, login, want = json.load(sys.stdin), sys.argv[1], sys.argv[2], sys.argv[3:]
users = {str(k): v.get("LoginName") for k, v in (st.get("User") or {}).items()}
peers = {p.get("ID"): p for p in (st.get("Peer") or {}).values()}
for n in want:
    p = peers.get(n)
    if n == st["Self"].get("ID") or p is None or p.get("Tags") or users.get(str(p.get("UserID"))) != login:
        sys.exit("refused: " + n + " is this node, unknown, tagged or not owned by " + login)
print(json.dumps({"host": st["Self"]["DNSName"].rstrip(".") + ":" + port, "login": login, "nodes": want}))' "$TAILNET" "$login" "$@" > "$ETC/policy.json.new"
    chmod 644 "$ETC/policy.json.new"; mv "$ETC/policy.json.new" "$ETC/policy.json"; say "policy written for $# node(s)" ;;
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
    tar="${2:?tarball}"; want="${3:?sha256}"
    [ "$(sha256sum "$tar" | cut -d' ' -f1)" = "$want" ] || { say "STOP: tarball hash mismatch"; exit 1; }
    for f in "$ETC/policy.json" "$CREDS/lifeos-close.notion" "$CREDS/lifeos-close.link"; do [ -s "$f" ] || { say "STOP: missing $f"; exit 1; }; done
    case "$(serve_state)" in free|ours) ;; *) say "STOP: tailnet port $TAILNET has another Serve/Funnel configuration"; exit 1;; esac
    dest="$OPT/${want:0:12}"; install -d -m 755 "$dest"; tar -xzf "$tar" -C "$dest" --no-same-owner
    (cd "$dest" && find . -type f ! -name MANIFEST -exec sha256sum {} + | sort -k2 > MANIFEST)
    chown -R root:root "$dest"; chmod -R go-w,a-w+rX "$dest"
    [ -L "$OPT/current" ] && ln -sfn "$(readlink "$OPT/current")" "$OPT/previous"
    ln -sfn "$dest" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"
    units "$UNITS"; systemctl daemon-reload; systemctl enable --now lifeos-close.socket >/dev/null
    [ "$(serve_state)" = ours ] || tailscale serve --bg --tls-terminated-tcp "$TAILNET" --proxy-protocol 2 "tcp://127.0.0.1:$LOCAL" >/dev/null
    "$0" verify ;;
  verify)
    ok=0
    check() { if eval "$2"; then say "  [PASS] $1"; else say "  [FAIL] $1"; ok=1; fi; }
    check "tree matches its manifest" '(cd "$OPT/current" && sha256sum --quiet -c MANIFEST)'
    check "tree root-owned and read-only" '[ -z "$(find -L "$OPT/current" ! -user root -o -perm /022 2>/dev/null)" ]'
    check "credentials root-only" '[ "$(stat -c %a:%U "$CREDS/lifeos-close.notion" "$CREDS/lifeos-close.link" | sort -u)" = "600:root" ]'
    check "ubuntu cannot read the credentials" '! runuser -u ubuntu -- cat "$CREDS/lifeos-close.notion" >/dev/null 2>&1'
    check "ubuntu has no sudo" '! runuser -u ubuntu -- sudo -n true >/dev/null 2>&1'
    check "socket active" 'systemctl is-active --quiet lifeos-close.socket'
    check "listener on 127.0.0.1 only" '[ "$(ss -Hltn "sport = :$LOCAL" | awk "{print \$4}" | sort -u)" = "127.0.0.1:$LOCAL" ]'
    check "Serve terminates TLS on $TAILNET with PROXY v2, no Funnel" '[ "$(serve_state)" = ours ]'
    exit $ok ;;
  rollback)
    [ -L "$OPT/previous" ] || { say "no previous tree"; exit 1; }
    ln -sfn "$(readlink "$OPT/previous")" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"; "$0" verify ;;
  uninstall)
    if [ "$(serve_state)" = ours ]; then tailscale serve --tls-terminated-tcp "$TAILNET" off >/dev/null || true; fi
    systemctl disable --now lifeos-close.socket >/dev/null 2>&1 || true
    rm -f "$UNITS/lifeos-close.socket" "$UNITS/lifeos-close@.service"; systemctl daemon-reload
    say "Removed the socket, units and Serve port $TAILNET; the tree, policy and credentials stay until removed by hand." ;;
  *) say "usage: install-close.sh policy|credential|units|install|verify|rollback|uninstall"; exit 2 ;;
esac
