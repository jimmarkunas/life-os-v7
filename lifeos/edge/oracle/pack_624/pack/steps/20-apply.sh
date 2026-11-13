#!/bin/bash
# LIFE-624 step 2, apply. Takes the release lock (so an auto-release cannot run in the middle), puts the two new tools in /opt/lifeos-close/bin, rewrites the unit with the installer's own `units`
# (the same text the installer writes, with the import path changed), reloads systemd, then proves it: the unit text names the new path only, the live tree imports the new module and the shim, and
# the installer's own `verify` passes (one real request answered). A failure exits non-zero and the runner restores everything it declared.
set -euo pipefail
OPT="${LIFEOS_CLOSE_OPT:-/opt/lifeos-close}"; UNITS="${LIFEOS_CLOSE_UNITS:-/etc/systemd/system}"   # the overrides are for the fixture test; the runner passes a clean environment
exec 9>"$OPT/.lock"; flock -w 120 9 || { echo "STOP: another Close release is running"; exit 21; }
install -d -m 755 "$OPT/bin.bak-LIFE-624"; cp -p "$OPT/bin/install-close.sh" "$OPT/bin/close_release.py" "$OPT/bin.bak-LIFE-624/"
install -m 755 "$PACK_DIR/files/install-close.sh" "$OPT/bin/install-close.sh"
install -m 644 "$PACK_DIR/files/close_release.py" "$OPT/bin/close_release.py"
"$OPT/bin/install-close.sh" units "$UNITS"
systemctl daemon-reload
exec_line="$(grep '^ExecStart=' "$UNITS/lifeos-close@.service")"
echo "READBACK unit_new_import_lines=$(printf '%s\n' "$exec_line" | grep -c 'from lifeos.sources.apps.close_app import handle')"
echo "READBACK unit_old_import_lines=$(printf '%s\n' "$exec_line" | grep -c 'from lifeos.sources.close_app import handle')"
[ "$(printf '%s\n' "$exec_line" | grep -c 'from lifeos.sources.apps.close_app import handle')" = 1 ] || { echo "STOP: the unit does not import the new path"; exit 22; }
[ "$(printf '%s\n' "$exec_line" | grep -c 'from lifeos.sources.close_app import handle')" = 0 ] || { echo "STOP: the unit still names the old path"; exit 23; }
python3 -I -B -c "import sys; sys.path.insert(0, '$OPT/current'); import lifeos.sources.apps.close_app as a, lifeos.sources.close_app as s; assert s.handle is a.handle" || { echo "STOP: the live tree does not import both paths"; exit 24; }
echo "READBACK live_tree_imports_new_and_shim=yes"
"$OPT/bin/install-close.sh" verify > /tmp/lifeos-close-verify-LIFE-624.txt 2>&1 || { echo "STOP: install-close.sh verify failed"; echo "READBACK verify_fail_lines=$(grep -c FAIL /tmp/lifeos-close-verify-LIFE-624.txt || true)"; exit 25; }
echo "READBACK verify_pass_lines=$(grep -c PASS /tmp/lifeos-close-verify-LIFE-624.txt)"
echo "READBACK verify_fail_lines=$(grep -c FAIL /tmp/lifeos-close-verify-LIFE-624.txt || true)"
rm -f /tmp/lifeos-close-verify-LIFE-624.txt
