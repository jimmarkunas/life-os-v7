#!/bin/bash
# LIFE-331 (Jim, Nov 6 2026: "OpenClaw Avatar - Yes! I definitely want it changed"; "Go on the OpenClaw read-only check").
# READ-ONLY discovery of where OpenClaw keeps its avatar. It changes NOTHING: no file is written, nothing is restarted, no config is edited.
#   sudo bash discover_avatar.sh            prints what it finds; paste the output back
#   sudo bash discover_avatar.sh --home DIR   use DIR as the service user's home (tests)
# It prints: the OpenClaw program and version, which user runs it, the config file's path/owner/size, and ONLY the config lines that mention avatar, identity,
# emoji, theme or workspace (cut to 160 characters; any line that looks secret-shaped is withheld), the avatar folder's file names and sizes, and IDENTITY.md lines
# about the avatar. It never prints other config lines, the environment, tokens or keys.
set -u
HOME_DIR=""; BIN=${OPENCLAW_BIN:-openclaw}; SS=${SS:-ss}; PS=${PS:-ps}; GETENT=${GETENT:-getent}; PORT=${PORT:-18789}
while [ $# -gt 0 ]; do
  case "$1" in
    --home) HOME_DIR=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
SECRETY='token|secret|passw|apikey|api_key|bearer|credential|private|nsec|sk-|tskey|AIza'
safe() { grep -viE "$SECRETY" | cut -c1-160; }          # a line that looks secret-shaped is dropped, never printed
echo "== OpenClaw avatar discovery (READ-ONLY; nothing is written or restarted) =="
echo "-- program"
PROG=$(command -v "$BIN" 2>/dev/null || true)
if [ -n "$PROG" ]; then echo "binary: $PROG"; else echo "binary: not on PATH for this shell ($BIN)"; fi
if [ -n "$PROG" ]; then TMO=$(command -v timeout 2>/dev/null || true)
  echo "version: $(${TMO:+$TMO 10} "$PROG" --version 2>&1 | head -1 | cut -c1-80)"; fi
echo "-- who runs it"
USER_RUN=""
if [ -z "$HOME_DIR" ]; then
  PID=$("$SS" -ltnp "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  if [ -n "${PID:-}" ]; then
    USER_RUN=$("$PS" -o user= -p "$PID" 2>/dev/null | tr -d ' ')
    HOME_DIR=$("$GETENT" passwd "$USER_RUN" 2>/dev/null | cut -d: -f6)
    echo "port $PORT: pid $PID, user $USER_RUN, home ${HOME_DIR:-unknown}"
  else
    echo "nothing listening on 127.0.0.1:$PORT that this shell can see"
  fi
else
  echo "home given: $HOME_DIR"
fi
if [ -z "$HOME_DIR" ] || [ ! -d "$HOME_DIR" ]; then echo "no home folder to read; stopping"; exit 0; fi
STATE="$HOME_DIR/.openclaw"
echo "-- state folder: $STATE"
[ -d "$STATE" ] || { echo "no .openclaw folder there; stopping"; exit 0; }
CONF=""
for c in "$STATE/openclaw.json" "$STATE/openclaw.json5" "$STATE/config.json"; do [ -f "$c" ] && { CONF="$c"; break; }; done
if [ -z "$CONF" ]; then
  echo "config file: none of openclaw.json, openclaw.json5, config.json found"
else
  echo "config file: $CONF ($(stat -c '%U:%G mode %a, %s bytes' "$CONF" 2>/dev/null || echo 'stat unavailable'))"
  echo "-- config lines about avatar / identity / emoji / theme / workspace (other lines are never shown)"
  grep -nIiE 'avatar|identity|emoji|theme|workspace' "$CONF" 2>/dev/null | safe | head -40
  echo "(matching lines shown: $(grep -cIiE 'avatar|identity|emoji|theme|workspace' "$CONF" 2>/dev/null))"
fi
echo "-- workspaces and avatar folders"
for w in "$STATE"/workspace* "$STATE"/agents/*/workspace; do
  [ -d "$w" ] || continue
  echo "workspace: $w"
  for f in IDENTITY.md; do
    if [ -f "$w/$f" ]; then echo "  $f lines about name/emoji/theme/avatar:"; grep -niE 'name|emoji|theme|avatar' "$w/$f" | safe | head -10 | sed 's/^/    /'; fi
  done
  if [ -d "$w/avatars" ]; then echo "  avatars/:"; ls -l "$w/avatars" 2>/dev/null | tail -n +2 | awk '{print "    " $5 " bytes  " $9}' | head -20; else echo "  no avatars/ folder"; fi
done
echo "-- where the program mentions avatar settings (file names only)"
if [ -n "$PROG" ]; then
  PKG=$(dirname "$(readlink -f "$PROG" 2>/dev/null || echo "$PROG")")
  for d in "$PKG" "$PKG/.." "$PKG/../lib/node_modules/openclaw"; do
    [ -d "$d" ] || continue
    grep -rIl --include='*.js' --include='*.mjs' --include='*.json' -E 'identity\.avatar|assistant\.avatar|ui\.assistant' "$d" 2>/dev/null | head -8
  done | sort -u
fi
echo "== end of discovery; nothing was changed =="
