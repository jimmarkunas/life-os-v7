#!/bin/bash
# LIFE-310 on Oracle (lifeos-edge-01), run as root via `opc`: installs the script-pack runner (runner.py in this folder) as ONE root-owned program with ONE command.
#   dry         (default) shows what would be installed and checks the files. Writes nothing.
#   install     copies runner.py to /opt/lifeos-runner/ (root, mode 755), writes the one-line launcher /usr/local/sbin/lifeos-run, creates /var/lib/lifeos-runner/ (root), reads the hash back.
#   verify      files present, owned by root, not group/world writable, runner.py equals the pinned hash; exit 0 only when all are good.
#   uninstall   removes the launcher and /opt/lifeos-runner. The results folder (/var/lib/lifeos-runner) stays; `uninstall --purge` removes that too.
# It starts nothing: no timer, no service, no watcher, no sudoers file, no credential. It does not touch OpenClaw, V7, Hermes, the ticker or any other file.
# The runner you install must be the one this script pins (RUNNER_SHA256); change runner.py and this script's own sha256 changes with the pin, so one printed hash covers both.
# Options: --prefix DIR (tests: put everything under DIR and skip the root check).
set -euo pipefail
RUNNER_SHA256=142c279f050814f5d77c114351483e3a53e45e68e7b17eadd5866450cf87e9bf
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=""; CMD=dry; PURGE=no
while [ $# -gt 0 ]; do
  case "$1" in
    dry|install|verify|uninstall) CMD=$1 ;;
    --purge) PURGE=yes ;;
    --prefix) PREFIX=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
OPT="$PREFIX/opt/lifeos-runner"; WRAP="$PREFIX/usr/local/sbin/lifeos-run"; STATE="$PREFIX/var/lib/lifeos-runner"; SRC="$HERE/runner.py"
say() { printf '%s\n' "$*"; }
die() { say "STOP: $*"; exit 1; }
[ -n "$PREFIX" ] || [ "$(id -u)" = 0 ] || die "run as root via opc (sudo bash install-runner.sh install)"
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
mode_of() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }
unsafe() { [ -e "$1" ] && [ $(( 8#$(mode_of "$1") & 8#022 )) -ne 0 ]; }
check_source() {
  [ -f "$SRC" ] && [ ! -L "$SRC" ] || die "runner.py is not next to this script"
  [ "$(sha "$SRC")" = "$RUNNER_SHA256" ] || die "runner.py does not match the pinned sha256 ($RUNNER_SHA256); nothing changed"
  python3 -I -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$SRC" || die "runner.py does not parse; nothing changed"
}
case "$CMD" in
  dry)
    check_source
    say "== DRY RUN (nothing is written) =="
    say "would install: $OPT/runner.py (755), $WRAP (755, one line), $STATE (755); no timer, service or sudoers rule"
    say "runner.py sha256 $RUNNER_SHA256 matches the pin; this script's sha256 $(sha "${BASH_SOURCE[0]}")"
    say "To install: sudo bash install-runner.sh install" ;;
  install)
    check_source
    install -d -m 755 "$OPT" "$(dirname "$WRAP")" "$STATE" "$STATE/results"
    install -m 755 "$SRC" "$OPT/runner.py"
    printf '#!/bin/sh\nexec /usr/bin/python3 -I /opt/lifeos-runner/runner.py "$@"\n' > "$WRAP.new"
    chmod 755 "$WRAP.new"; mv -f "$WRAP.new" "$WRAP"
    [ "$(sha "$OPT/runner.py")" = "$RUNNER_SHA256" ] || die "READ-BACK FAILED: installed runner.py differs from the pin. ROLLBACK: sudo bash install-runner.sh uninstall"
    say "installed: $OPT/runner.py read back equal to the pin; launcher $WRAP"
    say "Nothing was started. Try: lifeos-run help"
    say "ROLLBACK: sudo bash install-runner.sh uninstall" ;;
  verify)
    bad=0
    for f in "$OPT/runner.py" "$WRAP"; do [ -f "$f" ] || { say "MISSING $f"; bad=1; }; done
    [ -f "$OPT/runner.py" ] && { [ "$(sha "$OPT/runner.py")" = "$RUNNER_SHA256" ] || { say "runner.py differs from the pin"; bad=1; }; }
    for f in "$OPT" "$OPT/runner.py" "$WRAP" "$STATE"; do
      [ -e "$f" ] || continue
      if unsafe "$f"; then say "WRITABLE by group/others: $f"; bad=1; fi
      [ -n "$PREFIX" ] || [ "$(stat -c %U "$f")" = root ] || { say "NOT root-owned: $f"; bad=1; }
    done
    [ "$bad" = 0 ] || die "verify failed"
    say "verify OK: runner.py equals the pin, root-owned, not group/world writable" ;;
  uninstall)
    rm -f "$WRAP" "$WRAP.new"
    rm -rf "$OPT"
    if [ "$PURGE" = yes ]; then rm -rf "$STATE"; say "removed the launcher, $OPT and $STATE"; else say "removed the launcher and $OPT; $STATE (results) stays; add --purge to remove it"; fi ;;
esac
