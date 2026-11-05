#!/bin/bash
# LIFE-315 (Jim, Nov 5, 2026: "SOUL files: GitHub master"): put the role files in this repo (hermes/roles/<name>/SOUL.md) on the Oracle host, where Hermes loads them.
#   sudo bash deploy_roles.sh            DRY RUN (default): shows, per file, the current and the repo hash and whether it would change. Writes nothing.
#   sudo bash deploy_roles.sh --apply    backs up every file it will replace into /root/hermes-roles-<time CT>/, installs the repo copy (owner hermes, mode 0600), reads each back.
# Options for tests: --home DIR (the .hermes folder), --backup DIR. It prints names, sizes and hashes only, never file text. It restarts NOTHING: Hermes reads a role file when a session
# starts (UNVERIFIED for sessions already open). It never touches USER.md, memories, config, keys, state or skills.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
HOME_DIR=/var/lib/hermeseval/home/.hermes
APPLY=no; BACKUP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes ;;
    --home) HOME_DIR=${2:?}; shift ;;
    --backup) BACKUP=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
MAP="main:SOUL.md claude-hermes:profiles/claude-hermes/SOUL.md developer:profiles/developer/SOUL.md librarian:profiles/librarian/SOUL.md research:profiles/research/SOUL.md writer:profiles/writer/SOUL.md"
SECRET='sk-[A-Za-z0-9]{10,}|nsec1[a-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|password|token|secret)[[:space:]]*[:=][[:space:]]*[^[:space:]]{6,}'
h12() { sha256sum "$1" | cut -c1-12; }
echo "== $([ "$APPLY" = yes ] && echo APPLY || echo 'DRY RUN (nothing is written)'): repo roles -> $HOME_DIR =="
# 1) every repo file must exist, be small, and be free of anything that looks like a secret, before anything is touched
for pair in $MAP; do
  name=${pair%%:*}; src="$HERE/roles/$name/SOUL.md"
  [ -f "$src" ] || { echo "REFUSED: missing repo file for $name"; exit 1; }
  size=$(wc -c < "$src"); [ "$size" -gt 0 ] && [ "$size" -le 4096 ] || { echo "REFUSED: $name is empty or over 4096 bytes"; exit 1; }
  if grep -E -i -q "$SECRET" "$src"; then echo "REFUSED: $name looks like it contains a secret"; exit 1; fi
done
# 2) compare
changed=0
for pair in $MAP; do
  name=${pair%%:*}; rel=${pair#*:}; src="$HERE/roles/$name/SOUL.md"; dst="$HOME_DIR/$rel"
  cur="missing"; [ -f "$dst" ] && cur=$(h12 "$dst")
  new=$(h12 "$src"); state=SAME; [ "$cur" = "$new" ] || { state=CHANGED; changed=$((changed+1)); }
  printf '  %-14s current=%-12s repo=%-12s %s\n' "$name" "$cur" "$new" "$state"
done
echo "== $changed file(s) would change =="
[ "$APPLY" = yes ] || { echo "Dry run only. To apply: sudo bash $0 --apply"; exit 0; }
[ "$changed" -gt 0 ] || { echo "Nothing to do."; exit 0; }
# 3) apply
[ -n "$BACKUP" ] || BACKUP=/root/hermes-roles-$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
install -d -m 0700 "$BACKUP"
OWNER=""; if [ "$(id -u)" = 0 ] && id hermes >/dev/null 2>&1; then OWNER="-o hermes -g hermes"; fi
for pair in $MAP; do
  name=${pair%%:*}; rel=${pair#*:}; src="$HERE/roles/$name/SOUL.md"; dst="$HOME_DIR/$rel"
  [ -f "$dst" ] && [ "$(h12 "$dst")" = "$(h12 "$src")" ] && continue
  [ -f "$dst" ] && cp -p "$dst" "$BACKUP/$name.SOUL.md"
  # shellcheck disable=SC2086
  install $OWNER -m 0600 "$src" "$dst"
  [ "$(h12 "$dst")" = "$(h12 "$src")" ] || { echo "READ-BACK FAILED for $name; restore from $BACKUP"; exit 1; }
  echo "  installed $name ($(h12 "$dst"))"
done
echo "== read-back OK: every installed file's hash equals the repo hash =="
echo "No restart was done or needed for new sessions."
for pair in $MAP; do
  name=${pair%%:*}; rel=${pair#*:}
  [ -f "$BACKUP/$name.SOUL.md" ] && echo "ROLLBACK $name: cp -p $BACKUP/$name.SOUL.md $HOME_DIR/$rel"
done
exit 0
