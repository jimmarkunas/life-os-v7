#!/bin/sh
# LIFE-643 row 1. Installed root-only at /opt/lifeos-wa/bin/resume-old.sh; run by lifeos-wa-attention.service (ExecStopPost=+) and by the pack's own rollback.
# While the switch is in progress the pack keeps a flag file. If the new service stops for ANY reason in that window (a failed check, the runner's Ctrl-C/timeout restore,
# a crash), the old ubuntu user unit is enabled and started again, so there is again exactly one live copy of the linked-device session. Without the flag it does nothing,
# so a later normal stop of the new service never starts the old one. Idempotent; prints nothing.
R=${WA_ROOT:-}
FLAG="$R/var/lib/lifeos-runner/whatsapp-row1.switching"
[ -e "$FLAG" ] || exit 0
OWNER_UID=$(id -u ubuntu 2>/dev/null) || exit 0
u() {
  if [ -z "$R" ]; then
    /usr/sbin/runuser -u ubuntu -- env "XDG_RUNTIME_DIR=/run/user/$OWNER_UID" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$OWNER_UID/bus" /usr/bin/systemctl --user "$@"
  else
    "$R/usr/bin/systemctl" --user "$@"
  fi
}
u enable lifeos-whatsapp-attention.service > /dev/null 2>&1
u start lifeos-whatsapp-attention.service > /dev/null 2>&1 || exit 0
rm -f "$FLAG"
exit 0
