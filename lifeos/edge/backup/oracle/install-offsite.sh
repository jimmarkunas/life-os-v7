#!/bin/bash
# LIFE-129 on Oracle, run as root via `opc` (never `ubuntu`). The nightly off-site backup (lifeos/edge/backup): an encrypted copy of the state that cannot be rebuilt, to Jim's Drive.
#   install DIR    DIR holds offsite_backup.py, install-offsite.sh, lifeos-offsite-backup.service and .timer (this changes nothing live: the timer is NOT enabled yet)
#   key            pipe Jim's PUBLIC gpg key in; a file that carries a secret key is refused (his private key must never be on this machine)
#   credential     pipe consent.py in (the drive.file credential); stored as an encrypted systemd credential, never shown
#   dry            snapshot + archive + encrypt with the installed key, NO Drive, nothing stored: counts only
#   enable         start the nightly timer (needs the key and the credential)
#   run-now        one real run now, then its status (counts and a fixed code)
#   verify         files, timer and the last run's status; exit 0 only when all are good
#   disable | uninstall   stop the timer | remove the units and the code (the key, the credential and the status stay until `forget`)
#   forget         remove the stored key and credential
# Only its own units, /opt/lifeos-offsite and its own two files are touched: never the Gateway (443/18789), iMessage (47341/47342), Funnel, Tailscale Serve, the Close service,
# another user's files (databases are read by their owners), or any credential but its own.
set -euo pipefail
OPT=/opt/lifeos-offsite; ETC=/etc/lifeos-offsite; CREDS=/etc/credstore.encrypted; UNITS=/etc/systemd/system
KEYFILE="$ETC/recipient.asc"; CRED="$CREDS/lifeos-offsite.drive"; PROG="$OPT/offsite_backup.py"
say() { printf '%s\n' "$*"; }
die() { say "STOP: $*"; exit 1; }
[ "$(id -u)" = 0 ] || die "run as root via opc"

case "${1:-}" in
  install)
    src="${2:?usage: install-offsite.sh install DIR}"
    for f in offsite_backup.py install-offsite.sh lifeos-offsite-backup.service lifeos-offsite-backup.timer; do [ -f "$src/$f" ] || die "missing $src/$f"; done
    command -v gpg >/dev/null || die "gpg is not installed"
    python3 -I -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$src/offsite_backup.py" || die "offsite_backup.py does not parse; nothing changed"
    install -d -m 755 "$OPT" "$ETC"
    install -m 644 "$src/offsite_backup.py" "$PROG"
    install -m 755 "$src/install-offsite.sh" "$OPT/install-offsite.sh"
    install -m 644 "$src/lifeos-offsite-backup.service" "$UNITS/lifeos-offsite-backup.service"
    install -m 644 "$src/lifeos-offsite-backup.timer" "$UNITS/lifeos-offsite-backup.timer"
    systemctl daemon-reload
    say "installed the code and the units; the timer is not enabled (key, credential, dry, enable come next)" ;;
  key)
    [ -t 0 ] && die "pipe the PUBLIC key file into this: cat public.asc | ssh ... key"
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT; chmod 700 "$tmp"; cat > "$tmp/in.asc"
    gpg --batch --no-tty --homedir "$tmp" --import "$tmp/in.asc" >/dev/null 2>&1 || die "that is not a readable gpg key; the stored one (if any) is unchanged"
    [ -z "$(gpg --batch --no-tty --homedir "$tmp" --list-secret-keys --with-colons 2>/dev/null)" ] || die "that file carries a SECRET key; refused, nothing stored"
    [ "$(gpg --batch --no-tty --homedir "$tmp" --list-keys --with-colons 2>/dev/null | grep -c '^pub:')" = 1 ] || die "exactly one public key is expected"
    install -d -m 755 "$ETC"; install -m 644 "$tmp/in.asc" "$KEYFILE"
    say "public key stored; fingerprint: $(gpg --batch --no-tty --homedir "$tmp" --list-keys --with-colons 2>/dev/null | awk -F: '/^fpr:/{print $10; exit}')" ;;
  credential)
    [ -t 0 ] && die "pipe lifeos/edge/backup/consent.py into this; the credential is never typed or shown"
    value="$(cat)"
    printf '%s' "$value" | python3 -I -c 'import json, sys; d = json.load(sys.stdin); sys.exit(0 if all(isinstance(d.get(k), str) and d[k] for k in ("client_id", "client_secret", "refresh_token")) else 1)' 2>/dev/null \
      || die "no complete Drive credential arrived; the stored one (if any) is unchanged"
    install -d -m 700 "$CREDS"
    printf '%s' "$value" | systemd-creds encrypt --name=drive - "$CRED"; value=""
    chmod 600 "$CRED"; say "encrypted credential stored (value not shown)" ;;
  dry)
    [ -f "$KEYFILE" ] || die "no public key stored yet (run key)"
    python3 -I "$PROG" dry ;;
  enable)
    [ -f "$KEYFILE" ] && [ -s "$CRED" ] || die "the key and the credential must both be stored first"
    systemctl enable --now lifeos-offsite-backup.timer >/dev/null; say "timer enabled: $(systemctl show lifeos-offsite-backup.timer -p NextElapseUSecRealtime --value)" ;;
  run-now)
    systemctl start lifeos-offsite-backup.service || true
    python3 -I "$PROG" check || true
    cat /var/lib/lifeos-offsite/status.json 2>/dev/null || say "no status yet" ;;
  verify)
    ok=0
    check() { if eval "$2"; then say "  [PASS] $1"; else say "  [FAIL] $1"; ok=1; fi; }
    check "code and units installed" '[ -f "$PROG" ] && [ -f "$UNITS/lifeos-offsite-backup.service" ] && [ -f "$UNITS/lifeos-offsite-backup.timer" ]'
    check "public key stored and it is not a secret key" '[ -f "$KEYFILE" ] && ! grep -q "PRIVATE KEY" "$KEYFILE"'
    check "encrypted credential stored, root-only" '[ "$(stat -c %a:%U "$CRED" 2>/dev/null)" = "600:root" ]'
    check "timer enabled" 'systemctl is-enabled --quiet lifeos-offsite-backup.timer'
    check "ubuntu cannot read the credential" '! runuser -u ubuntu -- cat "$CRED" >/dev/null 2>&1'
    check "last run succeeded and is recent" 'python3 -I "$PROG" check >/dev/null 2>&1'
    exit $ok ;;
  disable) systemctl disable --now lifeos-offsite-backup.timer >/dev/null 2>&1 || true; say "timer stopped" ;;
  uninstall)
    systemctl disable --now lifeos-offsite-backup.timer >/dev/null 2>&1 || true
    rm -f "$UNITS/lifeos-offsite-backup.service" "$UNITS/lifeos-offsite-backup.timer"; rm -rf "$OPT"; systemctl daemon-reload
    say "units and code removed (the key, the credential and the status are kept; run forget to remove them)" ;;
  forget) rm -f "$KEYFILE" "$CRED"; say "stored key and credential removed" ;;
  *) say "usage: install-offsite.sh install DIR | key | credential | dry | enable | run-now | verify | disable | uninstall | forget"; exit 2 ;;
esac
