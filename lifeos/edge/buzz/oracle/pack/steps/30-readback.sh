#!/bin/bash
# LIFE-590 step 3 of 3: names and counts only; always exits 0 (the gate is step 2, which restores on failure).
R=$(cat "$PACK_DIR/files/ROOT" 2>/dev/null || true)
BASE="$R/opt/lifeos-buzz-mcp"; SVC=lifeos-buzz-mcp.service; SYSTEMCTL="$R/usr/bin/systemctl"
echo "READBACK service_active=$("$SYSTEMCTL" is-active "$SVC")"
echo "READBACK current=$(readlink "$BASE/current")"
[ -d "$BASE/releases/c423a4434c4c" ] && echo "READBACK previous_release_present=yes" || echo "READBACK previous_release_present=no"
pid=$("$SYSTEMCTL" show -p MainPID --value "$SVC")
tr '\0' ' ' < "$R/proc/$pid/cmdline" 2>/dev/null | grep -q 'lifeos\.buzz\.mcp' && echo "READBACK process_module=lifeos.buzz.mcp" || echo "READBACK process_module=other"
echo "READBACK releases_total=$(ls "$BASE/releases" | wc -l | tr -d ' ')"
exit 0
