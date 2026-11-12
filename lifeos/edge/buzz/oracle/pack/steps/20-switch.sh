#!/bin/bash
# LIFE-590 step 2 of 3: install the unit (ExecStart -m lifeos.buzz.mcp), point `current` at the new release, restart ONLY lifeos-buzz-mcp, and prove it answers.
# Any failure, Ctrl-C or SIGTERM puts the old unit text and `current` -> releases/c423a4434c4c back and restarts the service on it (the runner cannot restore a symlink, so this step does).
set -uo pipefail
R=$(cat "$PACK_DIR/files/ROOT" 2>/dev/null || true)
REL=$(cat "$PACK_DIR/files/RELEASE")
BASE="$R/opt/lifeos-buzz-mcp"; CUR="$BASE/current"; UNIT="$R/etc/systemd/system/lifeos-buzz-mcp.service"
SVC=lifeos-buzz-mcp.service; SYSTEMCTL="$R/usr/bin/systemctl"; CURL="$R/usr/bin/curl"
OLD_TARGET="releases/c423a4434c4c"; NEW_TARGET="releases/$REL"
OLDUNIT=$(mktemp); cp -p "$UNIT" "$OLDUNIT" || { echo "READBACK old_unit=unreadable"; exit 1; }
done=no
rollback() {
  [ "$done" = yes ] && return 0
  ln -sfn "$OLD_TARGET" "$CUR"; cp -p "$OLDUNIT" "$UNIT"
  "$SYSTEMCTL" daemon-reload; "$SYSTEMCTL" restart "$SVC"
  echo "READBACK rolled_back=yes current=$(readlink "$CUR")"
}
fail() { echo "READBACK failed=$1"; rollback; rm -f "$OLDUNIT"; exit 1; }
trap 'rollback; rm -f "$OLDUNIT"; exit 1' INT TERM HUP
[ "$(readlink "$CUR")" = "$OLD_TARGET" ] || { echo "READBACK current=unexpected"; rm -f "$OLDUNIT"; exit 1; }
install -m 644 "$PACK_DIR/files/lifeos-buzz-mcp.service" "$UNIT" || fail unit
ln -sfn "$NEW_TARGET" "$CUR" || fail link
"$SYSTEMCTL" daemon-reload || fail reload
"$SYSTEMCTL" restart "$SVC" || fail restart
code=000
for _ in $(seq 1 20); do
  code=$("$CURL" -s -o /dev/null -w '%{http_code}' -X POST --max-time 3 http://127.0.0.1:8787/mcp -d '{}' 2>/dev/null || true)
  [ "$code" = 401 ] && break
  "$R/usr/bin/sleep" 1
done
[ "$code" = 401 ] || fail "http_$code"
pid=$("$SYSTEMCTL" show -p MainPID --value "$SVC")
tr '\0' ' ' < "$R/proc/$pid/cmdline" 2>/dev/null | grep -q 'lifeos\.buzz\.mcp' || fail cmdline
done=yes; rm -f "$OLDUNIT"
echo "READBACK switched=yes current=$(readlink "$CUR")"
echo "READBACK http_mcp=$code"
