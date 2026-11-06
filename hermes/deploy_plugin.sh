#!/bin/bash
# LIFE-320/321: install the connector-allowlist plugin (hermes/plugins/connector-allowlist/) into Hermes profiles on the Oracle host, each with its own allowlist.
#   sudo bash deploy_plugin.sh --all                                   DRY RUN: every profile in hermes/connector-allowlists.txt (main included). Writes nothing.
#   sudo bash deploy_plugin.sh --all --apply                           backs up what it replaces into /root/hermes-plugin-<time CT>/, installs (owner hermes, files 0600, folders 0700), reads back.
#   sudo bash deploy_plugin.sh --profile librarian --allow notion      ONE profile with an explicit list (the LIFE-320 form); `--profile main` is the default profile.
# `ALL` in the table means that bot is deliberately NOT limited: nothing is installed in it (and it is never touched). Names: `notion` allows that Nous connector, `mcp:todoist` allows that MCP server; everything else is refused. An empty list (`--allow none` or an empty table row) allows nothing.
# Options for tests: --home DIR (the .hermes folder), --backup DIR, --table FILE. It prints names and hashes only. It does NOT enable the plugin, touch config.yaml or toolsets, or restart anything:
# the exact enable lines are printed at the end, and a Desktop server restart plus the live refusal test follow (docs/hermes-files.md rule 4).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/plugins/connector-allowlist"
HOME_DIR=/var/lib/hermeseval/home/.hermes
TABLE="$HERE/connector-allowlists.txt"
APPLY=no; BACKUP=""; PROFILE=""; ALLOW=""; ALL=no; HAVE_ALLOW=no
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes ;;
    --all) ALL=yes ;;
    --profile) PROFILE=${2:?}; shift ;;
    --allow) ALLOW=${2:?}; HAVE_ALLOW=yes; shift ;;
    --home) HOME_DIR=${2:?}; shift ;;
    --backup) BACKUP=${2:?}; shift ;;
    --table) TABLE=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
NAMERE='^[a-z0-9][a-z0-9_-]{0,63}$'
ITEMRE='^(mcp:)?[a-z0-9][a-z0-9_-]{0,63}$'
for f in plugin.yaml __init__.py; do [ -s "$SRC/$f" ] || { echo "REFUSED: repo file $f is missing or empty"; exit 1; }; done
# 1) the list of (profile, names) to deploy
TARGETS=""; SEEN=""; EXEMPT=""
if [ "$ALL" = yes ]; then
  [ -z "$PROFILE" ] && [ "$HAVE_ALLOW" = no ] || { echo "REFUSED: --all cannot be combined with --profile or --allow"; exit 1; }
  [ -f "$TABLE" ] || { echo "REFUSED: no table $TABLE"; exit 1; }
  while IFS= read -r line; do
    line=${line%%#*}; [ -n "$(echo "$line" | tr -d '[:space:]')" ] || continue
    case "$line" in *:*) ;; *) echo "REFUSED: bad table line '$line'"; exit 1 ;; esac
    name=$(echo "${line%%:*}" | tr -d '[:space:]'); names=$(echo "${line#*:}" | tr -d '[:space:]')
    echo "$name" | grep -E -q "$NAMERE" || { echo "REFUSED: '$name' is not a profile name"; exit 1; }
    if printf '%s' "$SEEN" | grep -q "^$name\$"; then echo "REFUSED: profile '$name' is listed twice"; exit 1; fi
    SEEN="$SEEN$name
"
    if [ "$names" = ALL ]; then EXEMPT="$EXEMPT $name"; continue; fi
    TARGETS="$TARGETS$name:$names
"
  done < "$TABLE"
else
  echo "$PROFILE" | grep -E -q "$NAMERE" || { echo "REFUSED: --profile NAME is required (letters, digits, - or _), or use --all"; exit 1; }
  [ "$HAVE_ALLOW" = yes ] && [ -n "$ALLOW" ] || { echo "REFUSED: --allow NAMES is required (for example notion, or none)"; exit 1; }
  [ "$ALLOW" = none ] && ALLOW=""
  TARGETS="$PROFILE:$ALLOW
"
fi
[ -n "$TARGETS" ] || { echo "REFUSED: nothing to deploy"; exit 1; }
# 2) validate every name and every folder before anything is touched
pdir_of() { if [ "$1" = main ]; then echo "$HOME_DIR"; else echo "$HOME_DIR/profiles/$1"; fi; }
list_of() { local out="" i; local old=$IFS; IFS=,; for i in $1; do [ -n "$i" ] || continue; echo "$i" | grep -E -q "$ITEMRE" || { IFS=$old; echo "REFUSED: '$i' is not an allowed name" >&2; return 1; }; out="$out$i
"; done; IFS=$old; printf '%s' "$out"; }
while IFS= read -r t; do
  [ -n "$t" ] || continue
  p=${t%%:*}; n=${t#*:}
  [ -d "$(pdir_of "$p")" ] || { echo "REFUSED: no profile folder $(pdir_of "$p")"; exit 1; }
  list_of "$n" > /dev/null || exit 1
done <<EOF2
$TARGETS
EOF2
h12() { sha256sum "$1" | cut -c1-12; }
WANTDIR=$(mktemp -d); trap 'rm -rf "$WANTDIR"' EXIT
echo "== $([ "$APPLY" = yes ] && echo APPLY || echo 'DRY RUN (nothing is written)'): connector-allowlist -> $(echo "$TARGETS" | grep -c .) profile(s) =="
[ -z "$EXEMPT" ] || echo " not limited by decision (ALL in the table, nothing installed or touched):$EXEMPT"
# 3) compare
changed=0
while IFS= read -r t; do
  [ -n "$t" ] || continue
  p=${t%%:*}; n=${t#*:}; dst="$(pdir_of "$p")/plugins/connector-allowlist"
  list_of "$n" > "$WANTDIR/$p.allowlist"
  shown=$(echo "$n" | tr ',' ' '); [ -n "$shown" ] || shown="(nothing)"
  echo " profile $p, allowed: $shown"
  for f in plugin.yaml __init__.py allowlist.txt; do
    src="$SRC/$f"; [ "$f" = allowlist.txt ] && src="$WANTDIR/$p.allowlist"
    cur=missing; [ -f "$dst/$f" ] && cur=$(h12 "$dst/$f")
    new=$(h12 "$src"); state=SAME; [ "$cur" = "$new" ] || { state=CHANGED; changed=$((changed+1)); }
    printf '   %-14s current=%-12s repo=%-12s %s\n' "$f" "$cur" "$new" "$state"
  done
done <<EOF2
$TARGETS
EOF2
echo "== $changed file(s) would change =="
if [ "$APPLY" != yes ]; then echo "Dry run only. To apply, add --apply."; exit 0; fi
if [ "$changed" -eq 0 ]; then echo "Nothing to do."; exit 0; fi
# 4) apply
[ -n "$BACKUP" ] || BACKUP=/root/hermes-plugin-$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
OWNER=""; if [ "$(id -u)" = 0 ] && id hermes >/dev/null 2>&1; then OWNER="-o hermes -g hermes"; fi
made=no
while IFS= read -r t; do
  [ -n "$t" ] || continue
  p=${t%%:*}; pd=$(pdir_of "$p"); dst="$pd/plugins/connector-allowlist"
  for f in plugin.yaml __init__.py allowlist.txt; do
    src="$SRC/$f"; [ "$f" = allowlist.txt ] && src="$WANTDIR/$p.allowlist"
    [ -f "$dst/$f" ] && [ "$(h12 "$dst/$f")" = "$(h12 "$src")" ] && continue
    [ "$made" = yes ] || { install -d -m 0700 "$BACKUP"; made=yes; }
    # shellcheck disable=SC2086
    install -d $OWNER -m 0700 "$pd/plugins" "$dst"
    [ -f "$dst/$f" ] && { install -d -m 0700 "$BACKUP/$p"; cp -p "$dst/$f" "$BACKUP/$p/$f"; }
    # shellcheck disable=SC2086
    install $OWNER -m 0600 "$src" "$dst/$f"
    [ "$(h12 "$dst/$f")" = "$(h12 "$src")" ] || { echo "READ-BACK FAILED for $p/$f; restore from $BACKUP"; exit 1; }
    echo "  installed $p/$f ($(h12 "$dst/$f"))"
  done
done <<EOF2
$TARGETS
EOF2
echo "== read-back OK: every installed file's hash equals the repo hash =="
echo "Nothing was enabled or restarted. Next, in this order (each its own step):"
echo "  1) enable, once per profile that does not have it yet (hermes plugins list shows it):"
while IFS= read -r t; do
  [ -n "$t" ] || continue
  p=${t%%:*}
  if [ "$p" = main ]; then echo "       hermes plugins enable connector-allowlist"; else echo "       hermes --profile $p plugins enable connector-allowlist"; fi
done <<EOF2
$TARGETS
EOF2
echo "  2) sudo systemctl restart hermes-eval-backend"
echo "  3) live refusal test in each bot, then: bash check_profiles.sh"
while IFS= read -r t; do
  [ -n "$t" ] || continue
  p=${t%%:*}; dst="$(pdir_of "$p")/plugins/connector-allowlist"
  for f in plugin.yaml __init__.py allowlist.txt; do
    [ -f "$BACKUP/$p/$f" ] && echo "ROLLBACK $p/$f: cp -p $BACKUP/$p/$f $dst/$f"
  done
done <<EOF2
$TARGETS
EOF2
exit 0
