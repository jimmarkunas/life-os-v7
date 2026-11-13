#!/bin/bash
# LIFE-652: put the fixed off-site backup program (an optional database that cannot be snapshotted is counted and skipped; a required one still fails the run) on Oracle. One file: /opt/lifeos-offsite/offsite_backup.py.
#   1 gates (read-only): the host file is exactly the main version this pack was built against (or already the new one); the backup service is not running; the new file parses.
#   2 install: owner and mode kept, atomic rename, the old file kept in a root-only temp copy.
#   3 proof: `offsite_backup.py dry` (real snapshots of every listed database, encrypted copy made and deleted, no Drive, no status file) must answer dry_ok; if not, the old file is put back.
# No database content, path or credential is read into this script's output: fixed words and counts only. The nightly timer is not touched.
#   install.sh [--dry-run]     --dry-run runs only the gates
# Test hooks (the host leaves them unset): IN_ROOT (prefix), IN_PY, IN_DRY (command that replaces the real dry proof), IN_SYSTEMCTL.
set -u
R=${IN_ROOT:-}
PY=${IN_PY:-/usr/bin/python3}
SYSC=${IN_SYSTEMCTL:-systemctl}
HERE=$(cd "$(dirname "$0")" && pwd)
NEW="$HERE/offsite_backup.py"
HOST="$R/opt/lifeos-offsite/offsite_backup.py"
OLD_SHA=$(cat "$HERE/old.sha256")
DRY=no; [ "${1:-}" = "--dry-run" ] && DRY=yes
say() { echo "READBACK $1=$2"; }
fail() { say failed "$1"; exit 1; }
sha() { "$PY" -I -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }

[ -f "$HOST" ] && [ ! -L "$HOST" ] || fail host_file_missing
[ -f "$NEW" ] || fail pack_file_missing
have=$(sha "$HOST"); want=$(sha "$NEW")
if [ "$have" = "$want" ]; then say host already_new; exit 0; fi
[ "$have" = "$OLD_SHA" ] || fail host_differs_from_main
"$PY" -I -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$NEW" || fail new_file_does_not_parse
[ "$("$SYSC" is-active lifeos-offsite-backup.service 2>/dev/null)" != active ] || fail backup_running
say gates "host=old_main new=parses service=idle"
[ "$DRY" = no ] || { say result dry_run_only; exit 0; }

SAVE=$(mktemp -d) || fail temp_dir; chmod 700 "$SAVE"
cp -p "$HOST" "$SAVE/old"
TMP="$HOST.new"
put_back() { cp -p "$SAVE/old" "$TMP" && mv -f "$TMP" "$HOST"; }
install -m 644 "$NEW" "$TMP" && { [ -n "$R" ] || chown root:root "$TMP"; } && mv -f "$TMP" "$HOST" || { rm -f "$TMP"; fail install; }
if [ -n "${IN_DRY:-}" ]; then out=$("$IN_DRY" 2>/dev/null | tail -1); else out=$(cd / && "$PY" -I "$HOST" dry 2>/dev/null | tail -1); fi
case "$out" in
  dry_ok*) say proof "$out" ;;
  *) put_back; say failed "dry_proof_${out%% *}_old_file_restored"; rm -rf "$SAVE"; exit 1 ;;
esac
[ "$(sha "$HOST")" = "$want" ] || { put_back; rm -rf "$SAVE"; fail installed_hash_differs; }
rm -rf "$SAVE"
say result installed
