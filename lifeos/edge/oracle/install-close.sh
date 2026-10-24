#!/bin/bash
# LIFE-47 S2 on Oracle, run as root via `opc` (never `ubuntu`): policy LOGIN LEDGER PIPELINE NODE... | credential notion|link|github|github-off|gdrive|gdrive-off | units DIR |
#   (gdrive: pipe lifeos/platform/google_consent.py into it, see that file)
#   install COMMIT (close_release.py stages and proves the approved commit before anything live changes) | verify | rollback | uninstall.
#   auto (Edge auto-release, Jim Oct 24: main's green head, only when the Close closure changed, only while installed and serving; verify, else back to the tree that
#   was live; at most 3 attempts per commit) | auto-units (once, by Jim).
# Only port 47443 and its own units/tree are touched: never the Gateway (443/18789), iMessage (47341/47342), Funnel, or `tailscale serve reset`.
set -euo pipefail
OPT=/opt/lifeos-close; ETC=/etc/lifeos-close; CREDS=/etc/credstore.encrypted; UNITS=/etc/systemd/system
LOCAL=47344; TAILNET=47443; REL="$(cd "$(dirname "$0")" && pwd)/close_release.py"
say() { printf '%s\n' "$*"; }
code() { tail -n +2 "$1/MANIFEST" | sha256sum | cut -d' ' -f1; }                 # a tree's files, whatever commit carried them
seen() { [ -s "$ETC/auto.seen" ] && grep -qx "$(code "$1")" "$ETC/auto.seen"; }
drop() { [ -e "$1" ] && chmod -R u+w "$1" 2>/dev/null; rm -rf "$1"; }                     # a staged tree is read-only
hold() {                                                                               # Jim installed or rolled back by hand: auto never overrides him with main's code as it is now
  install -d -m 755 "$ETC"; end_retries
  local c p n pp _                                                                     # a cut-off release first gets its own previous back, so Jim's rollback has its target
  if read -r n c pp _ 2>/dev/null < "$ETC/auto.pending" && [ "$(readlink "$OPT/current" 2>/dev/null)" = "$c" ] && [ "$(readlink "$OPT/previous" 2>/dev/null)" != "${pp:-}" ]; then
    if [ -n "${pp:-}" ] && [ -d "$pp" ]; then ln -sfn "$pp" "$OPT/previous.new" && mv -T "$OPT/previous.new" "$OPT/previous"; else rm -f "$OPT/previous"; fi
  fi
  rm -f "$ETC/auto.pending"
  local h; h="$(python3 -I "$REL" head 2>/dev/null)" || h=unknown                       # unreadable: auto holds whatever head it next reads
  echo "$h" > "$ETC/auto.hold"
}
end_retries() { local t _; read -r t _ 2>/dev/null < "$ETC/auto.attempts" && [ -n "$t" ] && echo "$t 3" > "$ETC/auto.attempts" || true; }   # Jim stepped in: no retry overrides him
remember() { install -d -m 755 "$ETC"; seen "$1" || code "$1" >> "$ETC/auto.seen"; }   # every code that has ever been live: auto-release never brings one back
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
RuntimeMaxSec=210
# D229 (Jim, Oct 20 2026: one click, ~90 s): Build resume waits for the Oracle resume worker; only this unit's group may reach its socket
SupplementaryGroups=lifeos-resume
MemoryMax=128M
TasksMax=16
CPUQuota=50%
EOF
  if [ -s "$CREDS/lifeos-close.github" ]; then                                       # optional (Jim, Oct 24): the post-close Hiring refresh; absent = never asked
    sed -i "/^LoadCredentialEncrypted=link:/a LoadCredentialEncrypted=github:$CREDS/lifeos-close.github" "$1/lifeos-close@.service"
  fi
  if [ -s "$CREDS/lifeos-close.gdrive" ]; then                                       # optional (Jim, Oct 24): Build resume saves to Google Drive (drive.file only); absent = Notion upload
    sed -i "/^LoadCredentialEncrypted=link:/a LoadCredentialEncrypted=gdrive:$CREDS/lifeos-close.gdrive" "$1/lifeos-close@.service"
  fi
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
      github) say "paste the fine-grained token (this repository, Actions read and write only), then Ctrl-D"; systemd-creds encrypt --name=github - "$CREDS/lifeos-close.github" ;;
      github-off) rm -f "$CREDS/lifeos-close.github"; [ ! -e "$UNITS/lifeos-close@.service" ] || { units "$UNITS"; systemctl daemon-reload; }; say "refresh credential removed; the Close screen no longer asks for refreshes"; exit 0 ;;
      gdrive) [ -t 0 ] && { say "pipe lifeos/platform/google_consent.py into this; the credential is never typed or shown"; exit 2; }
              value="$(cat)"                                                    # an aborted sign-in sends nothing: never replace a working credential with that
              printf '%s' "$value" | python3 -I -c 'import json, sys; d = json.load(sys.stdin); sys.exit(0 if all(isinstance(d.get(k), str) and d[k] for k in ("client_id", "client_secret", "refresh_token")) else 1)' 2>/dev/null \
                || { say "STOP: no complete Drive credential arrived; the stored one (if any) is unchanged"; exit 1; }
              printf '%s' "$value" | systemd-creds encrypt --name=gdrive - "$CREDS/lifeos-close.gdrive"; value="" ;;
      gdrive-off) rm -f "$CREDS/lifeos-close.gdrive"; [ ! -e "$UNITS/lifeos-close@.service" ] || { units "$UNITS"; systemctl daemon-reload; }; say "Drive credential removed; Build resume uploads to Notion again"; exit 0 ;;
      *) say "credential notion|link|github|github-off|gdrive|gdrive-off"; exit 2 ;;
    esac
    chmod 600 "$CREDS/lifeos-close.${2}"; say "encrypted credential ${2} stored (value not shown)"
    if { [ "$2" = github ] || [ "$2" = gdrive ]; } && [ -e "$UNITS/lifeos-close@.service" ]; then units "$UNITS"; systemctl daemon-reload; say "the next connection loads it"; fi ;;   # never re-creates an uninstalled service
  units) units "${2:?dir}" ;;
  install)
    commit="${2:?approved full commit sha}"
    [ "${LIFEOS_CLOSE_AUTO:-}" = 1 ] || { install -d -m 755 "$OPT"; exec 9>"$OPT/.lock"; flock -w 120 9 || { say "STOP: another release is running"; exit 1; }; hold; }   # auto already holds it; Jim's own install holds auto off main's current code
    for f in "$ETC/policy.json" "$CREDS/lifeos-close.notion" "$CREDS/lifeos-close.link"; do [ -s "$f" ] || { say "STOP: missing $f"; exit 1; }; done
    st="$(serve_state)"; case "$st" in free|ours) ;; *) say "STOP: tailnet port $TAILNET is $st (not verified free or ours)"; exit 1;; esac
    [ "${LIFEOS_CLOSE_AUTO:-}" != 1 ] || [ "$st" = ours ] || { say "STOP: auto-release never re-adds Serve"; exit 1; }
    dest="$OPT/${commit:0:12}"; stage="$OPT/.stage"; install -d -m 755 "$OPT"; drop "$stage"      # one fixed name: a run killed mid-stage is cleaned by the next
    python3 -I "$REL" stage "$commit" "$stage" || { drop "$stage"; say "STOP: release not verified; nothing changed"; exit 1; }
    chown -R root:root "$stage"; chmod -R go-w,a-w+rX "$stage"
    python3 -I "$REL" check "$stage" || { drop "$stage"; say "STOP: staged tree differs; nothing changed"; exit 1; }
    if [ -e "$dest" ]; then python3 -I "$REL" check "$dest" || { drop "$stage"; say "STOP: existing $dest differs"; exit 1; }; drop "$stage"; else mv -T "$stage" "$dest"; fi
    [ -L "$OPT/current" ] && [ "$(readlink "$OPT/current")" != "$dest" ] && ln -sfn "$(readlink "$OPT/current")" "$OPT/previous"
    ln -sfn "$dest" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"
    units "$UNITS"; systemctl daemon-reload; systemctl enable --now lifeos-close.socket >/dev/null
    [ "$st" = ours ] || tailscale serve --bg --tls-terminated-tcp "$TAILNET" --proxy-protocol 2 "tcp://127.0.0.1:$LOCAL" >/dev/null || { say "FAIL: Serve $TAILNET not added (run uninstall)"; exit 1; }
    "$0" verify && remember "$dest" ;;
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
    probe="$OPT/.probe"; trap 'drop "$probe"' EXIT
    cur="$(readlink "$OPT/current" 2>/dev/null || true)"; prev="$(readlink "$OPT/previous" 2>/dev/null || true)"
    [ -n "$cur" ] || { say "auto: no live tree; the first install is Jim's by hand"; exit 0; }
    systemctl is-enabled --quiet lifeos-close.socket && [ "$(serve_state)" = ours ] || { say "auto: the service is not installed and serving; nothing changed"; exit 0; }
    remember "$cur"; [ -n "$prev" ] && [ -d "$prev" ] && remember "$prev"
    [ -e "$ETC/auto.pending" ] && [ ! -s "$ETC/auto.pending" ] && rm -f "$ETC/auto.pending"
    if read -r p_commit p_cur p_prev _ 2>/dev/null < "$ETC/auto.pending"; then             # a release this step started and never finished (cut off): finish or undo it first
      relink() { if [ -n "${2:-}" ] && [ -d "$2" ]; then ln -sfn "$2" "$OPT/$1.new" && mv -T "$OPT/$1.new" "$OPT/$1"; else rm -f "$OPT/$1"; fi; }
      if [ "$cur" = "$p_cur" ] && [ "$prev" != "${p_prev:-}" ]; then relink previous "${p_prev:-}"; fi      # cut off between the two links: the live tree's own previous is put back
      if [ "$cur" = "$OPT/${p_commit:0:12}" ] && "$0" verify; then [ "$prev" = "$p_cur" ] || relink previous "$p_cur"; remember "$cur"; fi
      if [ "$cur" = "$OPT/${p_commit:0:12}" ] && ! "$0" verify; then
        remember "$cur"                                                                     # it was live: never auto-released again
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
    if read -r held _ 2>/dev/null < "$ETC/auto.hold"; then                                 # Jim's hand: main's code at that moment counts as seen, so only newer code is ever released
      [ "$held" = unknown ] && { echo "$commit" > "$ETC/auto.hold"; exit 0; }              # main was unreadable when Jim acted: the first head read now is held
      [ "$held" = "$commit" ] && exit 0
      drop "$probe"; python3 -I "$REL" stage "$held" "$probe" || { say "auto: held commit unreadable; nothing changed"; exit 1; }
      remember "$probe"; drop "$probe"; rm -f "$ETC/auto.hold"
    fi
    [ "$(cat "$ETC/auto.checked" 2>/dev/null)" = "$commit" ] && exit 0                    # already decided for this commit: no re-download
    [ "$cur" = "$OPT/${commit:0:12}" ] && { echo "$commit" > "$ETC/auto.checked"; exit 0; }   # live and settled (Jim installed it, or a release finished)
    tried=""; n=0; read -r tried n _ 2>/dev/null < "$ETC/auto.attempts" || true
    case "$n" in ''|*[!0-9]*|???*) n=0 ;; esac                                              # anything but a small count reads as none
    [ "$tried" = "$commit" ] || n=0
    [ "$n" -ge 3 ] && exit 0                                                              # three attempts at this commit: wait for the next green commit
    drop "$probe"
    if ! python3 -I "$REL" stage "$commit" "$probe"; then
      echo "$commit $(( n + 1 ))" > "$ETC/auto.attempts"; say "auto: ${commit:0:12} not staged (attempt $(( n + 1 )) of 3); nothing changed"; exit 1
    fi
    if seen "$probe"; then                                                                  # this code has been live before (released, installed, rolled back to or from): never brought back
      echo "$commit" > "$ETC/auto.checked"; python3 -I "$REL" same "$probe" "$cur" || say "auto: ${commit:0:12} carries code that was live before; not released (Jim installs it by hand)"; exit 0
    fi
    echo "$commit $(( n + 1 ))" > "$ETC/auto.attempts"                                   # new code: counted before the switch, so a cut-off run still counts
    echo "$commit $cur ${prev:-}" > "$ETC/auto.pending.new" && mv -f "$ETC/auto.pending.new" "$ETC/auto.pending"   # what a cut-off run is settled against (written whole or not at all)
    if LIFEOS_CLOSE_AUTO=1 "$0" install "$commit"; then
      rm -f "$ETC/auto.pending"; say "auto: released ${commit:0:12}"
    else
      if [ "$(readlink "$OPT/current")" != "$cur" ]; then                                  # verify failed after the switch: back to exactly the live tree, and its own previous tree
        remember "$(readlink "$OPT/current")"                                               # it was live: never auto-released again
        ln -sfn "$cur" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"
        if [ -n "$prev" ]; then ln -sfn "$prev" "$OPT/previous.new" && mv -T "$OPT/previous.new" "$OPT/previous"; else rm -f "$OPT/previous"; fi
        if "$0" verify; then say "auto: ${commit:0:12} failed verify; restored ${cur##*/}"; else say "auto: RESTORE VERIFY FAILED (${cur##*/})"; fi
      fi
      rm -f "$ETC/auto.pending"; exit 1
    fi
    echo "$commit" > "$ETC/auto.checked"
    keep="$(readlink "$OPT/current" 2>/dev/null || true) $(readlink "$OPT/previous" 2>/dev/null || true)"
    for d in "$OPT"/[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]; do
      [ -d "$d" ] && case " $keep " in *" $d "*) ;; *) drop "$d" ;; esac                # only the live tree and its previous tree are kept
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
    [ -d "$OPT/current" ] && remember "$OPT/current"                                       # the code rolled back was live: auto-release never brings it back
    hold
    python3 -I "$REL" check "$(readlink "$OPT/previous")" || { say "FAIL: previous tree differs from its manifest; not activated"; exit 1; }
    ln -sfn "$(readlink "$OPT/previous")" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"; "$0" verify ;;
  uninstall)
    install -d -m 755 "$OPT"; exec 9>"$OPT/.lock"; systemctl stop lifeos-close-release.timer 2>/dev/null || true    # no new run; a running one finishes (never killed mid-switch)
    flock -w 120 9 || { say "STOP: a release is running"; exit 1; }
    rm -f "$ETC/auto.pending"; bad=0; st="$(serve_state)"
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
