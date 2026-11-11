#!/usr/bin/env bash
# LIFE-576 option (a): the persistent headless Chromium that keeps Jim's Jobright sign-in, as a USER service on the Oracle VM (run as the ubuntu user; no root).
#   install-browser.sh install   write the unit and start it
#   install-browser.sh remove    stop and delete the unit (the saved profile folder stays until you delete it by hand)
# Same Chrome for Testing build that OpenClaw uses. It listens on 127.0.0.1:9223 ONLY; the runner reaches it through a forward-only SSH user (install-forward-user.sh).
# Resource cap from the LIFE-576 measurement: 3 GB of memory, one core, low priority.
set -euo pipefail
UNITS="$HOME/.config/systemd/user"
CHROME=/opt/openclaw-chromium/chrome-linux-arm64/chrome
case "${1:-}" in
  install)
    mkdir -p "$UNITS" "$HOME/.lifeos-jobright-profile"
    chmod 700 "$HOME/.lifeos-jobright-profile"
    cat > "$UNITS/lifeos-jobright-browser.service" <<UNIT
[Unit]
Description=LIFE-576: persistent headless Chromium for the Jobright resolver (127.0.0.1:9223 only)

[Service]
ExecStart=$CHROME --headless=new --no-sandbox --disable-dev-shm-usage --disable-blink-features=AutomationControlled --remote-debugging-address=127.0.0.1 --remote-debugging-port=9223 --user-data-dir=%h/.lifeos-jobright-profile --window-size=1920,1080 about:blank
Restart=on-failure
RestartSec=10
MemoryMax=3G
CPUQuota=100%
Nice=15

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now lifeos-jobright-browser.service
    systemctl --user is-active lifeos-jobright-browser.service
    ;;
  remove)
    systemctl --user disable --now lifeos-jobright-browser.service || true
    rm -f "$UNITS/lifeos-jobright-browser.service"
    systemctl --user daemon-reload
    ;;
  *) echo "usage: $0 install|remove" >&2; exit 2 ;;
esac
