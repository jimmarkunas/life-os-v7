#!/bin/bash
# LIFE-334 (Jim, Nov 6 2026: "Fix this at once!"): Hermes "Compress conversation" fails with 401 Missing Authentication header. The summarizer is on "auto"; the config's
# model.base_url points at OpenRouter while the provider is openai-codex, so the summary call most likely reaches OpenRouter with no key (UNVERIFIED until tested).
# This pins the compression model to the SAME provider and model as the main agent (which authenticates today): auxiliary.compression.provider / .model.
#   sudo bash set_compression.sh            DRY RUN (default): shows the current model lines and exactly what would be inserted. Writes nothing.
#   sudo bash set_compression.sh --apply    backs up config.yaml into /root/hermes-config-<time CT>/, inserts the block, reads it back (YAML parse when available), prints the rollback line.
# Options: --provider NAME (default openai-codex), --model NAME (default gpt-6-luna), and for tests --home DIR (the .hermes folder), --backup DIR.
# It restarts NOTHING (Hermes reads config.yaml at startup: a restart, `sudo systemctl restart hermes-eval-backend`, is a separate step). It keeps every comment (a text insert, not a
# YAML rewrite), changes no other setting (model.base_url and model.context_length are NOT touched), and never prints a key, token, secret or password line.
set -eu
HOME_DIR=/var/lib/hermeseval/home/.hermes; PROVIDER=openai-codex; MODEL=gpt-6-luna; APPLY=no; BACKUP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes ;;
    --provider) PROVIDER=${2:?}; shift ;;
    --model) MODEL=${2:?}; shift ;;
    --home) HOME_DIR=${2:?}; shift ;;
    --backup) BACKUP=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
NAME_RX='^[A-Za-z0-9][A-Za-z0-9._/:-]{0,80}$'
echo "$PROVIDER" | grep -Eq "$NAME_RX" && echo "$MODEL" | grep -Eq "$NAME_RX" || { echo "REFUSED: provider or model has odd characters"; exit 1; }
CONF="$HOME_DIR/config.yaml"
[ -f "$CONF" ] || { echo "REFUSED: no $CONF"; exit 1; }
SECRETY='key|token|secret|pass'
echo "== $([ "$APPLY" = yes ] && echo APPLY || echo 'DRY RUN (nothing is written)'): $CONF =="
echo "-- model lines now (key/token/secret/password lines hidden)"
awk '/^model:/{f=1;print;next} f&&/^[^[:space:]#]/{f=0} f&&!/^[[:space:]]*(#|$)/{print}' "$CONF" | grep -viE "$SECRETY" | head -12 | sed 's/^/  /'
HAS=$(awk '/^auxiliary:/{f=1;next} f&&/^[^[:space:]#]/{f=0} f&&/^  compression:/{print "yes"; exit}' "$CONF")
[ -z "$HAS" ] || { echo "REFUSED: auxiliary.compression already exists in the config; edit it by hand (nothing was changed)"; exit 1; }
if grep -q '^auxiliary:' "$CONF"; then WHERE="inserted right after the existing 'auxiliary:' line"; else WHERE="appended at the end (no 'auxiliary:' section yet)"; fi
echo "-- would add ($WHERE):"
printf 'auxiliary:\n  compression:\n    provider: %s\n    model: %s\n' "$PROVIDER" "$MODEL" | sed 's/^/  /'
if [ "$APPLY" != yes ]; then echo "Dry run only. To apply, add --apply. Nothing is restarted either way."; exit 0; fi
STAMP=$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
[ -n "$BACKUP" ] || BACKUP="/root/hermes-config-$STAMP"
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"; cp -p "$CONF" "$BACKUP/config.yaml"
TMP="$CONF.new.$$"
python3 - "$CONF" "$TMP" "$PROVIDER" "$MODEL" <<'PY'
import sys
src, dst, provider, model = sys.argv[1:5]
lines = open(src).read().split("\n")
block = ["  compression:", "    provider: " + provider, "    model: " + model]
for i, l in enumerate(lines):
    if l.rstrip() == "auxiliary:":
        lines[i + 1:i + 1] = block
        break
else:
    if lines and lines[-1] == "":
        lines[-1:-1] = ["auxiliary:"] + block
    else:
        lines += ["auxiliary:"] + block
open(dst, "w").write("\n".join(lines))
import os, shutil
shutil.copymode(src, dst)
st = os.stat(src)
try:
    os.chown(dst, st.st_uid, st.st_gid)
except PermissionError:
    pass
PY
mv -f "$TMP" "$CONF"
echo "  backed up to $BACKUP/config.yaml and inserted the block"
RESULT=$(python3 - "$CONF" "$BACKUP/config.yaml" "$PROVIDER" "$MODEL" <<'PY'
import sys
conf, old, provider, model = sys.argv[1:5]
try:
    import yaml
except Exception:
    yaml = None
if yaml:
    new = yaml.safe_load(open(conf)); was = yaml.safe_load(open(old))
    ok = new["auxiliary"]["compression"] == {"provider": provider, "model": model}
    rest = dict(new); rest["auxiliary"] = {k: v for k, v in rest["auxiliary"].items() if k != "compression"}
    before = dict(was); before["auxiliary"] = before.get("auxiliary") or {}
    same = rest == before
    print("yaml-ok" if ok and same else "yaml-MISMATCH")
else:
    text = open(conf).read()
    print("text-ok" if ("  compression:\n    provider: %s\n    model: %s" % (provider, model)) in text else "text-MISMATCH")
PY
)
case "$RESULT" in
  yaml-ok) echo "== read-back OK: the file parses as YAML, auxiliary.compression is $PROVIDER / $MODEL and every other setting is unchanged ==" ;;
  text-ok) echo "== read-back OK (text check only: no YAML parser for python3 on this host); the block is there ==" ;;
  *) echo "READ-BACK FAILED ($RESULT). ROLLBACK NOW: cp -p $BACKUP/config.yaml $CONF"; exit 1 ;;
esac
echo "Nothing was restarted. Hermes reads config.yaml at startup; next, as its own step: sudo systemctl restart hermes-eval-backend (Hermes Desktop reconnects), then press Compress conversation on a long chat."
echo "ROLLBACK: cp -p $BACKUP/config.yaml $CONF"
