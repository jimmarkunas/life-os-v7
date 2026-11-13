#!/bin/bash
# LIFE-643 row 1, step 1 of 4: read-only checks. Nothing is changed. Any failure prints one fixed word (READBACK failed=...) and stops the pack.
set -uo pipefail
. "$PACK_DIR/files/common.sh"
fail() { echo "READBACK failed=$1"; exit 1; }

# the old service: running as ubuntu, enabled, session folder intact and owned by ubuntu alone
[ -d "$OLD_STATE/auth" ] && [ -f "$OLD_KEY" ] && [ -d "$OLD_APP/node_modules" ] || fail old_tree_missing
why=$(wa check "$OWNER_UID" "$OLD_STATE") || fail "old_tree_$why"
[ "$(usys is-active "$OLD_SVC" 2>/dev/null)" = active ] || fail old_unit_not_active
[ "$(usys is-enabled "$OLD_SVC" 2>/dev/null)" = enabled ] || fail old_unit_not_enabled
OLDPID=$(usys show -p MainPID --value "$OLD_SVC" 2>/dev/null)
[ -n "$OLDPID" ] && [ "$OLDPID" != 0 ] || fail old_no_pid
[ "$(wa procuid "$R/proc" "$OLDPID")" = "$OWNER_UID" ] || fail old_process_not_ubuntu
[ "$(usys is-active "$GW" 2>/dev/null)" = active ] || fail gateway_not_active

# the files this pack replaces are the ones main had; the pinned Baileys lock is the one installed
[ "$(wa sha256 "$ENDPOINT")" = "$ENDPOINT_OLD_SHA" ] || fail endpoint_unexpected
[ "$(wa sha256 "$STATS")" = "$STATS_OLD_SHA" ] || fail stats_unexpected
[ "$(wa sha256 "$OLD_APP/attention.mjs")" = "$ATTENTION_OLD_SHA" ] || fail attention_unexpected
[ "$(wa sha256 "$OLD_APP/state.mjs")" = "$STATE_OLD_SHA" ] || fail state_unexpected
[ "$(wa sha256 "$OLD_APP/package-lock.json")" = "$(wa sha256 "$PACK_DIR/files/package-lock.json")" ] || fail lock_unexpected
[ "$(wa sha256 "$OLD_APP/package.json")" = "$(wa sha256 "$PACK_DIR/files/package.json")" ] || fail package_unexpected

# node: the binary the old unit runs, as a real file
NODE_SRC=$(readlink -f "$OLD_NODE_LINK") || fail node_missing
[ -f "$NODE_SRC" ] && [ -x "$NODE_SRC" ] || fail node_missing
NODE_VERSION=$("$NODE_SRC" --version 2>/dev/null) || fail node_does_not_run

# the socket group is the ubuntu group and nobody else is in it
cmd getent group ubuntu > /dev/null 2>&1 || fail no_ubuntu_group
if [ -z "$R" ]; then
  members=$(cmd getent group ubuntu | cut -d: -f4)
  [ -z "$members" ] || fail ubuntu_group_has_members
fi

# the new user: not there yet, or there in exactly the intended shape (a repeat run)
if cmd getent passwd "$NEW_USER" > /dev/null 2>&1; then
  line=$(cmd getent passwd "$NEW_USER")
  newuid=$(echo "$line" | cut -d: -f3)
  [ "$newuid" != 0 ] && [ "$newuid" != "$OWNER_UID" ] || fail new_user_wrong_id
  [ "$(echo "$line" | cut -d: -f6)" = /var/lib/lifeos-wa ] || fail new_user_wrong_home
  case "$(echo "$line" | cut -d: -f7)" in /usr/sbin/nologin | /sbin/nologin | /bin/false | /usr/bin/false) ;; *) fail new_user_has_shell ;; esac
  [ "$(cmd id -Gn "$NEW_USER")" = "$NEW_USER" ] || fail new_user_in_other_groups
  echo "READBACK new_user=exists"
else
  echo "READBACK new_user=absent"
fi

# nothing of the new side is running or left half-made
[ "$(sysc is-active "$NEW_SVC" 2>/dev/null)" != active ] || fail new_unit_already_active
[ "$(sysc is-active "$NEW_SOCKET_UNIT" 2>/dev/null)" != active ] || fail new_socket_already_active
[ ! -e "$NEW_SOCK" ] || fail new_socket_exists
[ ! -e "$FLAG" ] || fail switch_flag_present
for pair in "$NEW_SVC:lifeos-wa-attention.service" "$NEW_SOCKET_UNIT:lifeos-wa-attention.socket"; do
  f="$UNITDIR/${pair%%:*}"
  [ ! -e "$f" ] || [ "$(wa sha256 "$f")" = "$(wa sha256 "$PACK_DIR/files/${pair##*:}")" ] || fail unit_file_differs
done

# room for the program tree and a second copy of the session (counted generously)
need_kb=$(( $(du -sk "$NODE_SRC" | cut -f1) + $(du -sk "$OLD_APP" | cut -f1) + $(du -sk "$OLD_STATE" | cut -f1) * 3 + 51200 ))
free_kb=$(df -Pk "$R/var" | awk 'NR==2 {print $4}')
[ -n "$free_kb" ] && [ "$free_kb" -gt "$need_kb" ] || fail disk_space

echo "READBACK old_unit=active_enabled_as_uid_$OWNER_UID gateway=active"
echo "READBACK old_state $(wa counts "$OLD_STATE")"
echo "READBACK node=$NODE_VERSION lock_matches=yes files_pinned=yes"
echo "READBACK before_connections=$(conn_count "$OLDPID")"
exit 0
