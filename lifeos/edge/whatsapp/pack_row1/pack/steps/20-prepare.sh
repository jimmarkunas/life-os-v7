#!/bin/bash
# LIFE-643 row 1, step 2 of 4: PREPARE. Nothing here touches the running old service or its session: it makes the new user, the read-only program tree, the two unit files and the repointed
# consumer files. Every part is harmless if the pack stops here (the old unit keeps running, the new unit is neither enabled nor started). The runner puts the two consumer files back on a failure.
set -uo pipefail
. "$PACK_DIR/files/common.sh"
fail() { echo "READBACK failed=$1"; exit 1; }
NODE_SRC=$(readlink -f "$OLD_NODE_LINK") || fail node_missing

# (a) the user: system account, no login shell, own group, home 700
if ! cmd getent passwd "$NEW_USER" > /dev/null 2>&1; then
  cmd useradd --system --user-group --home-dir /var/lib/lifeos-wa --create-home --shell /usr/sbin/nologin --comment "LIFE OS WhatsApp Attention (LIFE-643)" "$NEW_USER" || fail useradd
fi
NEW_UID=$(cmd id -u "$NEW_USER") && NEW_GID=$(cmd id -g "$NEW_USER") || fail new_user_ids
[ "$NEW_UID" != 0 ] && [ "$NEW_UID" != "$OWNER_UID" ] || fail new_user_wrong_id
[ -d "$NEW_HOME" ] || fail home
[ -n "$R" ] || chown "$NEW_UID:$NEW_GID" "$NEW_HOME" || fail home_owner
chmod 700 "$NEW_HOME"
echo "READBACK new_user_uid_is_not_ubuntu=$([ "$NEW_UID" != "$OWNER_UID" ] && echo yes || echo no) home_mode=$(wa mode "$NEW_HOME")"

# (b) the program tree: root-owned and read-only for the service. node (already on this host, from the OpenClaw tools folder), the four app files (from this commit), the pinned Baileys tree
#     copied byte-for-byte from the installed one whose package-lock.json the preflight proved equal. No download, no install script, no new third-party code.
install -d -m 755 "$APP" "$APP/bin" "$APP/app" || fail app_dirs
[ -n "$R" ] || chown root:root "$APP" "$APP/bin" "$APP/app"
install -m 755 "$NODE_SRC" "$APP/bin/node" || fail node_copy
install -m 755 "$PACK_DIR/files/resume-old.sh" "$APP/bin/resume-old.sh" || fail resume_copy
for f in attention.mjs state.mjs package.json package-lock.json; do
  install -m 644 "$PACK_DIR/files/$f" "$APP/app/$f" || fail "copy_$f"
done
[ -n "$R" ] || chown -R root:root "$APP"
if [ ! -d "$APP/app/node_modules" ]; then
  wa install-tree "$OLD_APP/node_modules" "$APP/app/node_modules" > /dev/null || fail tree_copy
fi
want=$(wa tree-summary "$OLD_APP/node_modules"); have=$(wa tree-summary "$APP/app/node_modules")
[ "$want" = "$have" ] || fail tree_differs
echo "READBACK program_tree $have node=$("$APP/bin/node" --version 2>/dev/null)"

# (c) the new user can run it (no session needed): node starts and the pinned library loads
as_user "$NEW_USER" env "HOME=$NEW_HOME" "$APP/bin/node" --version > /dev/null || fail node_as_new_user
( cd "$APP/app" && as_user "$NEW_USER" env "HOME=$NEW_HOME" "$APP/bin/node" --input-type=module -e 'await import("baileys")' ) > /dev/null 2>&1 || fail library_as_new_user

# (d) the unit files; reload; neither is enabled or started
install -m 644 "$PACK_DIR/files/lifeos-wa-attention.service" "$UNITDIR/lifeos-wa-attention.service" || fail unit
install -m 644 "$PACK_DIR/files/lifeos-wa-attention.socket" "$UNITDIR/lifeos-wa-attention.socket" || fail socket_unit
sysc daemon-reload || fail reload
if cmd systemd-analyze --version > /dev/null 2>&1; then
  cmd systemd-analyze verify "$UNITDIR/lifeos-wa-attention.service" > /dev/null 2>&1 && echo "READBACK unit_verify=ok" || echo "READBACK unit_verify=warnings"
fi

# (e) the consumer: the endpoint tries /run/lifeos-wa first and falls back to the old socket path; the Oracle card counts either unit. Kept as ubuntu's own, 600.
OWN="-o ubuntu -g ubuntu"; [ -z "$R" ] || OWN=""
for pair in "alerts_endpoint.py:$ENDPOINT" "stats.mjs:$STATS"; do
  install $OWN -m 600 "$PACK_DIR/files/${pair%%:*}" "${pair#*:}" || fail "consumer_${pair%%:*}"
done
"$PY" -I -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$ENDPOINT" || fail endpoint_syntax
"$APP/bin/node" --check "$STATS" || fail stats_syntax
echo "READBACK consumer_files=installed old_unit_untouched=yes"
exit 0
