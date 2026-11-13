#!/bin/bash
# LIFE-518 step 3 of 3: read `claude auth status` again and print the answer as dates, yes/no and a code only. Step 4 (re-sign-in) is never done. Removes its scratch folder.
set -uo pipefail
R=$(cat "$PACK_DIR/files/ROOT" 2>/dev/null || true)
S="$R/tmp/lifeos-life518"
mkdir -p "$S"
"$R/usr/bin/timeout" 60 "$R/usr/local/lib/claude-handoff/client.py" auth status < /dev/null 2> /dev/null | python3 -I "$PACK_DIR/files/parse.py" status > "$S/after" 2> /dev/null
pick() { grep -m1 "^$2=" "$S/$1" 2> /dev/null | cut -d= -f2- | grep -E -x "$3" || echo "$4"; }
DATE='[0-9]{4}-[0-9]{2}-[0-9]{2}'
before=$(pick before date "$DATE" unknown)
after=$(pick after date "$DATE" unknown)
method=$(pick after method '[a-z_./]{1,40}' unknown)
[ "$method" != unknown ] || method=$(pick before method '[a-z_./]{1,40}' unknown)
if [ "$before" = unknown ] || [ "$after" = unknown ]; then changed=unknown; elif [ "$before" = "$after" ]; then changed=no; else changed=yes; fi
ok=$(pick message_ok ok 'yes|no' no)
code=$(grep -E -x '[0-9]{1,3}' "$S/message_exit" 2> /dev/null | head -1)
echo "READBACK expiry_before=$before"
echo "READBACK expiry_after=$after"
echo "READBACK expiry_changed=$changed"
echo "READBACK message_ok=$ok"
echo "READBACK message_exit=${code:-none}"
echo "READBACK method=$method"
rm -rf "$S"
exit 0
