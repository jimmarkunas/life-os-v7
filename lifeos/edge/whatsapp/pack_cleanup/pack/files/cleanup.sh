#!/bin/bash
# LIFE-643 cleanup (Jim: "Go", Nov 13 2026). After rows 1 and 5 the new Attention (user lifeos-wa) is the only copy that runs. This removes what the move left behind, in this order:
#   A  read-only gates: the new service is active, enabled, run by lifeos-wa, answers on its socket with a good health word, its own session exists; the old user unit is not running. Any miss stops with one fixed word and changes nothing.
#   B  the two consumer files lose the old-socket / old-unit fallback (installed from this pack, kept in a temp copy; a failed check puts the old files back; the runner also restores both files on any failure).
#   C  only after A and B passed: delete the old session copy, the old hash-key folder, the old program folder, the old ubuntu user unit file, the pre-slim npm tree and the rows 3+4 mode list. Exact paths only, never a link.
# No session or credential file is opened, read or printed: names, counts and fixed words only. Deleting is final: the old copies are gone; the new session in /var/lib/lifeos-wa is untouched; a lost session is re-linked from the phone.
# Not touched: the new unit, /opt/lifeos-wa/bin/resume-old.sh (the new unit's stop hook still calls it; with no flag file it does nothing), OpenClaw, the alert relay.
#   cleanup.sh [--dry-run]     --dry-run changes nothing and prints "would" words
# Test hooks (the host leaves them unset): CL_ROOT (prefix for host paths), CL_SYSTEMCTL, CL_USER_SYSTEMCTL, CL_PY, CL_NODE, CL_FILES (folder with alerts_endpoint.py and stats.mjs), CL_AS_UBUNTU (command that runs a command as ubuntu).
set -u
R=${CL_ROOT:-}
SYSC=${CL_SYSTEMCTL:-systemctl}
USYS=${CL_USER_SYSTEMCTL:-}
PY=${CL_PY:-python3}
NODE=${CL_NODE:-$R/opt/lifeos-wa/bin/node}
FILES=${CL_FILES:-$(cd "$(dirname "$0")" && pwd)}
DRY=no; [ "${1:-}" = "--dry-run" ] && DRY=yes
UH="$R/home/ubuntu"
SVC=lifeos-wa-attention.service
OLD_SVC=lifeos-whatsapp-attention.service
NEW_STATE="$R/var/lib/lifeos-wa/.local/state/lifeos-whatsapp"
SOCK="$R/run/lifeos-wa/attention.sock"
ENDPOINT="$UH/.local/lib/lifeos-imessage/alerts_endpoint.py"
STATS="$UH/.local/lib/lifeos-oracle/stats.mjs"

say() { echo "READBACK $1=$2"; }
fail() { say failed "$1"; exit 1; }
usys() {
  if [ -n "$USYS" ]; then "$USYS" "$@"; return; fi
  local uid; uid=$(id -u ubuntu 2>/dev/null) || return 1
  runuser -u ubuntu -- env "XDG_RUNTIME_DIR=/run/user/$uid" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus" /usr/bin/systemctl --user "$@"
}
as_ubuntu() { if [ -n "${CL_AS_UBUNTU:-}" ]; then "$CL_AS_UBUNTU" "$@"; else runuser -u ubuntu -- "$@"; fi; }
SNAP_PY='import socket,sys,json
try:
    s=socket.socket(socket.AF_UNIX); s.settimeout(8); s.connect(sys.argv[1]); b=b""
    while True:
        c=s.recv(65536)
        if not c: break
        b+=c
    print(json.loads(b.decode()).get("health","none"))
except Exception:
    print("none")'
ENDPOINT_PY='import importlib.util as u,sys
sp=u.spec_from_file_location("ep",sys.argv[1]); ep=u.module_from_spec(sp); sp.loader.exec_module(ep)
print(ep.whatsapp("0"*64, sys.argv[2] if len(sys.argv)>2 else None).get("health"))'
good() { [ "$1" = CURRENT ] || [ "$1" = DEGRADED ]; }

# ---- A: gates (read-only)
[ "$("$SYSC" is-active "$SVC" 2>/dev/null)" = active ] || fail new_service_not_active
[ "$("$SYSC" is-enabled "$SVC" 2>/dev/null)" = enabled ] || fail new_service_not_enabled
[ "$("$SYSC" show -p User --value "$SVC" 2>/dev/null)" = lifeos-wa ] || fail new_service_wrong_user
health=$("$PY" -I -c "$SNAP_PY" "$SOCK" 2>/dev/null | tail -1)
good "$health" || fail "new_service_health_${health:-none}"
[ -d "$NEW_STATE/auth" ] || fail new_session_missing
[ "$(usys is-active "$OLD_SVC" 2>/dev/null)" != active ] || fail old_unit_running
say gates "new_service=active_enabled_lifeos-wa health=$health old_unit=not_running"

# ---- B: the two consumer files (reversible)
BK=""
restore_files() { [ -n "$BK" ] || return 0; [ ! -f "$BK/endpoint" ] || cp -p "$BK/endpoint" "$ENDPOINT"; [ ! -f "$BK/stats" ] || cp -p "$BK/stats" "$STATS"; }
if [ "$DRY" = yes ]; then
  say consumers would_install
else
  BK=$(mktemp -d) || fail temp_dir
  cp -p "$ENDPOINT" "$BK/endpoint" 2>/dev/null; cp -p "$STATS" "$BK/stats" 2>/dev/null
  OWN=""; [ -n "$R" ] || OWN="-o ubuntu -g ubuntu"
  # shellcheck disable=SC2086
  { install $OWN -m 600 "$FILES/alerts_endpoint.py" "$ENDPOINT" && install $OWN -m 600 "$FILES/stats.mjs" "$STATS"; } || { restore_files; fail consumer_install; }
  "$PY" -I -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$ENDPOINT" || { restore_files; fail endpoint_syntax; }
  "$NODE" --check "$STATS" > /dev/null 2>&1 || { restore_files; fail stats_syntax; }
  eh=$(as_ubuntu "$PY" -I -c "$ENDPOINT_PY" "$ENDPOINT" ${R:+"$SOCK"} 2>/dev/null | tail -1)
  good "$eh" || { restore_files; fail "endpoint_health_${eh:-none}"; }
  rm -rf "$BK"
  say consumers "installed endpoint_health=$eh"
fi

# ---- C: the old copies (final)
removed=0; failed=0
rm_exact() {            # label, path
  local label=$1 path=$2 n
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then say "$label" skipped; return; fi
  if [ -L "$path" ] || [ "$(cd "$(dirname "$path")" 2>/dev/null && pwd -P)/$(basename "$path")" != "$path" ]; then say "$label" refused_link; failed=$((failed + 1)); return; fi
  n=$(find "$path" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$DRY" = yes ]; then say "$label" "would_remove_entries_$n"; return; fi
  if rm -rf -- "$path" 2>/dev/null && [ ! -e "$path" ]; then say "$label" "removed_entries_$n"; removed=$((removed + 1)); else say "$label" failed; failed=$((failed + 1)); fi
}
rm_exact old_session "$UH/.local/state/lifeos-whatsapp"
rm_exact old_key_folder "$UH/.config/lifeos-whatsapp"
rm_exact old_program "$UH/.local/lib/lifeos-whatsapp"
rm_exact old_unit_file "$UH/.config/systemd/user/lifeos-whatsapp-attention.service"
rm_exact pre_slim_tree "$R/opt/lifeos-wa/app/node_modules.pre-slim"
rm_exact rows34_mode_list "$R/var/lib/lifeos-runner/whatsapp-modes-LIFE-643.list"
if [ "$DRY" = no ]; then usys daemon-reload > /dev/null 2>&1; usys reset-failed "$OLD_SVC" > /dev/null 2>&1; fi

# ---- final read-back: the new service must still be fine
if [ "$DRY" = no ]; then
  [ "$("$SYSC" is-active "$SVC" 2>/dev/null)" = active ] || { say failed new_service_not_active_at_end; failed=$((failed + 1)); }
  end=$("$PY" -I -c "$SNAP_PY" "$SOCK" 2>/dev/null | tail -1); good "$end" || { say failed "health_at_end_${end:-none}"; failed=$((failed + 1)); }
  say new_service_at_end "active health=${end:-none}"
fi
say result "removed_$removed failed_$failed dry_run=$DRY"
[ "$failed" = 0 ]
