#!/bin/bash
# LIFE-647 groups 2+3: put four smaller Python files on Oracle, each only if the host holds exactly the file this change replaces (files/table: name, host path, sha256 of the old file, sha256 of the new one).
#   A  read-only gates, a miss stops with one fixed word and changes nothing: every host file is a plain file (no link) with the old sha256 (or already the new one); the forward unit is active when its file changes.
#   B  copies of the old files go to a temp folder; each changed file is installed with the owner, group and mode it has now; syntax check; the endpoint answers (WhatsApp health); the iMessage wrapper prints its usage line.
#   C  only if forward_alerts.py changed: restart that one ubuntu user unit; it must be active with one process id and no restart count change. A failure after B puts every old file back (and restarts the unit again).
# Names, counts and fixed words only; no session, key or message text is opened or printed. The endpoint and the two adapters run per call, so they need no restart.
#   10-install.sh [--dry-run]   --dry-run runs only A. Test hooks (the host leaves them unset): G_ROOT (prefix for host paths), G_USYS (user systemctl), G_AS_UBUNTU, G_PY, G_SLEEP.
set -u
R=${G_ROOT:-}
PY=${G_PY:-python3}
F=${PACK_DIR:+$PACK_DIR/files}; F=${F:-$(cd "$(dirname "$0")/../files" && pwd)}
UNIT=lifeos-alerts-whatsapp.service
say() { echo "READBACK $1=$2"; }
fail() { say failed "$1"; exit 1; }
usys() {
  if [ -n "${G_USYS:-}" ]; then "$G_USYS" "$@"; return; fi
  local uid; uid=$(id -u ubuntu) || return 1
  runuser -u ubuntu -- env "XDG_RUNTIME_DIR=/run/user/$uid" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus" /usr/bin/systemctl --user "$@"
}
as_ubuntu() { if [ -n "${G_AS_UBUNTU:-}" ]; then "$G_AS_UBUNTU" "$@"; else runuser -u ubuntu -- "$@"; fi; }
meta() { "$PY" -I -c 'import hashlib,os,sys,pwd,grp
s=os.stat(sys.argv[1]); print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest(), pwd.getpwuid(s.st_uid).pw_name, grp.getgrgid(s.st_gid).gr_name, "%o" % (s.st_mode & 0o7777))' "$1" 2>/dev/null; }
hostof() { awk -v n="$1" '$1 == n {print $2}' "$F/table"; }
ENDPOINT_PY='import importlib.util as u,sys
sp=u.spec_from_file_location("ep",sys.argv[1]); ep=u.module_from_spec(sp); sp.loader.exec_module(ep)
print(ep.whatsapp("0"*64, sys.argv[2] if len(sys.argv)>2 else None).get("health"))'

# ---- A: gates (read-only)
todo=(); same=0; restart=no
while read -r name host pin new; do
  path="$R$host"
  { [ -f "$path" ] && [ ! -L "$path" ]; } || fail "${name}_not_a_plain_file"
  read -r sha _ <<< "$(meta "$path")"
  if [ "$sha" = "$new" ]; then same=$((same + 1)); elif [ "$sha" = "$pin" ]; then todo+=("$name"); [ "$name" != forward_alerts.py ] || restart=yes; else fail "${name}_host_differs"; fi
done < "$F/table"
[ "$restart" = no ] || [ "$(usys is-active "$UNIT" 2>/dev/null)" = active ] || fail forward_unit_not_active
say gates "to_install=${#todo[@]} already_new=$same restart_forward=$restart"
[ "${1:-}" != "--dry-run" ] || { say dry_run no_change; exit 0; }
[ "${#todo[@]}" -gt 0 ] || { say result nothing_to_do; exit 0; }

# ---- B: install, keeping copies of the old files
BK=$(mktemp -d) || fail temp_dir
restarted=no
stop() {      # put every old file back, restart the unit again if this run restarted it, then fail with the word
  for n in "${todo[@]}"; do cp -p "$BK/$n" "$R$(hostof "$n")"; done
  [ "$restarted" = no ] || usys restart "$UNIT" > /dev/null 2>&1
  fail "$1"
}
for n in "${todo[@]}"; do
  path="$R$(hostof "$n")"
  read -r _ owner group mode <<< "$(meta "$path")"
  cp -p "$path" "$BK/$n"
  OWN="-o $owner -g $group"; [ -z "$R" ] || OWN=""
  # shellcheck disable=SC2086
  install $OWN -m "$mode" "$F/$n" "$path" || stop "${n}_install"
  "$PY" -I -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$path" || stop "${n}_syntax"
  case "$n" in
    alerts_endpoint.py) eh=$(as_ubuntu "$PY" -I -c "$ENDPOINT_PY" "$path" ${R:+"$R/run/lifeos-wa/attention.sock"} 2>/dev/null | tail -1); { [ "$eh" = CURRENT ] || [ "$eh" = DEGRADED ]; } || stop "endpoint_health_${eh:-none}" ;;
    openclaw_imessage.py) as_ubuntu "$PY" -I "$path" 2>/dev/null | grep -q '^usage:' || stop wrapper_usage ;;
  esac
done

# ---- C: the one restart
if [ "$restart" = yes ]; then
  restarted=yes; usys restart "$UNIT" > /dev/null 2>&1 || stop forward_restart
  p1=$(usys show -p MainPID --value "$UNIT"); n1=$(usys show -p NRestarts --value "$UNIT"); "${G_SLEEP:-sleep}" 8
  { [ "$(usys is-active "$UNIT")" = active ] && [ "${p1:-0}" != 0 ] && [ "$p1" = "$(usys show -p MainPID --value "$UNIT")" ] && [ "$n1" = "$(usys show -p NRestarts --value "$UNIT")" ]; } || stop forward_unit_unstable
fi
rm -rf "$BK"
say installed "files=${#todo[@]} restart=$restart"
say result "installed_${#todo[@]} already_new_$same"
