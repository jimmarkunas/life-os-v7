#!/bin/bash
# LIFE-643 row 1, step 3 of 4: THE SWITCH, the last thing the pack changes. One live copy of the linked-device session at a time:
#   1 raise the flag, 2 stop the old ubuntu unit, 3 copy the session (old folder untouched), 4 start the new service, 5 prove it, 6 disable the old unit and enable the new ones, 7 lower the flag.
# While the flag is up, ANY stop of the new service (a failed check below, the runner's SIGKILL/timeout restore which stops the declared units, a crash) runs resume-old.sh, which enables and
# starts the old unit again. The runner kills a step with SIGKILL, so nothing in this script can run after a kill: the flag + ExecStopPost are what make a mid-way kill recoverable.
# Rollback on a failure we can see: stop the new units, wipe the new copy of the session, enable and start the old unit, prove it.
set -uo pipefail
. "$PACK_DIR/files/common.sh"
OLDPID=$(usys show -p MainPID --value "$OLD_SVC" 2>/dev/null)
BEFORE=$(conn_count "$OLDPID")
NEW_UID=$(cmd id -u "$NEW_USER"); NEW_GID=$(cmd id -g "$NEW_USER")
finished=no

rollback() {
  [ "$finished" = yes ] && return 0
  finished=yes
  sysc stop "$NEW_SVC" > /dev/null 2>&1; sysc stop "$NEW_SOCKET_UNIT" > /dev/null 2>&1       # the stop itself runs resume-old.sh (flag is up) ...
  sysc disable "$NEW_SVC" "$NEW_SOCKET_UNIT" > /dev/null 2>&1
  "$APP/bin/resume-old.sh"                                                                    # ... and this is the same script again (idempotent), in case the unit was never started
  [ "$(usys is-active "$OLD_SVC" 2>/dev/null)" = active ] || { usys enable "$OLD_SVC" > /dev/null 2>&1; usys start "$OLD_SVC" > /dev/null 2>&1; }
  [ "$(usys is-enabled "$OLD_SVC" 2>/dev/null)" = enabled ] || usys enable "$OLD_SVC" > /dev/null 2>&1
  for _ in $(seq 1 30); do [ "$(usys is-active "$OLD_SVC" 2>/dev/null)" = active ] && break; "$SLEEP" 1; done
  wa wipe "$NEW_STATE" "$NEW_HOME" > /dev/null 2>&1; wa wipe "$(dirname "$NEW_KEY")" "$NEW_HOME" > /dev/null 2>&1   # the new copy goes; the old one was never touched
  rm -f "$FLAG"
  echo "READBACK rolled_back old_active=$(usys is-active "$OLD_SVC" 2>/dev/null) old_enabled=$(usys is-enabled "$OLD_SVC" 2>/dev/null) new_active=$(sysc is-active "$NEW_SVC" 2>/dev/null)"
}
fail() { echo "READBACK failed=$1"; rollback; exit 1; }

# refuse to start a second copy: the new side must be down and the old side up
[ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" != active ] || { echo "READBACK failed=new_already_active"; exit 1; }
[ "$(usys is-active "$OLD_SVC" 2>/dev/null)" = active ] || { echo "READBACK failed=old_not_active"; exit 1; }

# 1 + 2: flag, then stop the old unit (not disabled yet: the next boot would still bring it back if this pack died here)
mkdir -p "$(dirname "$FLAG")"; : > "$FLAG"
usys stop "$OLD_SVC" || fail old_stop
for _ in $(seq 1 30); do [ "$(usys is-active "$OLD_SVC" 2>/dev/null)" != active ] && break; "$SLEEP" 1; done
[ "$(usys is-active "$OLD_SVC" 2>/dev/null)" != active ] || fail old_still_active
[ "$(usys show -p MainPID --value "$OLD_SVC" 2>/dev/null)" = 0 ] || fail old_process_remains
echo "READBACK old_stopped=yes old_connections_before=$BEFORE"

# 3: the copy. Same names, kinds and sizes or it stops; modes 700/600; the hash key too.
[ ! -e "$NEW_STATE" ] || wa wipe "$NEW_STATE" "$NEW_HOME" > /dev/null || fail wipe_stale
[ ! -e "$(dirname "$NEW_KEY")" ] || wa wipe "$(dirname "$NEW_KEY")" "$NEW_HOME" > /dev/null || fail wipe_stale_key
echo "READBACK $(wa copy-session "$OLD_STATE" "$NEW_STATE" "$NEW_UID" "$NEW_GID" | sed 's/^/new_/')" ; [ -d "$NEW_STATE/auth" ] || fail copy_session
same=$(wa same "$OLD_STATE" "$NEW_STATE") || fail copy_differs
wa copy-file "$OLD_KEY" "$NEW_KEY" "$NEW_UID" "$NEW_GID" > /dev/null || fail copy_key
[ "$(wa mode "$NEW_KEY")" = 600 ] || fail key_mode
echo "READBACK copy_check $same old_state_untouched=$(wa counts "$OLD_STATE" | cut -d' ' -f1-3)"

# 4: start (the service pulls its socket unit in)
sysc start "$NEW_SVC" || fail start
for _ in $(seq 1 30); do [ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" = active ] && [ -S "$NEW_SOCK" ] && break; "$SLEEP" 1; done
[ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" = active ] || fail new_not_active
[ -S "$NEW_SOCK" ] || fail no_socket
PID=$(sysc show -p MainPID --value "$NEW_SVC" 2>/dev/null)
[ -n "$PID" ] && [ "$PID" != 0 ] || fail no_pid

# 5: proof. Whose process, how it is confined, who can reach the socket, whether WhatsApp is reached, whether the consumer is served.
[ "$(wa procuid "$R/proc" "$PID")" = "$NEW_UID" ] || fail process_not_new_user
"$SLEEP" 5                                                                     # a crash-and-restart loop would show here
[ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" = active ] || fail died_after_start
[ "$(sysc show -p MainPID --value "$NEW_SVC" 2>/dev/null)" = "$PID" ] || fail restarted_again
[ "$(sysc show -p NRestarts --value "$NEW_SVC" 2>/dev/null)" = 0 ] || fail restart_count
[ "$(sysc show -p User --value "$NEW_SVC")" = "$NEW_USER" ] || fail prop_user
for want in ProtectSystem=strict ProtectHome=yes PrivateTmp=yes NoNewPrivileges=yes ProtectKernelTunables=yes ProtectKernelModules=yes ProtectControlGroups=yes RestrictNamespaces=yes LockPersonality=yes RestrictRealtime=yes RestrictSUIDSGID=yes SystemCallArchitectures=native; do
  [ "$(sysc show -p "${want%%=*}" --value "$NEW_SVC")" = "${want#*=}" ] || fail "prop_${want%%=*}"
done
read -r s_uid s_gid s_mode s_kind <<< "$(wa sockinfo "$NEW_SOCK")"
[ "$s_kind" = socket ] && [ "$s_mode" = 660 ] || fail socket_mode
if [ -z "$R" ]; then [ "$s_uid" = "$NEW_UID" ] && [ "$s_gid" = "$OWNER_GID" ] || fail socket_owner; fi
[ "$(as_user ubuntu "$PY" -I -c "$CONNECT_PY" "$NEW_SOCK" 2>/dev/null | tail -1)" -ge 1 ] 2>/dev/null || fail consumer_cannot_connect              # as the consumer's user; bytes counted, never shown
as_user nobody "$PY" -I -c "$CONNECT_PY" "$NEW_SOCK" > /dev/null 2>&1 && fail socket_open_to_others                                        # anyone else must be refused
[ "$(as_user ubuntu "$PY" -I -c "$LISTDIR_PY" "$NEW_STATE" 2>/dev/null | tail -1)" = denied ] || fail consumer_can_read_session            # the consumer must NOT see the session
[ "$(as_user ubuntu "$PY" -I -c "$LISTDIR_PY" "$NEW_HOME" 2>/dev/null | tail -1)" = denied ] || fail consumer_can_read_home
n=0
for _ in $(seq 1 60); do
  [ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" = active ] || fail died_while_waiting
  n=$(conn_count "$PID")
  health=$(as_user ubuntu "$PY" -I -c "$ENDPOINT_PY" "$ENDPOINT" ${R:+"$NEW_SOCK"} 2>/dev/null | tail -1)
  if [ "$BEFORE" -ge 1 ]; then [ "$n" -ge 1 ] && { [ "$health" = CURRENT ] || [ "$health" = DEGRADED ]; } && break
  else [ "$health" = CURRENT ] || [ "$health" = DEGRADED ] || [ "$health" = UNAVAILABLE ] && break; fi
  "$SLEEP" 1
done
if [ "$BEFORE" -ge 1 ]; then
  [ "$n" -ge 1 ] || fail no_whatsapp_connection
  [ "$health" = CURRENT ] || [ "$health" = DEGRADED ] || fail endpoint_not_served
fi
[ "$(usys is-active "$GW" 2>/dev/null)" = active ] || fail gateway_not_active
echo "READBACK proof new_pid_user_is_new_user=yes socket_mode=$s_mode connections=$n endpoint_health=$health consumer_read_session=denied other_users=refused"

# 6: only now make it permanent: old unit disabled (its file and folder stay), new units enabled for boot
usys disable "$OLD_SVC" > /dev/null 2>&1 || fail old_disable
sysc enable "$NEW_SOCKET_UNIT" "$NEW_SVC" > /dev/null 2>&1 || fail new_enable
[ "$(usys is-enabled "$OLD_SVC" 2>/dev/null)" = disabled ] || fail old_not_disabled
[ "$(usys is-active "$OLD_SVC" 2>/dev/null)" != active ] || fail old_came_back
[ "$(sysc is-enabled "$NEW_SVC" 2>/dev/null)" = enabled ] || fail new_not_enabled
[ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" = active ] || fail new_stopped_at_end

# 7: done; the flag goes, so a later normal stop of the new service never starts the old one
finished=yes; rm -f "$FLAG"
echo "READBACK switched=yes old_disabled=yes new_enabled=yes"
exit 0
