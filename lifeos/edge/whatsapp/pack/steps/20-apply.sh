#!/bin/bash
# LIFE-643 step 2 of 3: (a) session folders to owner-only (folders 700, files 600), (b) hardened user unit, restart as the ubuntu user, prove it.
# The names-and-modes list (no contents) is written first. Any failure, Ctrl-C or SIGTERM puts back the old unit text AND every mode, and restarts the service on the old unit
# (the runner restores regular files only, not 2,300 chmods and not a user manager).
set -uo pipefail
. "$PACK_DIR/files/common.sh"
OWN="-o ubuntu -g ubuntu"; [ -z "$R" ] || OWN=""
OLDUNIT=$(mktemp); cp -p "$UNIT" "$OLDUNIT" || { echo "READBACK failed=old_unit_unreadable"; exit 1; }
modes_changed=no; unit_changed=no; done=no
rollback() {
  [ "$done" = yes ] && return 0
  done=yes
  if [ "$unit_changed" = yes ]; then
    cat "$OLDUNIT" > "$UNIT"
    usys daemon-reload; usys restart "$SVC"
    echo "READBACK unit_rolled_back=yes"
  fi
  if [ "$modes_changed" = yes ]; then
    echo "READBACK modes_rolled_back $(wa rollback "$LIST")"
  fi
}
fail() { echo "READBACK failed=$1"; rollback; rm -f "$OLDUNIT"; exit 1; }
trap 'echo "READBACK failed=interrupted"; rollback; rm -f "$OLDUNIT"; exit 1' INT TERM HUP

# (a) modes: record first, then change
[ ! -e "$LIST" ] || rm -f "$LIST"
mkdir -p "$(dirname "$LIST")"
rows=$(wa record "$LIST" "$STATE_DIR" "$CRED_DIR") || fail record_list
echo "READBACK mode_list_entries=$rows"
modes_changed=yes
changed=$(wa apply "$STATE_DIR" "$CRED_DIR") || fail chmod
echo "READBACK entries_changed=$changed"

# (b) unit: install, reload, restart ONLY the attention user service
OLDPID=$(usys show -p MainPID --value "$SVC")
BEFORE=$(conn_count "$OLDPID")
unit_changed=yes
install $OWN -m 644 "$PACK_DIR/files/lifeos-whatsapp-attention.service" "$UNIT" || fail unit
usys daemon-reload || fail reload
usys restart "$SVC" || fail restart
for _ in $(seq 1 30); do
  [ "$(usys is-active "$SVC")" = active ] && [ -S "$SOCK" ] && break
  "$SLEEP" 1
done
[ "$(usys is-active "$SVC")" = active ] || fail not_active
[ -S "$SOCK" ] || fail no_socket
PID=$(usys show -p MainPID --value "$SVC")
[ -n "$PID" ] && [ "$PID" != 0 ] || fail no_pid
"$SLEEP" 5                                                                   # a crash-and-restart loop would show here
[ "$(usys is-active "$SVC")" = active ] || fail died_after_start
[ "$(usys show -p MainPID --value "$SVC")" = "$PID" ] || fail restarted_again
[ "$(usys show -p NRestarts --value "$SVC")" = 0 ] || fail restart_count
[ "$(wa mode "$SOCK")" = 600 ] || fail socket_mode
wa connect "$SOCK" > /dev/null || fail socket_no_answer                      # the answer is counted and dropped, never kept
if [ "$BEFORE" -ge 1 ]; then                                                  # it was connected to WhatsApp before: it must be again (proves DNS and network under the new filter)
  n=0
  for _ in $(seq 1 45); do
    n=$(conn_count "$PID"); [ "$n" -ge 1 ] && break
    "$SLEEP" 1
  done
  [ "$n" -ge 1 ] || fail no_whatsapp_connection
fi
[ "$(usys is-active "$GW")" = active ] || fail gateway_not_active
done=yes; rm -f "$OLDUNIT"
echo "READBACK applied=yes attention_pid_changed=$([ "$PID" != "$OLDPID" ] && echo yes || echo no)"
exit 0
