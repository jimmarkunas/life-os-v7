#!/bin/bash
# LIFE-643 row 8: the WhatsApp kill switch. Run as root through lifeos-run (the pack's one step calls this file). Never deletes and never reads a session file: it stops and disables the units
# and MOVES the session folders into a root-only quarantine folder (mode 700). Prints fixed words and counts only. Idempotent: a second run reports "skipped" for what is already done.
#   kill_switch.sh [--dry-run]     --dry-run changes nothing and prints what it would do (words "would")
# Test hooks (environment, the host leaves them unset): KS_ROOT (prefix for every host path), KS_SYSTEMCTL (command for the system manager), KS_USER_SYSTEMCTL (command for ubuntu's user manager), KS_STAMP.
set -u
R=${KS_ROOT:-}
SYSC=${KS_SYSTEMCTL:-systemctl}
USYS=${KS_USER_SYSTEMCTL:-}
DRY=no; [ "${1:-}" = "--dry-run" ] && DRY=yes
STAMP=${KS_STAMP:-$(date -u +%s)}
QDIR="$R/var/lib/lifeos-runner/quarantine"
Q="$QDIR/whatsapp-$STAMP"
usys() {                                  # ubuntu's own manager, from root
  if [ -n "$USYS" ]; then "$USYS" "$@"; return; fi
  local uid; uid=$(id -u ubuntu 2>/dev/null) || return 1
  runuser -u ubuntu -- env "XDG_RUNTIME_DIR=/run/user/$uid" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus" /usr/bin/systemctl --user "$@"
}
say() { echo "READBACK $1=$2"; }
mode() { [ "$DRY" = yes ] && echo would || echo "$1"; }

moved=0; failed=0
# 1 and 2: Attention's system units (stopping the socket also stops the service; both are disabled so a reboot does not bring them back)
for unit in lifeos-wa-attention.socket lifeos-wa-attention.service; do
  key=${unit//[.-]/_}
  if [ "$("$SYSC" is-active "$unit" 2>/dev/null)" = active ] || [ "$("$SYSC" is-enabled "$unit" 2>/dev/null)" = enabled ]; then
    if [ "$DRY" = yes ]; then say "$key" would_stop_disable; else "$SYSC" stop "$unit" > /dev/null 2>&1; "$SYSC" disable "$unit" > /dev/null 2>&1; if [ "$("$SYSC" is-active "$unit" 2>/dev/null)" != active ] && [ "$("$SYSC" is-enabled "$unit" 2>/dev/null)" != enabled ]; then say "$key" ok; else say "$key" failed; failed=$((failed + 1)); fi; fi
  else say "$key" skipped; fi
done
# the old ubuntu user unit (row 1 left it disabled; this makes sure)
old=lifeos-whatsapp-attention.service
if [ "$(usys is-active "$old" 2>/dev/null)" = active ] || [ "$(usys is-enabled "$old" 2>/dev/null)" = enabled ]; then
  if [ "$DRY" = yes ]; then say old_user_unit would_stop_disable; else usys stop "$old" > /dev/null 2>&1; usys disable "$old" > /dev/null 2>&1; if [ "$(usys is-active "$old" 2>/dev/null)" != active ] && [ "$(usys is-enabled "$old" 2>/dev/null)" != enabled ]; then say old_user_unit ok; else say old_user_unit failed; failed=$((failed + 1)); fi; fi
else say old_user_unit skipped; fi

# 3: OpenClaw's WhatsApp channel has no fixed documented off command in this repo, so it is NOT touched here (manual step in KILL_SWITCH.md)
say openclaw skipped_manual

# 4: the session folders, moved (never deleted, never read) into the quarantine folder: root-only, mode 700
for src in "$R/var/lib/lifeos-wa/.local/state/lifeos-whatsapp" "$R/home/ubuntu/.local/state/lifeos-whatsapp"; do
  name=new_state; case "$src" in */home/ubuntu/*) name=old_state ;; esac
  if [ ! -d "$src" ]; then say "session_$name" skipped; continue; fi
  if [ "$DRY" = yes ]; then say "session_$name" would_move; continue; fi
  if { mkdir -p "$Q" && chmod 700 "$QDIR" "$Q" && mv "$src" "$Q/$name"; } 2> /dev/null; then say "session_$name" ok; moved=$((moved + 1)); else say "session_$name" failed; failed=$((failed + 1)); fi
done
[ ! -d "$Q" ] || say quarantine_mode "$(stat -c %a "$Q" 2> /dev/null || stat -f %Lp "$Q")"
say result "$(mode "moved_$moved")_failed_$failed dry_run=$DRY"
[ "$failed" = 0 ]
