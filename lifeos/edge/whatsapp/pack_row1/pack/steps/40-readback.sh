#!/bin/bash
# LIFE-643 row 1, step 4 of 4: names, ids, modes and counts only; always exits 0 (the gate is step 3, which rolls back on any failure).
. "$PACK_DIR/files/common.sh"
name_of_uid() { cmd getent passwd "$1" | cut -d: -f1; }
name_of_gid() { cmd getent group "$1" | cut -d: -f1; }
PID=$(sysc show -p MainPID --value "$NEW_SVC" 2>/dev/null)
puid=$(wa procuid "$R/proc" "$PID" 2>/dev/null)
echo "READBACK new_unit_active=$(sysc is-active "$NEW_SVC") enabled=$(sysc is-enabled "$NEW_SVC") socket_unit_active=$(sysc is-active "$NEW_SOCKET_UNIT") enabled=$(sysc is-enabled "$NEW_SOCKET_UNIT")"
echo "READBACK main_pid_user=$(name_of_uid "$puid") uid_is_ubuntu=$([ "$puid" = "$OWNER_UID" ] && echo yes || echo no) nrestarts=$(sysc show -p NRestarts --value "$NEW_SVC")"
for prop in User Group ProtectSystem ReadWritePaths ProtectHome PrivateTmp PrivateDevices ProtectKernelTunables ProtectKernelModules ProtectControlGroups RestrictAddressFamilies RestrictNamespaces LockPersonality RestrictRealtime RestrictSUIDSGID SystemCallArchitectures SystemCallErrorNumber NoNewPrivileges MemoryMax UMask Restart ActiveState SubState; do
  echo "READBACK $prop=$(sysc show -p "$prop" --value "$NEW_SVC" | tr '\n' ' ' | cut -c1-120)"
done
echo "READBACK SystemCallFilter_head=$(sysc show -p SystemCallFilter --value "$NEW_SVC" | tr '\n' ' ' | cut -c1-60)"
echo "READBACK whatsapp_connections=$(conn_count "$PID")"
read -r s_uid s_gid s_mode s_kind <<< "$(wa sockinfo "$NEW_SOCK" 2>/dev/null)"
echo "READBACK socket path=/run/lifeos-wa/attention.sock kind=$s_kind owner=$(name_of_uid "$s_uid") group=$(name_of_gid "$s_gid") mode=$s_mode"
echo "READBACK consumer_connect_bytes=$(as_user ubuntu "$PY" -I -c "$CONNECT_PY" "$NEW_SOCK" 2>/dev/null | tail -1) endpoint_health=$(as_user ubuntu "$PY" -I -c "$ENDPOINT_PY" "$ENDPOINT" ${R:+"$NEW_SOCK"} 2>/dev/null | tail -1)"
echo "READBACK ubuntu_lists_new_state=$(as_user ubuntu "$PY" -I -c "$LISTDIR_PY" "$NEW_STATE" 2>/dev/null | tail -1) ubuntu_lists_new_home=$(as_user ubuntu "$PY" -I -c "$LISTDIR_PY" "$NEW_HOME" 2>/dev/null | tail -1) nobody_connects=$(as_user nobody "$PY" -I -c "$CONNECT_PY" "$NEW_SOCK" > /dev/null 2>&1 && echo yes || echo no)"
echo "READBACK new_state $(wa counts "$NEW_STATE")"
echo "READBACK new_home_mode=$(wa mode "$NEW_HOME") new_key_mode=$(wa mode "$NEW_KEY") user_shell=$(cmd getent passwd "$NEW_USER" | cut -d: -f7)"
echo "READBACK old_unit_active=$(usys is-active "$OLD_SVC" 2>/dev/null) old_unit_enabled=$(usys is-enabled "$OLD_SVC" 2>/dev/null) old_unit_file_present=$([ -f "$UH/.config/systemd/user/$OLD_SVC" ] && echo yes || echo no)"
echo "READBACK old_state_still_there $(wa counts "$OLD_STATE")"
echo "READBACK gateway_active=$(usys is-active "$GW" 2>/dev/null) switch_flag_gone=$([ ! -e "$FLAG" ] && echo yes || echo no)"
echo "READBACK endpoint_sha12=$(wa sha256 "$ENDPOINT" | cut -c1-12) stats_sha12=$(wa sha256 "$STATS" | cut -c1-12) node=$("$APP/bin/node" --version 2>/dev/null)"
exit 0
