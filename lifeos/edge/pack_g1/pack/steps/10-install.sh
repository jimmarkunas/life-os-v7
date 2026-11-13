#!/bin/bash
# LIFE-647 group 1, one step, as root through lifeos-run (by hand, --dry-run changes nothing). Puts four trimmed programs on Oracle: gates (no run in progress), pins (each host file is
# exactly the old main file or already the new one, else it stops), install (same owner and mode, one rename each), check (it loads), readback. A failed check puts every file back.
# Nothing is restarted: three are timer one-shots and the Granola helper starts one process per connection. Hooks for the tests: EP_ROOT, EP_FILES, EP_SYSTEMCTL, EP_PY.
set -u
R=${EP_ROOT:-}; F=${EP_FILES:-${PACK_DIR:-.}/files}; SC=${EP_SYSTEMCTL:-systemctl}; PY=${EP_PY:-/usr/bin/python3}; DRY=0; [ "${1:-}" = --dry-run ] && DRY=1
UNITS="lifeos-ticker-tick lifeos-ticker-watch lifeos-offsite-backup lifeos-close-release"
say() { echo "READBACK $*"; }
fail() { say "failed=$1"; [ -n "${W:-}" ] && rm -rf "$W"; exit 1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }
put() { cp -p "$2" "$2.lifeos-new" && cat "$1" > "$2.lifeos-new" && mv -f "$2.lifeos-new" "$2"; }       # copy keeps the owner and mode; the rename is atomic
timers() { for u in $UNITS; do "$SC" is-active "$u.timer"; done | tr '\n' ' '; }

for u in $UNITS; do
  case "$("$SC" is-active "$u.service" 2>/dev/null)" in active|activating|reloading|deactivating) fail "busy_$u" ;; esac
done
[ -z "$("$SC" list-units 'lifeos-gran@*.service' --state=active,activating --no-legend 2>/dev/null)" ] || fail busy_lifeos-gran
before=$(timers)

OLD=" "; todo=0                                                   # names whose host file is still the old main file
while IFS='|' read -r name path old new; do
  p="$R$path"
  [ -f "$p" ] && [ ! -L "$p" ] || fail "missing_$name"
  [ "$(sha "$F/$name")" = "$new" ] || fail "pack_file_$name"
  cur=$(sha "$p")
  if [ "$cur" = "$old" ]; then OLD="$OLD$name "; todo=$((todo + 1))
  elif [ "$cur" != "$new" ]; then fail "pin_mismatch_$name"; fi
done < <(grep -v '^#' "$F/table")
say "gates=no_run_in_progress pins=ok to_install=$todo"
[ "$DRY" = 1 ] && { say "result=would_install_$todo dry_run=yes"; exit 0; }

W=$(mktemp -d) || fail no_work_folder
while IFS='|' read -r name path old new; do
  case "$OLD" in *" $name "*) ;; *) continue ;; esac
  cp -p "$R$path" "$W/$name" && put "$F/$name" "$R$path" || fail "install_$name"
done < <(grep -v '^#' "$F/table")
bad=""
while IFS='|' read -r name path old new; do
  "$PY" -I -B "$F/check.py" "$name" "$R$path" || bad=$name
done < <(grep -v '^#' "$F/table")
if [ -n "$bad" ]; then
  while IFS='|' read -r name path old new; do case "$OLD" in *" $name "*) put "$W/$name" "$R$path" ;; esac; done < <(grep -v '^#' "$F/table")
  fail "check_${bad}_restored"
fi
[ "$(timers)" = "$before" ] || fail timer_state_changed
say "timers_unchanged=yes"
say "result=installed_$todo dry_run=no"
rm -rf "$W"
