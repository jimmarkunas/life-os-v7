#!/bin/bash
# LIFE-643 row 5, step 1 of 4: read-only checks. Nothing is changed. Any failure prints one fixed word (READBACK failed=...) and stops the pack.
set -uo pipefail
. "$PACK_DIR/files/common.sh"
fail() { echo "READBACK failed=$1"; exit 1; }
APPD="$APP/app"

# the row 1 layout must be there: root-owned program folder, the app files, a node_modules tree, the user, both unit files
[ -x "$APP/bin/node" ] && [ -f "$APPD/attention.mjs" ] && [ -d "$APPD/node_modules" ] && [ -f "$UNITDIR/$NEW_SVC" ] && [ -f "$UNITDIR/$NEW_SOCKET_UNIT" ] || fail row1_layout_missing
cmd getent passwd "$NEW_USER" > /dev/null 2>&1 || fail row1_layout_missing
# the committed manifest and lock are the ones installed (so the new tree is the one the service was started with, minus the omitted packages)
[ "$(wa sha256 "$APPD/package-lock.json")" = "$(wa sha256 "$PACK_DIR/files/package-lock.json")" ] || fail lock_unexpected
[ "$(wa sha256 "$APPD/package.json")" = "$(wa sha256 "$PACK_DIR/files/package.json")" ] || fail package_unexpected
# nothing left from an earlier run
[ ! -e "$APPD/node_modules.pre-slim" ] || fail already_slimmed_or_leftover
[ ! -e "$APPD/.slim" ] || fail build_dir_leftover
# node and npm: the node the service runs, and the npm that sits beside the node the old unit used
NODE_SRC=$(readlink -f "$OLD_NODE_LINK") || fail node_missing
NPM="$(dirname "$NODE_SRC")/npm"
[ -x "$NPM" ] || fail npm_missing
NPM_VERSION=$(as_user "$NEW_USER" env "HOME=$NEW_HOME" "PATH=$APP/bin:/usr/bin:/bin" "$NPM" --version 2>/dev/null | tail -1) || fail npm_not_usable_by_new_user
[ -n "$NPM_VERSION" ] || fail npm_not_usable_by_new_user
# the service state is recorded: it must be running and enabled (it is started again at the end and compared)
[ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" = active ] || fail unit_not_active
[ "$(sysc is-enabled "$NEW_SVC" 2>/dev/null)" = enabled ] || fail unit_not_enabled
PID=$(sysc show -p MainPID --value "$NEW_SVC" 2>/dev/null)
[ -n "$PID" ] && [ "$PID" != 0 ] || fail no_pid
free_kb=$(df -Pk "$APPD" | awk 'NR==2 {print $4}')
[ -n "$free_kb" ] && [ "$free_kb" -gt $(( $(du -sk "$APPD/node_modules" | cut -f1) * 3 + 51200 )) ] || fail disk_space
echo "READBACK unit=active_enabled connections=$(conn_count "$PID") npm=$NPM_VERSION node=$("$APP/bin/node" --version 2>/dev/null)"
echo "READBACK old_tree $(wa tree-summary "$APPD/node_modules")"
exit 0
