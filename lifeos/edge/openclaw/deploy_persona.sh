#!/bin/bash
# LIFE-348 (Jim, Nov 6 2026: "update the personalities of both Hermes and OpenClaw to be like Muse AI"): put OpenClaw's persona text on the Oracle host.
#   sudo bash deploy_persona.sh             DRY RUN (default): shows the checks and whether the file would change. Writes nothing.
#   sudo bash deploy_persona.sh --apply     backs up the current SOUL.md, installs the repo text, reads back the hash.
# Source: persona/SOUL.md next to this script (public, PII-free; condensed from muse-persona-export). Target: <home>/.openclaw/workspace/SOUL.md.
# What it does (and nothing else): (1) refuses a source that is missing, a symbolic link, empty, over 8,000 bytes, without a final newline, that lacks the existing rules
# ("approved scope", "Never send half-baked"), or that looks like it holds a secret; (2) backs up the current SOUL.md into /root/openclaw-persona-<time CT>/ ;
# (3) installs the source with the current file's owner and mode; (4) reads the hash back. It never prints the current file's text (only size and hash), never touches
# IDENTITY.md (its Avatar line belongs to LIFE-331), openclaw.json or any other file, and restarts NOTHING: OpenClaw reads SOUL.md when a session starts (UNVERIFIED), so
# a NEW OpenClaw chat is the test; if an old chat keeps the old voice, that is expected. A ROLLBACK line is printed.
# Options for tests: --home DIR, --backup DIR, --src FILE, --user NAME.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/persona/SOUL.md"; HOME_DIR=/home/ubuntu; RUN_USER=ubuntu; APPLY=no; BACKUP=""; MAXBYTES=8000
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes ;;
    --src) SRC=${2:?}; shift ;;
    --home) HOME_DIR=${2:?}; shift ;;
    --backup) BACKUP=${2:?}; shift ;;
    --user) RUN_USER=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
[ ! -L "$SRC" ] || { echo "REFUSED: $SRC is a symbolic link"; exit 1; }
[ -f "$SRC" ] || { echo "REFUSED: no source file $SRC"; exit 1; }
SIZE=$(wc -c < "$SRC" | tr -d ' ')
[ "$SIZE" -gt 0 ] || { echo "REFUSED: the source file is empty"; exit 1; }
[ "$SIZE" -le "$MAXBYTES" ] || { echo "REFUSED: the source is $SIZE bytes (limit $MAXBYTES)"; exit 1; }
[ "$(tail -c 1 "$SRC" | od -An -tx1 | tr -d ' \n')" = "0a" ] || { echo "REFUSED: the source does not end with a newline"; exit 1; }
grep -q "approved scope" "$SRC" && grep -q "Never send half-baked" "$SRC" || { echo "REFUSED: the source lacks OpenClaw's existing rules (approved scope, never send half-baked)"; exit 1; }
if grep -qiE 'sk-[A-Za-z0-9]{10,}|nsec1[a-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{10,}|github_pat_|(api[_-]?key|password|token|secret)[[:space:]]*[:=][[:space:]]*[^[:space:]]{6,}' "$SRC"; then
  echo "REFUSED: the source looks like it holds a secret"; exit 1
fi
WS="$HOME_DIR/.openclaw/workspace"; DEST="$WS/SOUL.md"
[ -d "$WS" ] || { echo "REFUSED: no workspace folder $WS"; exit 1; }
WANT=$(sha256sum "$SRC" | cut -d' ' -f1)
STAMP=$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
[ -n "$BACKUP" ] || BACKUP="/root/openclaw-persona-$STAMP"
if [ "$APPLY" = yes ]; then echo "== APPLY: persona SOUL.md -> $DEST (user $RUN_USER) =="; else echo "== DRY RUN (nothing is written): persona SOUL.md -> $DEST (user $RUN_USER) =="; fi
echo "  source: $SIZE bytes, sha256 ${WANT:0:12}, ends with a newline, keeps the existing rules, no secret-shaped text"
if [ -f "$DEST" ]; then
  HAVE=$(sha256sum "$DEST" | cut -d' ' -f1)
  echo "  current $DEST: $(wc -c < "$DEST" | tr -d ' ') bytes, sha256 ${HAVE:0:12} (text not shown)"
  if [ "$HAVE" = "$WANT" ]; then echo "  SAME: nothing to change"; exit 0; fi
else
  HAVE=""; echo "  $DEST does not exist yet"
fi
echo "  untouched: IDENTITY.md, openclaw.json, every other file; nothing is restarted"
if [ "$APPLY" != yes ]; then echo "Dry run only. To apply, add --apply."; exit 0; fi
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"
if [ -n "$HAVE" ]; then cp -p "$DEST" "$BACKUP/SOUL.md"; echo "  backed up the current file into $BACKUP"; fi
MODE=644; if [ -f "$DEST" ]; then MODE=$(stat -c %a "$DEST" 2>/dev/null || stat -f %Lp "$DEST"); fi
install -m "$MODE" "$SRC" "$DEST"
chown "$RUN_USER":"$(id -gn "$RUN_USER" 2>/dev/null || echo "$RUN_USER")" "$DEST" 2>/dev/null || true
[ "$(sha256sum "$DEST" | cut -d' ' -f1)" = "$WANT" ] || { echo "READ-BACK FAILED: the installed file's hash differs"; [ -n "$HAVE" ] && echo "ROLLBACK: cp -p $BACKUP/SOUL.md $DEST"; exit 1; }
echo "== read-back OK: installed SOUL.md hash ${WANT:0:12} equals the repo file =="
echo "Nothing was restarted. Start a NEW OpenClaw chat to test."
if [ -n "$HAVE" ]; then echo "ROLLBACK: cp -p $BACKUP/SOUL.md $DEST"; else echo "ROLLBACK: rm -f $DEST"; fi
