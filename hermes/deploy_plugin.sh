#!/bin/bash
# LIFE-320: install the connector-allowlist plugin (hermes/plugins/connector-allowlist/) into ONE Hermes profile on the Oracle host, with that profile's allowlist.
#   sudo bash deploy_plugin.sh --profile librarian --allow notion            DRY RUN (default): shows what would change. Writes nothing.
#   sudo bash deploy_plugin.sh --profile librarian --allow notion --apply    backs up what it replaces into /root/hermes-plugin-<time CT>/, installs (owner hermes, files 0600, folder 0700), reads back.
# --allow takes names separated by commas (letters, digits, - or _): notion allows that connector, mcp:todoist allows that MCP server (everything else is refused). Options for tests: --home DIR (the .hermes folder), --backup DIR.
# It prints names and hashes only. It does NOT enable the plugin, does NOT touch config.yaml or toolsets, and restarts NOTHING.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/plugins/connector-allowlist"
HOME_DIR=/var/lib/hermeseval/home/.hermes
APPLY=no; BACKUP=""; PROFILE=""; ALLOW=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes ;;
    --profile) PROFILE=${2:?}; shift ;;
    --allow) ALLOW=${2:?}; shift ;;
    --home) HOME_DIR=${2:?}; shift ;;
    --backup) BACKUP=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
echo "$PROFILE" | grep -E -q '^[a-z0-9][a-z0-9_-]{0,63}$' || { echo "REFUSED: --profile NAME is required (letters, digits, - or _)"; exit 1; }
[ -n "$ALLOW" ] || { echo "REFUSED: --allow NAMES is required (for example notion)"; exit 1; }
LIST=""
OLDIFS=$IFS; IFS=,
for item in $ALLOW; do
  echo "$item" | grep -E -q '^(mcp:)?[a-z0-9][a-z0-9_-]{0,63}$' || { IFS=$OLDIFS; echo "REFUSED: '$item' is not a connector name"; exit 1; }
  LIST="$LIST$item
"
done
IFS=$OLDIFS
PDIR="$HOME_DIR/profiles/$PROFILE"
DST="$PDIR/plugins/connector-allowlist"
[ -d "$PDIR" ] || { echo "REFUSED: no profile folder $PDIR"; exit 1; }
for f in plugin.yaml __init__.py; do [ -s "$SRC/$f" ] || { echo "REFUSED: repo file $f is missing or empty"; exit 1; }; done
h12() { sha256sum "$1" | cut -c1-12; }
WANT=$(mktemp); trap 'rm -f "$WANT"' EXIT
printf '%s' "$LIST" > "$WANT"
echo "== $([ "$APPLY" = yes ] && echo APPLY || echo 'DRY RUN (nothing is written)'): connector-allowlist -> profile $PROFILE, allowed: $(echo "$ALLOW" | tr ',' ' ') =="
changed=0
for f in plugin.yaml __init__.py allowlist.txt; do
  src="$SRC/$f"; [ "$f" = allowlist.txt ] && src="$WANT"
  cur=missing; [ -f "$DST/$f" ] && cur=$(h12 "$DST/$f")
  new=$(h12 "$src"); state=SAME; [ "$cur" = "$new" ] || { state=CHANGED; changed=$((changed+1)); }
  printf '  %-14s current=%-12s repo=%-12s %s\n' "$f" "$cur" "$new" "$state"
done
echo "== $changed file(s) would change =="
[ "$APPLY" = yes ] || { echo "Dry run only. To apply: sudo bash $0 --profile $PROFILE --allow $ALLOW --apply"; exit 0; }
[ "$changed" -gt 0 ] || { echo "Nothing to do."; exit 0; }
[ -n "$BACKUP" ] || BACKUP=/root/hermes-plugin-$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
install -d -m 0700 "$BACKUP"
OWNER=""; if [ "$(id -u)" = 0 ] && id hermes >/dev/null 2>&1; then OWNER="-o hermes -g hermes"; fi
# shellcheck disable=SC2086
install -d $OWNER -m 0700 "$PDIR/plugins" "$DST"
for f in plugin.yaml __init__.py allowlist.txt; do
  src="$SRC/$f"; [ "$f" = allowlist.txt ] && src="$WANT"
  [ -f "$DST/$f" ] && [ "$(h12 "$DST/$f")" = "$(h12 "$src")" ] && continue
  [ -f "$DST/$f" ] && cp -p "$DST/$f" "$BACKUP/$f"
  # shellcheck disable=SC2086
  install $OWNER -m 0600 "$src" "$DST/$f"
  [ "$(h12 "$DST/$f")" = "$(h12 "$src")" ] || { echo "READ-BACK FAILED for $f; restore from $BACKUP"; exit 1; }
  echo "  installed $f ($(h12 "$DST/$f"))"
done
echo "== read-back OK: every installed file's hash equals the repo hash =="
echo "Not enabled and nothing restarted: enabling the plugin and the toolset are separate, approved steps."
for f in plugin.yaml __init__.py allowlist.txt; do
  [ -f "$BACKUP/$f" ] && echo "ROLLBACK $f: cp -p $BACKUP/$f $DST/$f"
done
echo "REMOVE (if there was no earlier copy): rm -r $DST"
