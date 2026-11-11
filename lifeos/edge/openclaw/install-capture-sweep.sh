#!/usr/bin/env bash
# LIFE-575: install the plugin-capture sweeper as a USER timer on the Oracle VM (run as the ubuntu user; no root, no gateway restart, no change to the gateway unit).
#   install-capture-sweep.sh install   copy capture_sweep.py, write the service and a 6-hourly timer, enable the timer
#   install-capture-sweep.sh remove    disable the timer and delete the three files (rollback)
# Run from a repo checkout: the script copies the sweeper file that sits beside it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LIB="$HOME/.local/lib/lifeos-capture-sweep"
UNITS="$HOME/.config/systemd/user"
case "${1:-}" in
  install)
    mkdir -p "$LIB" "$UNITS"
    install -m 0644 "$HERE/capture_sweep.py" "$LIB/capture_sweep.py"
    cat > "$UNITS/lifeos-capture-sweep.service" <<UNIT
[Unit]
Description=LIFE-575: remove OpenClaw plugin-capture folders no process uses

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I $LIB/capture_sweep.py --delete
Nice=10
UNIT
    cat > "$UNITS/lifeos-capture-sweep.timer" <<UNIT
[Unit]
Description=LIFE-575: sweep orphaned OpenClaw plugin-capture folders every 6 hours

[Timer]
OnBootSec=30min
OnUnitActiveSec=6h
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now lifeos-capture-sweep.timer
    systemctl --user list-timers lifeos-capture-sweep.timer --no-pager
    ;;
  remove)
    systemctl --user disable --now lifeos-capture-sweep.timer || true
    rm -f "$UNITS/lifeos-capture-sweep.timer" "$UNITS/lifeos-capture-sweep.service" "$LIB/capture_sweep.py"
    rmdir "$LIB" 2>/dev/null || true
    systemctl --user daemon-reload
    ;;
  *) echo "usage: $0 install|remove" >&2; exit 2 ;;
esac
