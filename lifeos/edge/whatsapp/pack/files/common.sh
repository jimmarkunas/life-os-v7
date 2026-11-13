# LIFE-643: shared by the WhatsApp pack steps (sourced, never run alone). Names, modes and counts only; no session file is ever opened.
R=$(cat "$PACK_DIR/files/ROOT" 2>/dev/null || true)     # empty on the host; a temp folder in the tests
HOME_DIR="$R/home/ubuntu"
STATE_DIR="$HOME_DIR/.local/state/lifeos-whatsapp"
CRED_DIR="$HOME_DIR/.openclaw/credentials/whatsapp"
UNIT="$HOME_DIR/.config/systemd/user/lifeos-whatsapp-attention.service"
SVC=lifeos-whatsapp-attention.service
GW=openclaw-gateway.service
LIST="$R/var/lib/lifeos-runner/whatsapp-modes-LIFE-643.list"
SOCK="$STATE_DIR/attention.sock"
SLEEP="$R/usr/bin/sleep"
PY=python3; [ -x "$R/usr/bin/python3" ] && PY="$R/usr/bin/python3"
if [ -z "$R" ]; then OWNER_UID=$(id -u ubuntu 2>/dev/null || echo 0); else OWNER_UID=$(id -u); fi

wa() { "$PY" -I "$PACK_DIR/files/wa_host.py" "$@"; }

# systemctl --user for the ubuntu user, run from root (the runner is root): runuser with that user's runtime folder and bus.
usys() {
  if [ -z "$R" ]; then
    runuser -u ubuntu -- env "XDG_RUNTIME_DIR=/run/user/$OWNER_UID" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$OWNER_UID/bus" /usr/bin/systemctl --user "$@"
  else
    "$R/usr/bin/systemctl" --user "$@"
  fi
}

conn_count() { "$R/usr/bin/ss" -tnpH state established 2>/dev/null | grep -c "pid=$1," || true; }
