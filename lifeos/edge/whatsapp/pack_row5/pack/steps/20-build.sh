#!/bin/bash
# LIFE-643 row 5, step 2 of 4: BUILD the slim tree beside the running one by COPYING the installed tree and REMOVING the optional and peer-only packages (the sharp / @img image libraries) listed in the committed lock. No npm runs (the only npm on this host sits in the
# ubuntu account's private home, which the service user cannot read, and root must not run code from there), nothing is downloaded, no install script runs. The running service is not touched; on any failure the build folder is removed.
set -uo pipefail
. "$PACK_DIR/files/common.sh"
APPD="$APP/app"; BUILD="$APPD/.slim"
fail() { echo "READBACK failed=$1"; wa wipe "$BUILD" "$APPD" > /dev/null 2>&1; exit 1; }

mkdir "$BUILD" || fail build_dir
chmod 755 "$BUILD"
cp -a "$APPD/node_modules" "$BUILD/node_modules" || fail copy_tree
"$PY" -I "$PACK_DIR/files/slim.py" prune "$BUILD" "$PACK_DIR/files/package-lock.json" > /dev/null || fail prune
counts=$("$PY" -I "$PACK_DIR/files/slim.py" verify "$BUILD" "$PACK_DIR/files/package-lock.json") || fail "tree_${counts%% *}"
( cd "$BUILD" && as_user "$NEW_USER" env "HOME=$NEW_HOME" "$APP/bin/node" --input-type=module -e 'await import("baileys")' ) > /dev/null 2>&1 || fail library_does_not_load
# the program tree is root-owned and read-only for the service, like the one it replaces
[ -n "$R" ] || chown -R root:root "$BUILD" || fail tree_owner
chmod -R go-w "$BUILD/node_modules"
echo "READBACK slim_tree $counts library_loads_as_new_user=yes npm_run=no install_scripts=none_run $(wa tree-summary "$BUILD/node_modules")"
exit 0
