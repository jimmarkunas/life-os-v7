#!/bin/bash
# LIFE-327 (Jim, Nov 6, 2026: "Approved, go"): host the LIFE OS Bot Studio (a static site, the `dist/` of the private repo jimmarkunas/bot-studio) on lifeos-edge-01,
# reachable ONLY over the tailnet. It holds other companies' marks (Claude, Buzz, OpenClaw), so it must never be public: this script has no funnel path and refuses one.
#   sudo bash install.sh dry     --src DIR     DRY RUN (the default verb): every check, every step it would take. Writes nothing.
#   sudo bash install.sh install --src DIR     copies DIR to /opt/lifeos-botstudio/dist, installs and starts lifeos-botstudio.service (loopback 127.0.0.1:8788), publishes it with
#                                              `tailscale serve --bg --https=8443` (tailnet only), then reads it back (HTTP 200 and the served index.html hash).
#   sudo bash install.sh status                what is running and what is published.
#   sudo bash install.sh remove                undoes each step (serve off, service off and deleted, files deleted).
# It never touches the existing serve rule on 443 (OpenClaw), loopback 8787, the Hermes units or any config. Options for tests: --root DIR (prefix for /opt and /etc),
# and the environment variables SYSTEMCTL, TAILSCALE, CURL, SS, PORT, HTTPS_PORT. Prints names, numbers and hashes only.
set -eu
VERB=dry; SRC=""; ROOT=""
SYSTEMCTL=${SYSTEMCTL:-systemctl}; TAILSCALE=${TAILSCALE:-tailscale}; CURL=${CURL:-curl}; SS=${SS:-ss}
PORT=${PORT:-8788}; HTTPS_PORT=${HTTPS_PORT:-8443}
[ $# -gt 0 ] && case "$1" in dry|install|status|remove) VERB=$1; shift ;; esac
while [ $# -gt 0 ]; do
  case "$1" in
    --src) SRC=${2:?}; shift ;;
    --root) ROOT=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
APP="$ROOT/opt/lifeos-botstudio"; DIST="$APP/dist"; UNIT="$ROOT/etc/systemd/system/lifeos-botstudio.service"
MAX_BYTES=5242880
ALLOWED='html|js|mjs|css|svg|png|apng|webp|jpg|jpeg|ico|json|txt|woff2|map|webmanifest'
h64() { sha256sum "$1" | cut -d' ' -f1; }
case "$PORT$HTTPS_PORT" in *[!0-9]*) echo "REFUSED: ports must be numbers"; exit 1 ;; esac
# Ports that belong to something else on this host: never reuse them.
for reserved in 22 443 8787 9119 11434 18789; do
  [ "$PORT" != "$reserved" ] || { echo "REFUSED: port $PORT belongs to another service on this host"; exit 1; }
done
[ "$HTTPS_PORT" != 443 ] || { echo "REFUSED: https port 443 is the OpenClaw rule; this page uses another port"; exit 1; }

unit_text() {
cat <<UNITEOF
[Unit]
Description=LIFE OS Bot Studio (static page; loopback only; published to the tailnet by tailscale serve, LIFE-327)
After=network.target

[Service]
Type=simple
DynamicUser=yes
ExecStart=/usr/bin/python3 -m http.server $PORT --bind 127.0.0.1 --directory /opt/lifeos-botstudio/dist
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadOnlyPaths=/opt/lifeos-botstudio
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_UNIX
IPAddressDeny=any
IPAddressAllow=localhost

[Install]
WantedBy=multi-user.target
UNITEOF
}

check_src() {
  [ -n "$SRC" ] && [ -d "$SRC" ] || { echo "REFUSED: --src DIR is required (the built dist/ folder of bot-studio)"; exit 1; }
  [ -f "$SRC/index.html" ] || { echo "REFUSED: $SRC has no index.html (is it the dist/ folder?)"; exit 1; }
  [ -z "$(find "$SRC" -type l | head -1)" ] || { echo "REFUSED: $SRC contains a symbolic link"; exit 1; }
  total=0; n=0
  while IFS= read -r f; do
    rel=${f#"$SRC"/}
    case "$rel" in *..*|/*) echo "REFUSED: unsafe path $rel"; exit 1 ;; esac
    echo "$rel" | grep -E -q '^[A-Za-z0-9._/@+-]{1,160}$' || { echo "REFUSED: unusual file name $rel"; exit 1; }
    echo "$rel" | grep -E -q "\.($ALLOWED)\$" || { echo "REFUSED: file type not allowed: $rel"; exit 1; }
    size=$(wc -c < "$f" | tr -d ' '); total=$((total + size)); n=$((n + 1))
  done < <(find "$SRC" -type f | sort)
  [ "$n" -gt 0 ] && [ "$total" -le "$MAX_BYTES" ] || { echo "REFUSED: $n files, $total bytes (limit $MAX_BYTES)"; exit 1; }
  echo "  source: $n files, $total bytes, index.html $(h64 "$SRC/index.html" | cut -c1-12)"
}

busy_port() { "$SS" -ltn 2>/dev/null | awk '{print $4}' | grep -E -q "(^|:)$1\$"; }

case "$VERB" in
  dry|install)
    echo "== $([ "$VERB" = install ] && echo INSTALL || echo 'DRY RUN (nothing is written)'): Bot Studio -> $APP, loopback port $PORT, tailnet https port $HTTPS_PORT =="
    check_src
    own=no; [ -f "$UNIT" ] && own=yes
    if busy_port "$PORT" && [ "$own" = no ]; then echo "REFUSED: port $PORT on this host is already in use by something else"; exit 1; fi
    if [ -x "$(command -v "$TAILSCALE" 2>/dev/null || true)" ] || [ -x "$TAILSCALE" ]; then
      status=$("$TAILSCALE" serve status 2>&1 || true)
      if echo "$status" | grep -q ":$HTTPS_PORT" && ! echo "$status" | grep -q "127.0.0.1:$PORT"; then echo "REFUSED: tailnet https port $HTTPS_PORT is already published for something else"; exit 1; fi
    fi
    echo "  1) copy to $DIST (root-owned, read-only), previous copy kept as dist.prev"
    echo "  2) write $UNIT (loopback $PORT, DynamicUser, no capabilities, only localhost allowed)"
    echo "  3) $SYSTEMCTL daemon-reload; enable --now lifeos-botstudio.service"
    echo "  4) $TAILSCALE serve --bg --https=$HTTPS_PORT http://127.0.0.1:$PORT   (tailnet only; funnel is never used)"
    echo "  5) read back: HTTP 200 on loopback and the served index.html hash"
    [ "$VERB" = install ] || { echo "Dry run only. To apply: sudo bash $0 install --src $SRC"; exit 0; }
    install -d -m 0755 "$APP"
    NEW="$APP/dist.new"; rm -rf "$NEW"; install -d -m 0755 "$NEW"
    ( cd "$SRC" && find . -type d | while read -r d; do install -d -m 0755 "$NEW/$d"; done; find . -type f | while read -r f; do install -m 0644 "$f" "$NEW/$f"; done )
    [ "$(h64 "$NEW/index.html")" = "$(h64 "$SRC/index.html")" ] || { echo "READ-BACK FAILED: the copy differs"; rm -rf "$NEW"; exit 1; }
    rm -rf "$APP/dist.prev"; [ ! -d "$DIST" ] || mv "$DIST" "$APP/dist.prev"; mv "$NEW" "$DIST"
    install -d -m 0755 "$(dirname "$UNIT")"
    unit_text > "$UNIT.tmp"; chmod 0644 "$UNIT.tmp"; mv "$UNIT.tmp" "$UNIT"
    "$SYSTEMCTL" daemon-reload
    "$SYSTEMCTL" enable --now lifeos-botstudio.service
    "$SYSTEMCTL" restart lifeos-botstudio.service
    sleep 1
    "$TAILSCALE" serve --bg --https="$HTTPS_PORT" "http://127.0.0.1:$PORT"
    code=$("$CURL" -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || echo 000)
    served=$("$CURL" -s "http://127.0.0.1:$PORT/index.html" | sha256sum | cut -d' ' -f1)
    [ "$code" = 200 ] || { echo "READ-BACK FAILED: loopback answered HTTP $code"; exit 1; }
    [ "$served" = "$(h64 "$SRC/index.html")" ] || { echo "READ-BACK FAILED: the served index.html is not the source one"; exit 1; }
    echo "== read-back OK: HTTP 200, index.html $(echo "$served" | cut -c1-12) =="
    echo "Open it from a device on the tailnet: https://<this host's tailnet name>:$HTTPS_PORT   (see: sudo bash $0 status)"
    ;;
  status)
    echo "== Bot Studio status =="
    echo "  files: $( [ -f "$DIST/index.html" ] && echo "present, index.html $(h64 "$DIST/index.html" | cut -c1-12)" || echo missing)"
    echo "  service: $("$SYSTEMCTL" is-active lifeos-botstudio.service 2>&1 || true)"
    echo "  loopback: HTTP $("$CURL" -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null || echo 000)"
    echo "  tailnet publication:"; "$TAILSCALE" serve status 2>&1 | grep -E ":$HTTPS_PORT|127.0.0.1:$PORT" | sed 's/^/    /' || true
    ;;
  remove)
    echo "== REMOVE Bot Studio =="
    "$TAILSCALE" serve --https="$HTTPS_PORT" off 2>&1 || true
    "$SYSTEMCTL" disable --now lifeos-botstudio.service 2>&1 || true
    rm -f "$UNIT"; "$SYSTEMCTL" daemon-reload
    rm -rf "$APP"
    echo "== removed: serve rule on $HTTPS_PORT, the service, $APP. The rule on 443 and everything else were not touched. =="
    ;;
esac
exit 0
