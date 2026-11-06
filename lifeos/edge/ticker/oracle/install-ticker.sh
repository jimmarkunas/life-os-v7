#!/bin/bash
# LIFE-330 on Oracle, run as root via `opc` (never `ubuntu`). The ticker (lifeos/edge/ticker): systemd timers that start the hourly pipeline and run the watchdog's
# incident checks, replacing cron-job.org (D31). Same shape as LIFE-129's install-offsite.sh.
#   install REPO        REPO is a life-os-v7 checkout; copies the ticker, the five watchdog modules it runs and the four units (changes nothing live: no timer is enabled)
#   credential github   pipe the fine-grained GitHub token in (life-os-v7 only: Actions read/write, Issues read/write, Variables read); stored encrypted, never shown
#   credential ntfy     pipe the phone topic in; stored encrypted, never shown
#   dry                 credentials present and the alert-switch variables readable; dispatches nothing, pushes nothing
#   run-now tick|watch  one real run now, then its result line
#   enable              start both timers (needs both credentials and a passing dry)
#   verify              files, credentials, timers and the last runs; exit 0 only when all are good
#   disable | uninstall stop the timers | remove the units and the code (the credentials stay until `forget`)
#   forget              remove the two stored credentials
# Only its own four units, /opt/lifeos-ticker and its own two credentials are touched: never the Gateway, iMessage, the Close service, the off-site backup,
# Tailscale, another user's files or any other credential.
set -euo pipefail
OPT=/opt/lifeos-ticker; CREDS=/etc/credstore.encrypted; UNITS=/etc/systemd/system
PROG="$OPT/ticker.py"; GH="$CREDS/lifeos-ticker.github"; NT="$CREDS/lifeos-ticker.ntfy"
UNIT_FILES="lifeos-ticker-tick.service lifeos-ticker-tick.timer lifeos-ticker-watch.service lifeos-ticker-watch.timer"
MODULES="gate.py alerts.py alert_registry.py alert_settings.py redact.py"
say() { printf '%s\n' "$*"; }
die() { say "STOP: $*"; exit 1; }
[ "$(id -u)" = 0 ] || die "run as root via opc"

# a one-off run of the ticker with the stored credentials, exactly as the timer runs it
ticker() { systemd-run --quiet --wait --pipe --collect -p DynamicUser=yes -p "LoadCredentialEncrypted=github:$GH" -p "LoadCredentialEncrypted=ntfy:$NT" \
  /usr/bin/python3 -I "$PROG" "$1"; }

case "${1:-}" in
  install)
    src="${2:?usage: install-ticker.sh install REPO}"
    for f in lifeos/edge/ticker/ticker.py $(for u in $UNIT_FILES; do printf 'lifeos/edge/ticker/oracle/%s ' "$u"; done) lifeos/edge/ticker/oracle/install-ticker.sh \
             $(for m in $MODULES; do printf 'lifeos/platform/%s ' "$m"; done); do
      [ -f "$src/$f" ] || die "missing $src/$f"
    done
    for f in lifeos/edge/ticker/ticker.py $(for m in $MODULES; do printf 'lifeos/platform/%s ' "$m"; done); do
      python3 -I -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$src/$f" || die "$f does not parse; nothing changed"
    done
    install -d -m 755 "$OPT" "$OPT/lifeos" "$OPT/lifeos/platform"
    install -m 644 "$src/lifeos/edge/ticker/ticker.py" "$PROG"
    install -m 755 "$src/lifeos/edge/ticker/oracle/install-ticker.sh" "$OPT/install-ticker.sh"
    : > "$OPT/lifeos/__init__.py"; : > "$OPT/lifeos/platform/__init__.py"
    for m in $MODULES; do install -m 644 "$src/lifeos/platform/$m" "$OPT/lifeos/platform/$m"; done
    for u in $UNIT_FILES; do install -m 644 "$src/lifeos/edge/ticker/oracle/$u" "$UNITS/$u"; done
    systemctl daemon-reload
    say "installed the ticker and its units at $(git -C "$src" rev-parse --short HEAD 2>/dev/null || echo unknown); no timer is enabled (credential github, credential ntfy, dry, enable come next)" ;;
  credential)
    name="${2:-}"; [ "$name" = github ] || [ "$name" = ntfy ] || die "usage: credential github | credential ntfy"
    [ -t 0 ] && die "pipe the value in (it is never typed on the command line or shown)"
    value="$(cat | tr -d '\r\n ')"
    if [ "$name" = github ]; then
      [[ "$value" =~ ^github_pat_[A-Za-z0-9_]{20,}$ ]] || die "that is not a fine-grained GitHub token (github_pat_...); the stored one (if any) is unchanged"
      out="$GH"
    else
      [[ "$value" =~ ^[A-Za-z0-9_-]{8,64}$ ]] || die "that is not an ntfy topic name; the stored one (if any) is unchanged"
      out="$NT"
    fi
    install -d -m 700 "$CREDS"
    printf '%s' "$value" | systemd-creds encrypt --name="$name" - "$out"; value=""
    chmod 600 "$out"; say "encrypted $name credential stored (value not shown)" ;;
  dry)
    [ -s "$GH" ] && [ -s "$NT" ] || die "both credentials must be stored first"
    ticker dry ;;
  run-now)
    what="${2:-}"; [ "$what" = tick ] || [ "$what" = watch ] || die "usage: run-now tick | run-now watch"
    [ -s "$GH" ] && [ -s "$NT" ] || die "both credentials must be stored first"
    ticker "$what" ;;
  enable)
    [ -s "$GH" ] && [ -s "$NT" ] || die "both credentials must be stored first"
    ticker dry >/dev/null || die "dry did not pass; nothing enabled"
    systemctl enable --now lifeos-ticker-tick.timer lifeos-ticker-watch.timer >/dev/null
    say "timers enabled; next tick $(systemctl show lifeos-ticker-tick.timer -p NextElapseUSecRealtime --value), next watch $(systemctl show lifeos-ticker-watch.timer -p NextElapseUSecRealtime --value)" ;;
  verify)
    ok=0
    check() { if eval "$2"; then say "  [PASS] $1"; else say "  [FAIL] $1"; ok=1; fi; }
    units_present() { for u in $UNIT_FILES; do [ -f "$UNITS/$u" ] || return 1; done; }
    check "code, modules and units installed" '[ -f "$PROG" ] && [ -f "$OPT/lifeos/platform/gate.py" ] && units_present'
    check "both encrypted credentials stored, root-only" '[ "$(stat -c %a:%U "$GH" 2>/dev/null)" = "600:root" ] && [ "$(stat -c %a:%U "$NT" 2>/dev/null)" = "600:root" ]'
    check "ubuntu cannot read the credentials" '! runuser -u ubuntu -- cat "$GH" >/dev/null 2>&1 && ! runuser -u ubuntu -- cat "$NT" >/dev/null 2>&1'
    check "both timers enabled" 'systemctl is-enabled --quiet lifeos-ticker-tick.timer && systemctl is-enabled --quiet lifeos-ticker-watch.timer'
    # A service that has never run also reports Result=success and status 0, so a real run is required: ExecMainStartTimestamp must be set (LIFE-330).
    ran_ok() { [ -n "$(systemctl show "$1" -p ExecMainStartTimestamp --value)" ] && [ "$(systemctl show "$1" -p Result --value)" = success ] && [ "$(systemctl show "$1" -p ExecMainStatus --value)" = 0 ]; }
    check "tick service has run (started by its timer) and succeeded" 'ran_ok lifeos-ticker-tick.service'
    check "watch service has run (started by its timer) and succeeded" 'ran_ok lifeos-ticker-watch.service'
    exit $ok ;;
  disable) systemctl disable --now lifeos-ticker-tick.timer lifeos-ticker-watch.timer >/dev/null 2>&1 || true; say "timers stopped" ;;
  uninstall)
    systemctl disable --now lifeos-ticker-tick.timer lifeos-ticker-watch.timer >/dev/null 2>&1 || true
    for u in $UNIT_FILES; do rm -f "$UNITS/$u"; done; rm -rf "$OPT"; systemctl daemon-reload
    say "units and code removed (the credentials are kept; run forget to remove them)" ;;
  forget) rm -f "$GH" "$NT"; say "stored credentials removed" ;;
  *) say "usage: install-ticker.sh install REPO | credential github|ntfy | dry | run-now tick|watch | enable | verify | disable | uninstall | forget"; exit 2 ;;
esac
