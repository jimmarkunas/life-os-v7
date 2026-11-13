#!/bin/bash
# LIFE-518 step 1 of 3: read `claude auth status` through the Hermes hand-off client (the same fixed route Hermes uses to reach the claude-svc login) and keep ONLY the expiry date and the method words in a private scratch folder.
# No sign-in is started or changed. Always exits 0 so the later read-backs still run.
set -uo pipefail
R=$(cat "$PACK_DIR/files/ROOT" 2>/dev/null || true)
S="$R/tmp/lifeos-life518"
rm -rf "$S"; mkdir -p "$S"; chmod 700 "$S"
"$R/usr/bin/timeout" 60 "$R/usr/local/lib/claude-handoff/client.py" auth status < /dev/null 2> /dev/null | python3 -I "$PACK_DIR/files/parse.py" status > "$S/before" 2> /dev/null
echo "READBACK step=before_status_read"
exit 0
