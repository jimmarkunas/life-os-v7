#!/bin/bash
# LIFE-326 (Jim, Nov 6, 2026: "Approved, go"): put the bot avatars on the Oracle host, where Hermes Desktop shows them.
#   sudo bash deploy_avatars.sh --src DIR            DRY RUN (default): per profile, the current and the manifest hash, and whether it would change. Writes nothing.
#   sudo bash deploy_avatars.sh --src DIR --apply    backs up what it replaces into /root/hermes-avatars-<time CT>/, installs the image (owner hermes, mode 0600), reads it back.
# DIR holds the image files named in hermes/avatars.manifest. THE IMAGES ARE NOT IN THIS (PUBLIC) REPOSITORY: they live in the private repo jimmarkunas/bot-studio (exports/).
# Hermes accepts PNG, JPEG or WebP, 2,000,000 bytes at most, format recognised from the first bytes; an animated PNG is a PNG and plays in Hermes Desktop.
# It is the same file Edit profile > Upload writes: profiles/<name>/assets/avatar.<ext> (`main` = the default profile, <home>/assets/avatar.<ext>). One canonical file per profile.
# Options for tests: --home DIR (the .hermes folder), --backup DIR, --manifest FILE. It prints names, sizes and hashes only, never image content. It restarts NOTHING and touches no config.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
HOME_DIR=/var/lib/hermeseval/home/.hermes
MANIFEST="$HERE/avatars.manifest"
APPLY=no; BACKUP=""; SRC=""
MAXBYTES=2000000
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes ;;
    --src) SRC=${2:?}; shift ;;
    --home) HOME_DIR=${2:?}; shift ;;
    --backup) BACKUP=${2:?}; shift ;;
    --manifest) MANIFEST=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
[ -n "$SRC" ] && [ -d "$SRC" ] || { echo "REFUSED: --src DIR is required and must be a folder (the images are not in the repository)"; exit 1; }
[ -f "$MANIFEST" ] || { echo "REFUSED: no manifest $MANIFEST"; exit 1; }
h64() { sha256sum "$1" | cut -d' ' -f1; }
h12() { sha256sum "$1" | cut -c1-12; }
size_of() { wc -c < "$1" | tr -d ' '; }
ext_of() { # png | jpg | webp, from the first bytes; empty when it is none of them
  local f=$1
  if [ "$(head -c 8 "$f" | od -An -tx1 | tr -d ' \n')" = "89504e470d0a1a0a" ]; then echo png
  elif [ "$(head -c 3 "$f" | od -An -tx1 | tr -d ' \n')" = "ffd8ff" ]; then echo jpg
  elif [ "$(head -c 4 "$f")" = "RIFF" ] && [ "$(dd if="$f" bs=1 skip=8 count=4 2>/dev/null)" = "WEBP" ]; then echo webp
  fi
}
assets_of() { if [ "$1" = main ]; then echo "$HOME_DIR/assets"; else echo "$HOME_DIR/profiles/$1/assets"; fi; }
pdir_of() { if [ "$1" = main ]; then echo "$HOME_DIR"; else echo "$HOME_DIR/profiles/$1"; fi; }
# 1) read the manifest and check EVERYTHING before anything is touched
ROWS=""
seen=""
while IFS= read -r line; do
  line=${line%%#*}; [ -n "$(echo "$line" | tr -d '[:space:]')" ] || continue
  set -- $line
  [ $# -eq 4 ] || { echo "REFUSED: bad manifest line '$line' (need: profile file sha256 bytes)"; exit 1; }
  p=$1; f=$2; sha=$3; bytes=$4
  echo "$p" | grep -E -q '^[a-z0-9][a-z0-9_-]{0,63}$' || { echo "REFUSED: '$p' is not a profile name"; exit 1; }
  echo "$f" | grep -E -q '^[A-Za-z0-9._-]{1,80}$' || { echo "REFUSED: '$f' is not a plain file name"; exit 1; }
  echo "$sha" | grep -E -q '^[0-9a-f]{64}$' || { echo "REFUSED: bad sha256 for $p"; exit 1; }
  echo "$bytes" | grep -E -q '^[0-9]{1,8}$' || { echo "REFUSED: bad size for $p"; exit 1; }
  case " $seen " in *" $p "*) echo "REFUSED: profile '$p' is listed twice"; exit 1 ;; esac
  seen="$seen $p"
  [ -d "$(pdir_of "$p")" ] || { echo "REFUSED: no profile folder $(pdir_of "$p")"; exit 1; }
  src="$SRC/$f"
  [ -f "$src" ] || { echo "REFUSED: $p: file $f is not in $SRC"; exit 1; }
  n=$(size_of "$src")
  [ "$n" -gt 0 ] && [ "$n" -le "$MAXBYTES" ] || { echo "REFUSED: $p: $f is empty or over $MAXBYTES bytes (Hermes refuses it)"; exit 1; }
  [ "$n" = "$bytes" ] || { echo "REFUSED: $p: $f is $n bytes, the manifest says $bytes"; exit 1; }
  ext=$(ext_of "$src"); [ -n "$ext" ] || { echo "REFUSED: $p: $f is not a PNG, JPEG or WebP (Hermes would refuse it)"; exit 1; }
  [ "$(h64 "$src")" = "$sha" ] || { echo "REFUSED: $p: $f does not match the manifest hash"; exit 1; }
  ROWS="$ROWS$p $f $ext $sha
"
done < "$MANIFEST"
[ -n "$ROWS" ] || { echo "REFUSED: the manifest lists nothing"; exit 1; }
echo "== $([ "$APPLY" = yes ] && echo APPLY || echo 'DRY RUN (nothing is written)'): avatars from $SRC -> $HOME_DIR =="
changed=0
while read -r p f ext sha; do
  [ -n "$p" ] || continue
  dst="$(assets_of "$p")/avatar.$ext"; cur=missing; [ -f "$dst" ] && cur=$(h12 "$dst")
  state=SAME; [ "$cur" = "$(echo "$sha" | cut -c1-12)" ] || { state=CHANGED; changed=$((changed+1)); }
  others=""; for o in png jpg webp; do [ "$o" = "$ext" ] || { [ -f "$(assets_of "$p")/avatar.$o" ] && others="$others avatar.$o"; }; done
  printf '  %-14s %-26s %-5s current=%-12s manifest=%-12s %s%s\n' "$p" "$f" "$ext" "$cur" "$(echo "$sha" | cut -c1-12)" "$state" "${others:+  (also replaces:$others)}"
  [ -z "$others" ] || [ "$state" = CHANGED ] || changed=$((changed+1))
done <<EOF2
$ROWS
EOF2
echo "== $changed profile(s) would change =="
[ "$APPLY" = yes ] || { echo "Dry run only. To apply, add --apply."; exit 0; }
[ "$changed" -gt 0 ] || { echo "Nothing to do."; exit 0; }
# 2) apply
[ -n "$BACKUP" ] || BACKUP=/root/hermes-avatars-$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
install -d -m 0700 "$BACKUP"
OWNER=""; if [ "$(id -u)" = 0 ] && id hermes >/dev/null 2>&1; then OWNER="-o hermes -g hermes"; fi
while read -r p f ext sha; do
  [ -n "$p" ] || continue
  a=$(assets_of "$p"); dst="$a/avatar.$ext"
  [ -f "$dst" ] && [ "$(h64 "$dst")" = "$sha" ] && { for o in png jpg webp; do [ "$o" = "$ext" ] || [ ! -f "$a/avatar.$o" ] || { install -d -m 0700 "$BACKUP/$p"; cp -p "$a/avatar.$o" "$BACKUP/$p/avatar.$o"; rm -f "$a/avatar.$o"; echo "  removed stale $p/avatar.$o"; }; done; continue; }
  # shellcheck disable=SC2086
  install -d $OWNER -m 0700 "$a"
  for o in png jpg webp; do [ -f "$a/avatar.$o" ] && { install -d -m 0700 "$BACKUP/$p"; cp -p "$a/avatar.$o" "$BACKUP/$p/avatar.$o"; }; done
  # shellcheck disable=SC2086
  install $OWNER -m 0600 "$SRC/$f" "$a/avatar.$ext.tmp"
  mv -f "$a/avatar.$ext.tmp" "$dst"
  for o in png jpg webp; do [ "$o" = "$ext" ] || rm -f "$a/avatar.$o"; done
  [ "$(h64 "$dst")" = "$sha" ] || { echo "READ-BACK FAILED for $p; restore from $BACKUP/$p"; exit 1; }
  echo "  installed $p/avatar.$ext ($(echo "$sha" | cut -c1-12))"
done <<EOF2
$ROWS
EOF2
echo "== read-back OK: every installed avatar's hash equals the manifest =="
echo "Nothing was restarted. Hermes Desktop shows a new picture the next time it reads the profile (reconnect or reload if it still shows the old one)."
while read -r p f ext sha; do
  [ -n "$p" ] || continue
  for o in png jpg webp; do
    [ -f "$BACKUP/$p/avatar.$o" ] || continue
    undo="cp -p $BACKUP/$p/avatar.$o $(assets_of "$p")/avatar.$o"
    [ "$o" = "$ext" ] || undo="$undo && rm -f $(assets_of "$p")/avatar.$ext"   # only when the new file has another extension than the old one
    echo "ROLLBACK $p: $undo"
  done
done <<EOF2
$ROWS
EOF2
exit 0
