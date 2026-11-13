#!/bin/bash
# LIFE-643 step 1 of 3: read-only checks. Nothing is changed. Any failure prints one fixed word (READBACK failed=...) and stops the pack.
set -uo pipefail
. "$PACK_DIR/files/common.sh"
fail() { echo "READBACK failed=$1"; exit 1; }
[ -d "$STATE_DIR" ] && [ -d "$CRED_DIR" ] || fail tree_missing
why=$(wa check "$OWNER_UID" "$STATE_DIR" "$CRED_DIR") || fail "tree_$why"
holders=$(wa holders "$R/proc" "$OWNER_UID" "$STATE_DIR" "$CRED_DIR") || fail holders_unreadable
[ "$holders" = 0 ] || fail other_user_uses_paths
if [ -z "$R" ]; then
  members=$(getent group ubuntu | cut -d: -f4)
  [ -z "$members" ] || fail group_has_members
fi
for unit in "$SVC" "$GW"; do
  [ "$(usys is-active "$unit" 2>/dev/null)" = active ] || fail "not_active_${unit%%.*}"
  pid=$(usys show -p MainPID --value "$unit" 2>/dev/null)
  [ -n "$pid" ] && [ "$pid" != 0 ] || fail "no_pid_${unit%%.*}"
  [ "$(wa uid "$R/proc/$pid")" = "$OWNER_UID" ] || fail "process_not_ubuntu_${unit%%.*}"
done
[ -f "$UNIT" ] || fail unit_missing
[ "$(wa sha256 "$UNIT")" = "2ed076ee246fc8296ad00d32711b05a7b52c3d7cfa956e411ee88628f2863a77" ] || fail unit_unexpected
echo "READBACK owner_only_users=ok other_user_holders=$holders"
echo "READBACK gateway_and_attention_run_as_uid=$OWNER_UID"
echo "READBACK before_state $(wa counts "$STATE_DIR")"
echo "READBACK before_credentials $(wa counts "$CRED_DIR")"
echo "READBACK before_connections=$(conn_count "$(usys show -p MainPID --value "$SVC")")"
exit 0
