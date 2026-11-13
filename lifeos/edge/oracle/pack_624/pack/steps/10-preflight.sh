#!/bin/bash
# LIFE-624 step 2, preflight (changes nothing). Refuses unless: the two installed tools are exactly the old ones (or already the new ones: a re-run), the live tree already holds both the new
# module and the old-path shim (so the unit can import either), and the socket is active.
set -euo pipefail
OPT="${LIFEOS_CLOSE_OPT:-/opt/lifeos-close}"   # the override is for the fixture test; the runner passes a clean environment
sum() { sha256sum "$1" | cut -d' ' -f1; }
for name in install-close.sh close_release.py; do
  have="$(sum "$OPT/bin/$name")"; old="$(awk -v n="$name" '$1=="old"&&$2==n{print $3}' "$PACK_DIR/files/expected.txt")"; new="$(awk -v n="$name" '$1=="new"&&$2==n{print $3}' "$PACK_DIR/files/expected.txt")"
  if [ "$have" = "$old" ]; then echo "READBACK $name=old"; elif [ "$have" = "$new" ]; then echo "READBACK $name=already_new"; else echo "READBACK $name=unexpected"; echo "STOP: $name is neither the old nor the new version"; exit 11; fi
done
for f in lifeos/sources/apps/close_app.py lifeos/sources/close_app.py; do
  [ -s "$OPT/current/$f" ] || { echo "READBACK live_tree_has_$(basename "$(dirname "$f")")_close_app=no"; echo "STOP: the live tree lacks $f (wait for the Close auto-release, then run again)"; exit 12; }
done
echo "READBACK live_tree_has_both_close_app_files=yes"
systemctl is-active --quiet lifeos-close.socket || { echo "STOP: lifeos-close.socket is not active"; exit 13; }
echo "READBACK socket=active"
