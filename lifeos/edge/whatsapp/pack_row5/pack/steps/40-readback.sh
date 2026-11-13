#!/bin/bash
# LIFE-643 row 5, step 4 of 4: read-only. Names and counts only; always exits 0 (the gate is step 3, which rolls back on any failure).
. "$PACK_DIR/files/common.sh"
APPD="$APP/app"
PID=$(sysc show -p MainPID --value "$NEW_SVC" 2>/dev/null)
echo "READBACK unit_active=$(sysc is-active "$NEW_SVC") enabled=$(sysc is-enabled "$NEW_SVC") socket_active=$(sysc is-active "$NEW_SOCKET_UNIT") nrestarts=$(sysc show -p NRestarts --value "$NEW_SVC") connections=$(conn_count "$PID")"
echo "READBACK tree_check $("$PY" -I "$PACK_DIR/files/slim.py" verify "$APPD" "$PACK_DIR/files/package-lock.json" 2>&1 | tr '\n' ' ')"
echo "READBACK new_tree $(wa tree-summary "$APPD/node_modules") pre_slim_kept=$([ -d "$APPD/node_modules.pre-slim" ] && wa tree-summary "$APPD/node_modules.pre-slim" || echo no)"
echo "READBACK build_dir_gone=$([ ! -e "$APPD/.slim" ] && echo yes || echo no) lock_matches=$([ "$(wa sha256 "$APPD/package-lock.json")" = "$(wa sha256 "$PACK_DIR/files/package-lock.json")" ] && echo yes || echo no)"
exit 0
