#!/bin/bash
# LIFE-47 S2 on Oracle, run as root via `opc` (never `ubuntu`): policy LOGIN LEDGER PIPELINE NODE... | credential notion|link | units DIR |
#   install COMMIT (close_release.py stages and proves the approved commit before anything live changes) | verify | rollback | uninstall.
#   auto (Edge auto-release, Jim Oct 24: main's green head, only when the Close closure changed, only while installed and serving; verify, else back to the tree that
#   was live; at most 3 attempts per commit) | auto-units (once, by Jim).
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
ExecStart=/usr/bin/python3 -I -c "import sys; sys.path.insert(0, '/opt/lifeos-close/current'); from lifeos.edge import close_gate; from lifeos.sources.close_app import handle; sys.exit(close_gate.main(handle))"
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
RuntimeMaxSec=150
# D229 (Jim, Oct 20 2026: one click, ~90 s): Build resume waits for the Oracle resume worker; only this unit's group may reach its socket
SupplementaryGroups=lifeos-resume
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
    [ "${LIFEOS_CLOSE_AUTO:-}" = 1 ] || { install -d -m 755 "$OPT"; exec 9>"$OPT/.lock"; flock -w 120 9 || { say "STOP: another release is running"; exit 1; }; }   # auto already holds it
    for f in "$ETC/policy.json" "$CREDS/lifeos-close.notion" "$CREDS/lifeos-close.link"; do [ -s "$f" ] || { say "STOP: missing $f"; exit 1; }; done
    st="$(serve_state)"; case "$st" in free|ours) ;; *) say "STOP: tailnet port $TAILNET is $st (not verified free or ours)"; exit 1;; esac
    [ "${LIFEOS_CLOSE_AUTO:-}" != 1 ] || [ "$st" = ours ] || { say "STOP: auto-release never re-adds Serve"; exit 1; }
    dest="$OPT/${commit:0:12}"; stage="$OPT/.stage"; install -d -m 755 "$OPT"; rm -rf "$stage"      # one fixed name: a run killed mid-stage is cleaned by the next
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
  auto)
    install -d -m 755 "$OPT" "$ETC"; exec 9>"$OPT/.lock"; flock -n 9 || exit 0           # one run at a time (install, rollback and uninstall take the same lock)
    probe="$OPT/.probe"; trap 'rm -rf "$probe"' EXIT
    cur="$(readlink "$OPT/current" 2>/dev/null || true)"; prev="$(readlink "$OPT/previous" 2>/dev/null || true)"
    [ -n "$cur" ] || { say "auto: no live tree; the first install is Jim's by hand"; exit 0; }
    systemctl is-enabled --quiet lifeos-close.socket && [ "$(serve_state)" = ours ] || { say "auto: the service is not installed and serving; nothing changed"; exit 0; }
    [ -e "$ETC/auto.pending" ] && [ ! -s "$ETC/auto.pending" ] && rm -f "$ETC/auto.pending"
    if read -r p_commit p_cur p_prev _ 2>/dev/null < "$ETC/auto.pending"; then             # a release this step started and never finished (cut off): finish or undo it first
      relink() { if [ -n "${2:-}" ] && [ -d "$2" ]; then ln -sfn "$2" "$OPT/$1.new" && mv -T "$OPT/$1.new" "$OPT/$1"; else rm -f "$OPT/$1"; fi; }
      if [ "$cur" = "$p_cur" ] && [ "$prev" != "${p_prev:-}" ]; then relink previous "${p_prev:-}"; fi      # cut off between the two links: the live tree's own previous is put back
      if [ "$cur" = "$OPT/${p_commit:0:12}" ] && "$0" verify && [ "$prev" != "$p_cur" ]; then relink previous "$p_cur"; fi
      if [ "$cur" = "$OPT/${p_commit:0:12}" ] && ! "$0" verify; then
        if [ -n "${p_cur:-}" ] && [ -d "$p_cur" ]; then
          ln -sfn "$p_cur" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"
          if [ -n "${p_prev:-}" ] && [ -d "$p_prev" ]; then ln -sfn "$p_prev" "$OPT/previous.new" && mv -T "$OPT/previous.new" "$OPT/previous"; else rm -f "$OPT/previous"; fi
          if "$0" verify; then say "auto: ${p_commit:0:12} unverified after a cut-off run; restored ${p_cur##*/}"; else say "auto: RESTORE VERIFY FAILED (${p_cur##*/})"; fi
        else
          say "auto: ${p_commit:0:12} is live and fails verify; no earlier tree recorded"
        fi
      fi
      rm -f "$ETC/auto.pending"; exit 0                                                   # the next tick starts from a settled tree
    fi
    commit="$(python3 -I "$REL" latest)" || { say "auto: main not green or unreadable; nothing changed"; exit 0; }
    [ "$(cat "$ETC/auto.checked" 2>/dev/null)" = "$commit" ] && exit 0                    # already decided for this commit: no re-download
    [ "$cur" = "$OPT/${commit:0:12}" ] && { echo "$commit" > "$ETC/auto.checked"; exit 0; }   # live and settled (Jim installed it, or a release finished)
    code() { tail -n +2 "$1/MANIFEST" | sha256sum | cut -d' ' -f1; }
    tried=""; n=0; read -r tried n _ 2>/dev/null < "$ETC/auto.attempts" || true
    case "$n" in ''|*[!0-9]*|???*) n=0 ;; esac                                              # anything but a small count reads as none
    [ "$tried" = "$commit" ] || n=0
    [ "$n" -ge 3 ] && exit 0                                                              # three attempts at this commit (or Jim rolled it back): wait for the next green commit
    rm -rf "$probe"
    if ! python3 -I "$REL" stage "$commit" "$probe"; then
      echo "$commit $(( n + 1 ))" > "$ETC/auto.attempts"; say "auto: ${commit:0:12} not staged (attempt $(( n + 1 )) of 3); nothing changed"; exit 1
    fi
    if [ -s "$ETC/auto.rolled" ] && grep -qx "$(code "$probe")" "$ETC/auto.rolled"; then
      echo "$commit" > "$ETC/auto.checked"; say "auto: ${commit:0:12} carries the code Jim rolled back; not released"; exit 0
    fi
    if [ "$(cat "$ETC/auto.released" 2>/dev/null)" = "$(code "$probe")" ]; then              # the code this step last released: Jim has since chosen another tree by hand
      echo "$commit" > "$ETC/auto.checked"; exit 0
    fi
    if ! python3 -I "$REL" same "$probe" "$cur"; then                                     # the commit changes the Close closure
      echo "$commit $(( n + 1 ))" > "$ETC/auto.attempts"                                   # counted before the switch, so a cut-off run still counts
      echo "$commit $cur ${prev:-}" > "$ETC/auto.pending.new" && mv -f "$ETC/auto.pending.new" "$ETC/auto.pending"   # what a cut-off run is settled against (written whole or not at all)
      if LIFEOS_CLOSE_AUTO=1 "$0" install "$commit"; then
        code "$probe" > "$ETC/auto.released"; rm -f "$ETC/auto.pending"; say "auto: released ${commit:0:12}"
      else
        if [ "$(readlink "$OPT/current")" != "$cur" ]; then                                  # verify failed after the switch: back to exactly the live tree, and its own previous tree
          ln -sfn "$cur" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"
          if [ -n "$prev" ]; then ln -sfn "$prev" "$OPT/previous.new" && mv -T "$OPT/previous.new" "$OPT/previous"; else rm -f "$OPT/previous"; fi
          if "$0" verify; then say "auto: ${commit:0:12} failed verify; restored ${cur##*/}"; else say "auto: RESTORE VERIFY FAILED (${cur##*/})"; fi
        fi
        rm -f "$ETC/auto.pending"; exit 1
      fi
    fi
    echo "$commit" > "$ETC/auto.checked"
    keep="$(readlink "$OPT/current" 2>/dev/null || true) $(readlink "$OPT/previous" 2>/dev/null || true)"
    for d in "$OPT"/[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]; do
      [ -d "$d" ] && case " $keep " in *" $d "*) ;; *) rm -rf "$d" ;; esac                # only the live tree and its previous tree are kept
    done
    exit 0 ;;
  auto-units)   # once, by Jim: a root-owned copy of these two scripts runs the timer; main can change the app, never the updater
    install -d -m 755 "$OPT/bin"; install -m 755 "$0" "$OPT/bin/install-close.sh"; install -m 644 "$REL" "$OPT/bin/close_release.py"
    cat > "$UNITS/lifeos-close-release.service" <<EOF
[Unit]
Description=LIFE OS Close service auto-release (main's green head; verify or restore)
[Service]
Type=oneshot
ExecStart=$OPT/bin/install-close.sh auto
EOF
    cat > "$UNITS/lifeos-close-release.timer" <<'EOF'
[Unit]
Description=LIFE OS Close service auto-release every 15 minutes
[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
RandomizedDelaySec=60
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload; systemctl enable --now lifeos-close-release.timer >/dev/null; say "auto-release timer enabled (every 15 minutes; journalctl -u lifeos-close-release)" ;;
  rollback)
    install -d -m 755 "$OPT"; exec 9>"$OPT/.lock"; flock -w 120 9 || { say "STOP: a release is running"; exit 1; }
    [ -L "$OPT/previous" ] || { say "FAIL: no previous tree"; exit 1; }
    rolled="$(head -1 "$OPT/current/MANIFEST" 2>/dev/null | cut -d' ' -f2)"
    if [ -n "$rolled" ] && [ "$(readlink "$OPT/current")" != "$(readlink "$OPT/previous")" ]; then   # auto-release never puts back code Jim rolled back, under any later commit
      install -d -m 755 "$ETC"; echo "$rolled 3" > "$ETC/auto.attempts"
      tail -n +2 "$OPT/current/MANIFEST" | sha256sum | cut -d' ' -f1 >> "$ETC/auto.rolled"          # a set that only grows: every rolled-back code stays out
    fi
    python3 -I "$REL" check "$(readlink "$OPT/previous")" || { say "FAIL: previous tree differs from its manifest; not activated"; exit 1; }
    ln -sfn "$(readlink "$OPT/previous")" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"; "$0" verify ;;
  uninstall)
    install -d -m 755 "$OPT"; exec 9>"$OPT/.lock"; systemctl stop lifeos-close-release.timer 2>/dev/null || true    # no new run; a running one finishes (never killed mid-switch)
    flock -w 120 9 || { say "STOP: a release is running"; exit 1; }
    bad=0; st="$(serve_state)"
    case "$st" in
      ours) tailscale serve --tls-terminated-tcp "$TAILNET" off >/dev/null || { say "FAIL: Serve $TAILNET not removed"; bad=1; } ;;
      free) ;;
      *) say "FAIL: Serve $TAILNET is $st; left untouched"; bad=1 ;;
    esac
    if systemctl list-unit-files lifeos-close-release.timer --no-legend 2>/dev/null | grep -q .; then systemctl disable --now lifeos-close-release.timer >/dev/null || { say "FAIL: auto-release timer not stopped"; bad=1; }; fi
    rm -f "$UNITS/lifeos-close-release.timer" "$UNITS/lifeos-close-release.service"
    if systemctl list-unit-files lifeos-close.socket --no-legend 2>/dev/null | grep -q .; then systemctl disable --now lifeos-close.socket >/dev/null || { say "FAIL: socket not stopped"; bad=1; }; fi
    rm -f "$UNITS/lifeos-close.socket" "$UNITS/lifeos-close@.service"; systemctl daemon-reload || bad=1
    [ "$(serve_state)" = free ] || { say "FAIL: Serve $TAILNET not verified free"; bad=1; }
    if [ "$bad" = 0 ]; then say "Removed the socket, the auto-release timer, units and Serve port $TAILNET; the tree, policy and credentials stay until removed by hand."; else say "UNINSTALL INCOMPLETE"; exit 1; fi ;;
  *) say "usage: install-close.sh policy|credential|units|install|auto|auto-units|verify|rollback|uninstall"; exit 2 ;;
esac
