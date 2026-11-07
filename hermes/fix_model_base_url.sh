#!/bin/bash
# LIFE-340 (found Nov 7 2026 from Hermes' own logs): config.yaml says provider openai-codex but model.base_url https://openrouter.ai/api/v1. Hermes builds its first request for every turn against
# OpenRouter (no key there: 401 "Missing Authentication header"), then recovers by credential rotation onto the ChatGPT/Codex address the login really belongs to (about 270 OpenRouter lines in
# agent.log). That wasted failed call is part of the "slow, clunky" feel, and the Compress conversation summary call has no such recovery, so it fails with the same 401 every time.
# The Codex login's own pool entry already routes to https://chatgpt.com/backend-api/codex; this makes model.base_url say the same thing. Only that one value changes.
#   sudo bash fix_model_base_url.sh            DRY RUN (default): shows the model section (key/token/secret/password lines hidden) and the one line it would change. Writes nothing.
#   sudo bash fix_model_base_url.sh --apply    backs up config.yaml into /root/hermes-config-<time CT>/, changes that one line (a text edit: every comment stays), reads it back
#                                              (YAML parse when available: only model.base_url differs), prints the rollback line.
# Options: --from URL (default https://openrouter.ai/api/v1), --to URL (default https://chatgpt.com/backend-api/codex), and for tests --home DIR, --backup DIR.
# It refuses unless model.provider is openai-codex and model.base_url is exactly the --from value (so it cannot touch a setting that is already right or deliberately different).
# It restarts NOTHING (Hermes reads config at startup: a restart is a separate step and ends any open chat). It never prints a key, token, secret or password line.
set -eu
HOME_DIR=/var/lib/hermeseval/home/.hermes; FROM=https://openrouter.ai/api/v1; TO=https://chatgpt.com/backend-api/codex; APPLY=no; BACKUP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes ;;
    --from) FROM=${2:?}; shift ;;
    --to) TO=${2:?}; shift ;;
    --home) HOME_DIR=${2:?}; shift ;;
    --backup) BACKUP=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
URL_RX='^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._/-]*)?$'
echo "$FROM" | grep -Eq "$URL_RX" && echo "$TO" | grep -Eq "$URL_RX" || { echo "REFUSED: --from and --to must be plain https addresses"; exit 1; }
CONF="$HOME_DIR/config.yaml"
[ -f "$CONF" ] || { echo "REFUSED: no $CONF"; exit 1; }
SECRETY='key|token|secret|pass'
MODEL=$(awk '/^model:/{f=1;print;next} f&&/^[^[:space:]#]/{f=0} f{print}' "$CONF")
echo "== $([ "$APPLY" = yes ] && echo APPLY || echo 'DRY RUN (nothing is written)'): $CONF =="
echo "-- model section now (key/token/secret/password lines hidden)"
echo "$MODEL" | grep -viE "$SECRETY" | grep -vE '^[[:space:]]*(#|$)' | head -12 | sed 's/^/  /'
echo "$MODEL" | grep -Eq '^  provider:[[:space:]]*"?openai-codex"?[[:space:]]*(#.*)?$' || { echo "REFUSED: model.provider is not openai-codex; nothing was changed"; exit 1; }
LINE_RX='^  base_url:[[:space:]]*"?'"$(printf '%s' "$FROM" | sed 's/[.[\*^$/]/\\&/g')"'"?[[:space:]]*(#.*)?$'
N=$(echo "$MODEL" | grep -Ec "$LINE_RX" || true)
if [ "$N" = 0 ]; then
  if echo "$MODEL" | grep -Eq '^  base_url:[[:space:]]*"?'"$(printf '%s' "$TO" | sed 's/[.[\*^$/]/\\&/g')"'"?[[:space:]]*(#.*)?$'; then echo "Nothing to do: model.base_url is already $TO."; exit 0; fi
  echo "REFUSED: model.base_url is not $FROM (found a different value, or none); edit by hand (nothing was changed)"; exit 1
fi
[ "$N" = 1 ] || { echo "REFUSED: model.base_url appears $N times; edit by hand (nothing was changed)"; exit 1; }
echo "-- would change: model.base_url  $FROM  ->  $TO"
if [ "$APPLY" != yes ]; then echo "Dry run only. To apply, add --apply. Nothing is restarted either way."; exit 0; fi
STAMP=$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
[ -n "$BACKUP" ] || BACKUP="/root/hermes-config-$STAMP"
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"; cp -p "$CONF" "$BACKUP/config.yaml"
TMP="$CONF.new.$$"
python3 - "$CONF" "$TMP" "$FROM" "$TO" <<'PY'
import os, re, shutil, sys
src, dst, old, new = sys.argv[1:5]
lines, inside, done = open(src).read().split("\n"), False, False
for i, line in enumerate(lines):
    if re.match(r"^model:\s*(#.*)?$", line):
        inside = True
    elif inside and re.match(r"^[^\s#]", line):
        inside = False
    elif inside and not done and re.match(r"^  base_url:\s*\"?" + re.escape(old) + r"\"?\s*(#.*)?$", line):
        lines[i] = "  base_url: " + new
        done = True
if not done:
    sys.exit("edit target vanished")
open(dst, "w").write("\n".join(lines))
shutil.copymode(src, dst)
st = os.stat(src)
try:
    os.chown(dst, st.st_uid, st.st_gid)
except PermissionError:
    pass
PY
mv -f "$TMP" "$CONF"
echo "  backed up to $BACKUP/config.yaml and changed the one line"
RESULT=$(python3 - "$CONF" "$BACKUP/config.yaml" "$TO" <<'PY'
import sys
conf, old, to = sys.argv[1:4]
try:
    import yaml
except Exception:
    yaml = None
if yaml:
    new = yaml.safe_load(open(conf)); was = yaml.safe_load(open(old))
    ok = new["model"]["base_url"] == to
    new["model"].pop("base_url", None); was["model"].pop("base_url", None)
    print("yaml-ok" if ok and new == was else "yaml-MISMATCH")
else:
    print("text-ok")
PY
)
case "$RESULT" in
  yaml-ok) echo "== read-back OK: the file parses as YAML and only model.base_url differs from the backup ==" ;;
  text-ok) echo "== read-back: no YAML parser for python3 on this host; the edit was made line by line ==" ;;
  *) echo "READ-BACK FAILED ($RESULT). ROLLBACK NOW: cp -p $BACKUP/config.yaml $CONF"; exit 1 ;;
esac
echo "Nothing was restarted. Hermes reads config at startup; next, as its own step (it ends any open chat): sudo systemctl restart hermes-eval-backend"
echo "ROLLBACK: cp -p $BACKUP/config.yaml $CONF"
