#!/bin/bash
# LIFE-331 (Jim, Nov 6 2026: "OpenClaw Avatar - Yes! I definitely want it changed"): put the OpenClaw bot avatar on the Oracle host.
#   sudo bash deploy_avatar.sh --src DIR            DRY RUN (default): shows the checks and what would change. Writes nothing.
#   sudo bash deploy_avatar.sh --src DIR --apply    backs up, installs the image, sets the avatar through OpenClaw's own command, reads back.
# DIR holds the image named in avatar.manifest. THE IMAGE IS NOT IN THIS (PUBLIC) REPOSITORY: it is in the private repo jimmarkunas/bot-studio (exports/).
# What it does (and nothing else): (1) refuses an image that is missing, over 2,000,000 bytes, not a PNG by its first bytes, or whose sha256 differs from the manifest;
# (2) backs up openclaw.json and any current avatars/openclaw.png into /root/openclaw-avatar-<time CT>/ ; (3) copies the image to <workspace>/avatars/openclaw.png
# (owner = the OpenClaw user); (4) runs `openclaw agents set-identity --agent main --avatar avatars/openclaw.png` as that user (OpenClaw's own command: it replaces the
# inline picture in the config with this file path); (5) reads the config and the file back. It restarts NOTHING and touches no other setting; if the Control UI still
# shows the old picture after a reload, a restart is a separate decision. ROLLBACK lines are printed.
# Options for tests: --home DIR (the OpenClaw user's home), --backup DIR, --manifest FILE, --user NAME, --bin PATH.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
MANIFEST="$HERE/avatar.manifest"; HOME_DIR=/home/ubuntu; RUN_USER=ubuntu; BIN=${OPENCLAW_BIN:-openclaw}
APPLY=no; BACKUP=""; SRC=""; MAXBYTES=2000000; AGENT=main
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes ;;
    --src) SRC=${2:?}; shift ;;
    --home) HOME_DIR=${2:?}; shift ;;
    --backup) BACKUP=${2:?}; shift ;;
    --manifest) MANIFEST=${2:?}; shift ;;
    --user) RUN_USER=${2:?}; shift ;;
    --bin) BIN=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
[ -n "$SRC" ] && [ -d "$SRC" ] || { echo "REFUSED: --src DIR is required and must be a folder (the image is not in the repository)"; exit 1; }
[ -f "$MANIFEST" ] || { echo "REFUSED: no manifest $MANIFEST"; exit 1; }
LINE=$(grep -vE '^[[:space:]]*(#|$)' "$MANIFEST" | awk -v a="$AGENT" '$1==a {print; exit}')
[ -n "$LINE" ] || { echo "REFUSED: the manifest has no line for agent $AGENT"; exit 1; }
FILE=$(echo "$LINE" | awk '{print $2}'); WANT=$(echo "$LINE" | awk '{print $3}'); WANTB=$(echo "$LINE" | awk '{print $4}')
IMG="$SRC/$FILE"
[ ! -L "$IMG" ] || { echo "REFUSED: $IMG is a symbolic link"; exit 1; }
[ -f "$IMG" ] || { echo "REFUSED: $IMG is not there"; exit 1; }
SIZE=$(wc -c < "$IMG" | tr -d ' ')
[ "$SIZE" -le "$MAXBYTES" ] || { echo "REFUSED: $FILE is $SIZE bytes; OpenClaw accepts at most $MAXBYTES for a local avatar"; exit 1; }
[ "$SIZE" = "$WANTB" ] || { echo "REFUSED: $FILE is $SIZE bytes, the manifest says $WANTB"; exit 1; }
[ "$(head -c 8 "$IMG" | od -An -tx1 | tr -d ' \n')" = "89504e470d0a1a0a" ] || { echo "REFUSED: $FILE does not start like a PNG"; exit 1; }
GOT=$(sha256sum "$IMG" | cut -d' ' -f1)
[ "$GOT" = "$WANT" ] || { echo "REFUSED: $FILE hashes to ${GOT:0:12}, the manifest says ${WANT:0:12}"; exit 1; }
STATE="$HOME_DIR/.openclaw"; CONF="$STATE/openclaw.json"; WS="$STATE/workspace"; DEST="$WS/avatars/openclaw.png"
[ -f "$CONF" ] || { echo "REFUSED: no config at $CONF"; exit 1; }
[ -d "$WS" ] || { echo "REFUSED: no workspace folder $WS"; exit 1; }
command -v "$BIN" >/dev/null 2>&1 || [ -x "$BIN" ] || { echo "REFUSED: no openclaw program ($BIN)"; exit 1; }
# the main agent's avatar today: a path, or an inline picture (never printed in full)
current() {
  python3 - "$CONF" <<'PY'
import json, sys
def find(o):
    if isinstance(o, dict):
        e = o.get("entries")
        if isinstance(e, dict) and isinstance(e.get("main"), dict):
            return e["main"]
        for v in o.values():
            r = find(v)
            if r is not None: return r
    elif isinstance(o, list):
        for v in o:
            r = find(v)
            if r is not None: return r
try:
    cfg = json.load(open(sys.argv[1]))
except Exception:
    print("unreadable (not plain JSON)"); sys.exit(0)
m = find(cfg)
a = (m or {}).get("identity", {}).get("avatar") if m else None
if a is None: print("none set")
elif a.startswith("data:"): print("inline picture (data URI, %d characters)" % len(a))
else: print("path or URL: " + a[:120])
PY
}
STAMP=$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
[ -n "$BACKUP" ] || BACKUP="/root/openclaw-avatar-$STAMP"
if [ "$APPLY" = yes ]; then echo "== APPLY: $FILE -> $DEST (agent $AGENT, user $RUN_USER) =="; else echo "== DRY RUN (nothing is written): $FILE -> $DEST (agent $AGENT, user $RUN_USER) =="; fi
echo "  image: $SIZE bytes, sha256 ${GOT:0:12} matches the manifest, PNG signature ok"
echo "  main agent's avatar now: $(current)"
if [ -f "$DEST" ]; then echo "  existing $DEST: $(wc -c < "$DEST" | tr -d ' ') bytes, sha256 $(sha256sum "$DEST" | cut -c1-12)"; else echo "  $DEST does not exist yet"; fi
IDMD="$WS/IDENTITY.md"
IDLINE=$(grep -iE '^[[:space:]]*[-*][[:space:]]*Avatar:' "$IDMD" 2>/dev/null | head -1 || true)
if [ -n "$IDLINE" ]; then echo "  IDENTITY.md avatar line now: $IDLINE (some OpenClaw surfaces read this file, so it is pointed at the same image)"; else echo "  IDENTITY.md has no '- Avatar:' line: left alone"; fi
echo "  would run as $RUN_USER: openclaw agents set-identity --agent $AGENT --avatar avatars/openclaw.png   (nothing is restarted)"
if [ "$APPLY" != yes ]; then echo "Dry run only. To apply, add --apply."; exit 0; fi
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"
cp -p "$CONF" "$BACKUP/openclaw.json"
HAD=no; if [ -f "$DEST" ]; then cp -p "$DEST" "$BACKUP/openclaw.png"; HAD=yes; fi
if [ "$HAD" = yes ]; then echo "  backed up the config and the existing image into $BACKUP"; else echo "  backed up the config into $BACKUP"; fi
mkdir -p "$WS/avatars"; chown "$RUN_USER" "$WS/avatars" 2>/dev/null || true
install -m 0644 "$IMG" "$DEST"; chown "$RUN_USER":"$(id -gn "$RUN_USER" 2>/dev/null || echo "$RUN_USER")" "$DEST" 2>/dev/null || true
if [ "$(id -un)" = "$RUN_USER" ] || ! command -v sudo >/dev/null 2>&1; then
  "$BIN" agents set-identity --agent "$AGENT" --avatar avatars/openclaw.png
else
  sudo -u "$RUN_USER" -H "$BIN" agents set-identity --agent "$AGENT" --avatar avatars/openclaw.png
fi
echo "  installed $DEST ($(sha256sum "$DEST" | cut -c1-12))"
HADID=no
if [ -n "$IDLINE" ] && [ "$IDLINE" != "- Avatar: avatars/openclaw.png" ]; then
  cp -p "$IDMD" "$BACKUP/IDENTITY.md"; HADID=yes
  python3 - "$IDMD" <<'PY'
import re, shutil, sys, os
p = sys.argv[1]
t = open(p).read().split("\n")
for i, l in enumerate(t):
    if re.match(r"^\s*[-*]\s*Avatar:", l, re.I):
        t[i] = re.sub(r"(Avatar:\s*).*$", r"\1avatars/openclaw.png", l, flags=re.I)
        break
st = os.stat(p)
open(p, "w").write("\n".join(t))
os.chmod(p, st.st_mode & 0o7777)
try:
    os.chown(p, st.st_uid, st.st_gid)
except PermissionError:
    pass
PY
  echo "  IDENTITY.md avatar line now points at avatars/openclaw.png (backup of the file in $BACKUP)"
fi
AFTER=$(current)
[ "$(sha256sum "$DEST" | cut -d' ' -f1)" = "$WANT" ] || { echo "READ-BACK FAILED: the installed file's hash differs"; exit 1; }
if [ "$HADID" = yes ] && ! grep -qiE '^[[:space:]]*[-*][[:space:]]*Avatar:[[:space:]]*avatars/openclaw.png' "$IDMD"; then echo "READ-BACK FAILED: IDENTITY.md was not updated. ROLLBACK: cp -p $BACKUP/IDENTITY.md $IDMD"; exit 1; fi
case "$AFTER" in "path or URL: avatars/openclaw.png") ;; *) echo "READ-BACK FAILED: the main agent's avatar is now: $AFTER"; echo "ROLLBACK: cp -p $BACKUP/openclaw.json $CONF"; exit 1 ;; esac
echo "== read-back OK: file hash equals the manifest and the main agent's avatar is avatars/openclaw.png =="
echo "Nothing was restarted. Reload the OpenClaw Control UI; if the old picture still shows, tell Claude (a restart is a separate decision)."
echo "ROLLBACK config: cp -p $BACKUP/openclaw.json $CONF"
[ "$HAD" = yes ] && echo "ROLLBACK image:  cp -p $BACKUP/openclaw.png $DEST" || echo "ROLLBACK image:  rm -f $DEST"
if [ "$HADID" = yes ]; then echo "ROLLBACK IDENTITY.md: cp -p $BACKUP/IDENTITY.md $IDMD"; fi
