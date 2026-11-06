#!/bin/bash
# LIFE-339 (Jim, Nov 6 2026: "fix these!"): two warnings Hermes logs at every start.
#   1) `config.yaml has empty section(s): max_concurrent_sessions` - the key is present with no value. Per the config's own comment, unset / null / 0 / omitted all mean "unlimited", so the
#      line is commented out (same meaning, no warning).
#   2) `Invalid compression.codex_responses_compact_threshold=None; using 200000` - the key is present with no value. Per the config's comment a positive integer is an absolute server-compaction
#      trigger that is clamped DOWN when needed; the automatic value is (local trigger - 8192). The local trigger is 0.50 x context_length 64000 = 32000, so 23808 is what automatic mode
#      would pick; the default is 24000. (codex_responses_native is false today, so this value is not used until server compaction is switched on.)
#   sudo bash fix_config_warnings.sh            DRY RUN (default): shows the two lines it would change. Writes nothing.
#   sudo bash fix_config_warnings.sh --apply    backs up config.yaml into /root/hermes-config-<time CT>/, makes the two edits (a text edit: every comment stays), reads it back (YAML parse
#                                               when available: only those two settings differ), prints the rollback line.
# Options: --threshold N (default 24000), and for tests --home DIR (the .hermes folder), --backup DIR. It restarts NOTHING (Hermes reads config at startup: a restart is a separate step).
# It never prints a key, token, secret or password line and changes no other setting (model.context_length and model.base_url are NOT touched).
set -eu
HOME_DIR=/var/lib/hermeseval/home/.hermes; THRESHOLD=24000; APPLY=no; BACKUP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes ;;
    --threshold) THRESHOLD=${2:?}; shift ;;
    --home) HOME_DIR=${2:?}; shift ;;
    --backup) BACKUP=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
echo "$THRESHOLD" | grep -Eq '^[1-9][0-9]{3,7}$' || { echo "REFUSED: --threshold must be a whole number of 1000 to 99999999"; exit 1; }
CONF="$HOME_DIR/config.yaml"
[ -f "$CONF" ] || { echo "REFUSED: no $CONF"; exit 1; }
A_RX='^max_concurrent_sessions:[[:space:]]*(#.*)?$'
B_RX='^  codex_responses_compact_threshold:[[:space:]]*(#.*)?$'
NA=$(grep -Ec "$A_RX" "$CONF" || true); NB=$(grep -Ec "$B_RX" "$CONF" || true)
echo "== $([ "$APPLY" = yes ] && echo APPLY || echo 'DRY RUN (nothing is written)'): $CONF =="
echo "-- edit 1: top-level 'max_concurrent_sessions:' with no value: found $NA line(s)"
[ "$NA" = 1 ] && echo "   would become: # max_concurrent_sessions:   (commented out; unset = unlimited)"
echo "-- edit 2: 'codex_responses_compact_threshold:' with no value under compression: found $NB line(s)"
[ "$NB" = 1 ] && echo "   would become: codex_responses_compact_threshold: $THRESHOLD"
if [ "$NA" = 0 ] && [ "$NB" = 0 ]; then echo "Nothing to do: neither empty setting is present (already fixed?)."; exit 0; fi
if [ "$NA" -gt 1 ] || [ "$NB" -gt 1 ]; then echo "REFUSED: a setting appears more than once; edit by hand (nothing was changed)"; exit 1; fi
if [ "$APPLY" != yes ]; then echo "Dry run only. To apply, add --apply. Nothing is restarted either way."; exit 0; fi
STAMP=$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
[ -n "$BACKUP" ] || BACKUP="/root/hermes-config-$STAMP"
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"; cp -p "$CONF" "$BACKUP/config.yaml"
TMP="$CONF.new.$$"
python3 - "$CONF" "$TMP" "$THRESHOLD" <<'PY'
import re, shutil, os, sys
src, dst, threshold = sys.argv[1:4]
out = []
for line in open(src).read().split("\n"):
    if re.match(r"^max_concurrent_sessions:\s*(#.*)?$", line):
        line = "# max_concurrent_sessions:   # unset = unlimited (commented out: an empty value logs a warning; LIFE-339)"
    elif re.match(r"^  codex_responses_compact_threshold:\s*(#.*)?$", line):
        line = "  codex_responses_compact_threshold: " + threshold
    out.append(line)
open(dst, "w").write("\n".join(out))
shutil.copymode(src, dst)
st = os.stat(src)
try:
    os.chown(dst, st.st_uid, st.st_gid)
except PermissionError:
    pass
PY
mv -f "$TMP" "$CONF"
echo "  backed up to $BACKUP/config.yaml and made the edit(s)"
RESULT=$(python3 - "$CONF" "$BACKUP/config.yaml" "$THRESHOLD" <<'PY'
import sys
conf, old, threshold = sys.argv[1:4]
try:
    import yaml
except Exception:
    yaml = None
if yaml:
    new = yaml.safe_load(open(conf)); was = yaml.safe_load(open(old))
    new_c = dict(new.get("compression") or {}); was_c = dict(was.get("compression") or {})
    t_ok = (new_c.get("codex_responses_compact_threshold") == int(threshold)) if "codex_responses_compact_threshold" in was_c and was_c["codex_responses_compact_threshold"] is None else True
    new_c.pop("codex_responses_compact_threshold", None); was_c.pop("codex_responses_compact_threshold", None)
    a_new = dict(new); a_new["compression"] = new_c
    a_old = dict(was); a_old["compression"] = was_c
    if "max_concurrent_sessions" in was and was["max_concurrent_sessions"] is None:       # the empty one was commented out: the key is gone (same meaning)
        a_old.pop("max_concurrent_sessions"); a_new.pop("max_concurrent_sessions", None)
    print("yaml-ok" if t_ok and a_new == a_old else "yaml-MISMATCH")
else:
    print("text-ok")
PY
)
case "$RESULT" in
  yaml-ok) echo "== read-back OK: the file parses as YAML and only the two settings differ from the backup ==" ;;
  text-ok) echo "== read-back: no YAML parser for python3 on this host; the edit was made line by line ==" ;;
  *) echo "READ-BACK FAILED ($RESULT). ROLLBACK NOW: cp -p $BACKUP/config.yaml $CONF"; exit 1 ;;
esac
echo "Nothing was restarted. Hermes reads config at startup; next, as its own step: sudo systemctl restart hermes-eval-backend; then the two warnings should be gone from the log."
echo "ROLLBACK: cp -p $BACKUP/config.yaml $CONF"
