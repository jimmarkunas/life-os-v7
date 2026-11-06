#!/bin/bash
# LIFE-321: read-only audit. Is EVERY Hermes bot (profile) on this host limited by the connector-allowlist plugin?
#   bash check_profiles.sh [--home DIR] [--table FILE]
# For the default profile (main) and every folder under profiles/ it checks: the plugin files equal the repo copy, the plugin is in plugins.enabled and not in plugins.disabled, and
# allowlist.txt holds exactly the names in hermes/connector-allowlists.txt. A profile that exists on the host but is NOT in the table is reported as UNPROTECTED. It writes nothing, restarts
# nothing, and prints names and hashes only. Exit 0 only when every profile passes. It cannot tell whether the running Desktop server has loaded the plugin: after any change run
# `sudo systemctl restart hermes-eval-backend` and the live refusal test (docs/hermes-files.md rule 4).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/plugins/connector-allowlist"
HOME_DIR=/var/lib/hermeseval/home/.hermes
TABLE="$HERE/connector-allowlists.txt"
while [ $# -gt 0 ]; do
  case "$1" in
    --home) HOME_DIR=${2:?}; shift ;;
    --table) TABLE=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
[ -f "$TABLE" ] || { echo "no table $TABLE"; exit 2; }
h12() { sha256sum "$1" | cut -c1-12; }
pdir_of() { if [ "$1" = main ]; then echo "$HOME_DIR"; else echo "$HOME_DIR/profiles/$1"; fi; }
want_of() { # sorted names for a profile from the table, or the word NOTABLE
  local row
  row=$(sed 's/#.*//' "$TABLE" | awk -F: -v p="$1" '{n=$1; gsub(/[ \t]/,"",n); if (n==p) {print $2; found=1}} END{if(!found) print "@@NONE@@"}')
  [ "$row" = "@@NONE@@" ] && { echo NOTABLE; return; }
  echo "$row" | tr -d ' \t' | tr ',' '\n' | grep -v '^$' | sort | tr '\n' ' '
}
plugin_state() { # enabled | disabled | no  (reads the plugins: block of config.yaml)
  awk '
    /^plugins:/ {inp=1; next}
    inp && /^[A-Za-z_]+:/ {inp=0}
    inp && /^  enabled:/ {mode="e"; next}
    inp && /^  disabled:/ {mode="d"; next}
    inp && /^  [A-Za-z_]+:/ {mode=""; next}
    inp && /^    - / {item=$2; if (item=="connector-allowlist") { if (mode=="e") en=1; if (mode=="d") di=1 }}
    END {if (di) print "disabled"; else if (en) print "enabled"; else print "no"}
  ' "$1" 2>/dev/null
}
names="main"
for d in "$HOME_DIR"/profiles/*/; do [ -d "$d" ] && names="$names $(basename "$d")"; done
for t in $(sed 's/#.*//' "$TABLE" | awk -F: 'NF>=2{n=$1; gsub(/[ \t]/,"",n); if(n!="") print n}'); do
  case " $names " in *" $t "*) ;; *) names="$names $t" ;; esac
done
fail=0
echo "== connector-allowlist audit of $HOME_DIR =="
for p in $names; do
  pd=$(pdir_of "$p"); dst="$pd/plugins/connector-allowlist"; problems=""
  [ -d "$pd" ] || { echo "  $p: FAIL (in the table but no profile folder)"; fail=1; continue; }
  want=$(want_of "$p")
  if [ "$want" = "ALL " ]; then
    if [ "$(plugin_state "$pd/config.yaml")" = enabled ]; then echo "  $p: FAIL; the table says ALL (not limited) but the plugin is enabled in it"; fail=1
    else echo "  $p: OK (not limited, by decision: ALL)"; fi
    continue
  fi
  [ "$want" = NOTABLE ] && problems="$problems; UNPROTECTED: this profile is not in the table"
  for f in plugin.yaml __init__.py; do
    if [ ! -f "$dst/$f" ]; then problems="$problems; $f missing"
    elif [ "$(h12 "$dst/$f")" != "$(h12 "$SRC/$f")" ]; then problems="$problems; $f differs from the repo"; fi
  done
  if [ ! -f "$dst/allowlist.txt" ]; then problems="$problems; allowlist.txt missing"
  elif [ "$want" != NOTABLE ]; then
    have=$(sed 's/#.*//' "$dst/allowlist.txt" | tr -d ' \t' | grep -v '^$' | sort | tr '\n' ' ')
    [ "$have" = "$want" ] || problems="$problems; allowlist.txt holds [${have% }] but the table says [${want% }]"
  fi
  state=$(plugin_state "$pd/config.yaml")
  case "$state" in enabled) ;; disabled) problems="$problems; plugin is in plugins.disabled" ;; *) problems="$problems; plugin is not in plugins.enabled" ;; esac
  shown="${want% }"; [ -n "$shown" ] || shown="nothing"; [ "$want" = NOTABLE ] && shown="?"
  if [ -z "$problems" ]; then echo "  $p: OK (may use: $shown)"; else echo "  $p: FAIL${problems}"; fail=1; fi
done
if [ "$fail" -eq 0 ]; then echo "== PASS: every profile is limited =="; else echo "== FAIL: at least one profile is NOT limited correctly =="; fi
exit "$fail"
