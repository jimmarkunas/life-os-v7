#!/bin/bash
# LIFE-643 step 3 of 3: names and counts only; always exits 0 (the gate is step 2, which restores on failure).
. "$PACK_DIR/files/common.sh"
echo "READBACK after_state $(wa counts "$STATE_DIR")"
echo "READBACK after_credentials $(wa counts "$CRED_DIR")"
echo "READBACK attention_active=$(usys is-active "$SVC")"
echo "READBACK gateway_active=$(usys is-active "$GW")"
echo "READBACK socket_mode=$(wa mode "$SOCK")"
echo "READBACK attention_whatsapp_connections=$(conn_count "$(usys show -p MainPID --value "$SVC")")"
echo "READBACK mode_list_lines=$(wc -l < "$LIST" | tr -d ' ')"
for prop in NoNewPrivileges RestrictAddressFamilies RestrictNamespaces LockPersonality RestrictRealtime RestrictSUIDSGID SystemCallArchitectures SystemCallErrorNumber MemoryMax UMask NRestarts ActiveState SubState; do
  echo "READBACK $prop=$(usys show -p "$prop" --value "$SVC" | tr '\n' ' ' | cut -c1-120)"
done
echo "READBACK SystemCallFilter_head=$(usys show -p SystemCallFilter --value "$SVC" | tr '\n' ' ' | cut -c1-60)"
exit 0
