#!/bin/bash
# LIFE-643 row 5, step 3 of 4: THE SWAP. Stop the service and its socket, rename the old tree to node_modules.pre-slim, move the slim tree in, start, prove it. Any failed check puts the old tree back and
# starts the service again (fixed word, READBACK rolled_back). The pre-slim tree is kept for a manual rollback. The runner also restores the two units' running state if this step is killed.
set -uo pipefail
. "$PACK_DIR/files/common.sh"
APPD="$APP/app"; BUILD="$APPD/.slim"; NM="$APPD/node_modules"; OLD="$APPD/node_modules.pre-slim"
PID=$(sysc show -p MainPID --value "$NEW_SVC" 2>/dev/null)
BEFORE=$(conn_count "$PID")
swapped=no; finished=no

wait_active() { for _ in $(seq 1 30); do [ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" = active ] && return 0; "$SLEEP" 1; done; return 1; }
rollback() {
  [ "$finished" = yes ] && return 0
  finished=yes
  sysc stop "$NEW_SOCKET_UNIT" "$NEW_SVC" > /dev/null 2>&1
  if [ "$swapped" = yes ]; then wa wipe "$NM" "$APPD" > /dev/null 2>&1; mv "$OLD" "$NM"; fi
  sysc start "$NEW_SVC" > /dev/null 2>&1; wait_active
  wa wipe "$BUILD" "$APPD" > /dev/null 2>&1
  echo "READBACK rolled_back old_tree_back=$([ ! -e "$OLD" ] && echo yes || echo no) unit_active=$(sysc is-active "$NEW_SVC" 2>/dev/null)"
}
fail() { echo "READBACK failed=$1"; rollback; exit 1; }

[ -d "$BUILD/node_modules" ] && [ ! -e "$OLD" ] || fail not_ready
sysc stop "$NEW_SOCKET_UNIT" "$NEW_SVC" || fail stop
for _ in $(seq 1 30); do [ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" != active ] && break; "$SLEEP" 1; done
[ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" != active ] || fail still_active
mv "$NM" "$OLD" || fail rename_old
swapped=yes
mv "$BUILD/node_modules" "$NM" || fail move_new
sysc start "$NEW_SVC" || fail start
wait_active || fail not_active
[ -S "$NEW_SOCK" ] || fail no_socket
PID=$(sysc show -p MainPID --value "$NEW_SVC" 2>/dev/null)
[ -n "$PID" ] && [ "$PID" != 0 ] || fail no_pid
"$SLEEP" 5                                                                     # a crash-and-restart loop would show here
[ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" = active ] && [ "$(sysc show -p MainPID --value "$NEW_SVC" 2>/dev/null)" = "$PID" ] || fail died_after_start
[ "$(sysc show -p NRestarts --value "$NEW_SVC" 2>/dev/null)" = 0 ] || fail restart_count
[ "$(sysc is-enabled "$NEW_SVC" 2>/dev/null)" = enabled ] || fail not_enabled
n=0
for _ in $(seq 1 60); do
  [ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" = active ] || fail died_while_waiting
  n=$(conn_count "$PID")
  { [ "$BEFORE" -lt 1 ] || [ "$n" -ge 1 ]; } && break
  "$SLEEP" 1
done
[ "$BEFORE" -lt 1 ] || [ "$n" -ge 1 ] || fail no_whatsapp_connection
[ "$BEFORE" -lt 1 ] || [ "$(as_user ubuntu "$PY" -I -c "$CONNECT_PY" "$NEW_SOCK" 2>/dev/null | tail -1)" -ge 1 ] 2>/dev/null || fail consumer_cannot_connect
finished=yes
wa wipe "$BUILD" "$APPD" > /dev/null
echo "READBACK swapped=yes connections_before=$BEFORE connections_after=$n unit=active_enabled old_tree_kept=$([ -d "$OLD" ] && echo yes || echo no)"
exit 0
