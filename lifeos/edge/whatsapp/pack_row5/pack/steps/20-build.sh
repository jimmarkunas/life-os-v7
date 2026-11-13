#!/bin/bash
# LIFE-643 row 5, step 2 of 4: BUILD the slim tree beside the running one, as the unprivileged user, from the committed lock, and prove it. The running service is not touched; on any failure the build folder is removed.
set -uo pipefail
. "$PACK_DIR/files/common.sh"
APPD="$APP/app"; BUILD="$APPD/.slim"
fail() { echo "READBACK failed=$1"; wa wipe "$BUILD" "$APPD" > /dev/null 2>&1; exit 1; }
NODE_SRC=$(readlink -f "$OLD_NODE_LINK") || fail node_missing
NPM="$(dirname "$NODE_SRC")/npm"
NODEPATH="$APP/bin:/usr/bin:/bin"

mkdir "$BUILD" || fail build_dir
install -m 644 "$PACK_DIR/files/package.json" "$PACK_DIR/files/package-lock.json" "$BUILD/" || fail copy_manifest
[ -n "$R" ] || chown -R "$NEW_USER:$NEW_USER" "$BUILD" || fail build_owner
chmod 700 "$BUILD"
# exactly these flags: no optional packages (the sharp / @img image libraries), no peer-only packages, no install script runs (baileys' preinstall and protobufjs' postinstall are not needed)
( cd "$BUILD" && as_user "$NEW_USER" env "HOME=$NEW_HOME" "PATH=$NODEPATH" "npm_config_cache=$BUILD/.cache" "$NPM" ci --omit=optional --omit=peer --ignore-scripts ) > /dev/null 2>&1 || fail npm_ci
[ -d "$BUILD/node_modules" ] || fail npm_ci_no_tree
counts=$("$PY" -I "$PACK_DIR/files/slim.py" verify "$BUILD" "$PACK_DIR/files/package-lock.json") || fail "tree_${counts%% *}"
( cd "$BUILD" && as_user "$NEW_USER" env "HOME=$NEW_HOME" "$APP/bin/node" --input-type=module -e 'await import("baileys")' ) > /dev/null 2>&1 || fail library_does_not_load
# the program tree is root-owned and read-only for the service, like the one it replaces
wa wipe "$BUILD/.cache" "$BUILD" > /dev/null 2>&1
[ -n "$R" ] || chown -R root:root "$BUILD" || fail tree_owner
chmod -R go-w "$BUILD/node_modules"
echo "READBACK slim_tree $counts library_loads_as_new_user=yes install_scripts=ignored $(wa tree-summary "$BUILD/node_modules")"
exit 0
