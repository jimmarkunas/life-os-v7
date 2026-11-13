#!/bin/bash
# LIFE-518 step 2 of 3: send ONE fixed tiny message through the same hand-off route Hermes uses (picker mode: model alias haiku, no tools, no session saved, the claude.ai Max login of claude-svc; no API key). Capped at 180 seconds.
# The reply text is never stored or printed: only yes/no and the exit code are kept. A failure here does not stop step 3. No Hermes config or default model is touched.
set -uo pipefail
R=$(cat "$PACK_DIR/files/ROOT" 2>/dev/null || true)
S="$R/tmp/lifeos-life518"
mkdir -p "$S"; chmod 700 "$S"
printf '%s\n' '{"type":"user","message":{"role":"user","content":"Reply with the single word: ok"}}' \
  | "$R/usr/bin/timeout" 180 "$R/usr/local/lib/claude-handoff/client.py" -p --model haiku --input-format stream-json --output-format stream-json --verbose 2> /dev/null > "$S/reply"
echo "${PIPESTATUS[1]}" > "$S/message_exit"
python3 -I "$PACK_DIR/files/parse.py" message < "$S/reply" > "$S/message_ok" 2> /dev/null
rm -f "$S/reply"
echo "READBACK step=message_sent"
exit 0
