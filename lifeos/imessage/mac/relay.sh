#!/bin/bash
# COMM-1.4 relay on the Mac Mini: Tailscale Serve (tailnet only, PROXY v2) -> loopback relay -> the existing signed helper's Unix socket.
#   bash relay.sh install <commit> <oracle tailnet IPv4> <oracle tailnet IPv6>   build + install the relay, add one Serve TCP port, prove it, print the summary
#   bash relay.sh update <commit> <oracle tailnet IPv4> <oracle tailnet IPv6>    replace only the reader script and the relay binary (previous kept as .prev)
#   bash relay.sh rollback                                                       put the .prev reader script and relay back
#   bash relay.sh uninstall                                                      remove only what this script added (the helper and its grant stay)
# Never rebuilds, re-signs or replaces the signed Messages helper (the launcher that holds Full Disk Access), never touches privacy settings, never enables
# Funnel, never changes other Serve ports. `update` swaps the root-owned reader script the launcher runs: the grant belongs to the launcher, so it stays valid,
# and the checks prove that by reading Messages again through it.
# The Oracle addresses are build inputs only: never printed, logged or committed. Every mandatory check must PASS, otherwise "RELAY FAILED", the rollback
# command and a nonzero exit.
set -euo pipefail

LABEL="ai.lifeos.imessage-relay"
DIR="/usr/local/libexec/lifeos-imessage"
HELPER="$DIR/lifeos-imessage-attention"
TOOL="$DIR/attention_mcp.py"
RELAY="$DIR/lifeos-imessage-relay"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
HELPER_SOCK="$HOME/Library/Application Support/LifeOS/imessage-attention.sock"
DOMAIN="gui/$(id -u)"
LOCAL_PORT=47321
TAILNET_PORT=47331
TS="/usr/local/bin/tailscale"
REPO="https://raw.githubusercontent.com/jimmarkunas/life-os-v7"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
SHA_RELAY="62d74eef27df24d75c3bb08a131cebf9d272d365f4e611eafffaf94e60266250"
SHA_AGENT="cd619461ed3e78e76ec94402aa3e206c8d5948bd861671c269f08a665be6f99b"
SHA_TOOL="24d0cbda96975136cab6a0b6b709c48c20b01c13998824fc25f6c6f766a69498"

say() { printf '%s\n' "$*"; }
die() { say "STOP: $*"; exit 1; }
RESULTS=()
record() { RESULTS+=("$1: $2"); say "  [$2] $1"; }

preflight() {
  [ "$(uname -s)" = "Darwin" ] || die "not macOS"
  [ "$(id -u)" -ne 0 ] || die "run as Jim's user, not root"
  for t in /usr/bin/cc /usr/bin/codesign /usr/bin/python3 /usr/bin/shasum /usr/bin/plutil /bin/launchctl /usr/bin/curl /usr/sbin/netstat "$TS"; do [ -x "$t" ] || die "missing $t"; done
  [ -x "$HELPER" ] && [ -S "$HELPER_SOCK" ] || die "the Messages helper from the local proof is not installed; nothing was changed"
}

helper_cdhash() {
  local out
  out="$(/usr/bin/codesign -d --verbose=4 "$HELPER" 2>&1)" || return 0
  /usr/bin/awk -F= '$1 == "CDHash" && $2 ~ /^[0-9A-Fa-f]{40}$/ { print tolower($2); exit }' <<<"$out"
}

helper_reads() {   # helper_reads [tool]: the existing helper, asked over its own socket; health CURRENT proves it still reads Messages under its own grant
  /usr/bin/python3 -I - "$HELPER_SOCK" "${1:-imessage_attention}" <<'HELPER_PY'
import json, socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(15)
s.connect(sys.argv[1])
s.sendall(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": sys.argv[2], "arguments": {}}}).encode() + b"\n")
s.shutdown(socket.SHUT_WR)
data = b""
while len(data) <= 65536:
    chunk = s.recv(4096)
    if not chunk:
        break
    data += chunk
state = json.loads(data)["result"]["structuredContent"]
ok = state["health"] == "CURRENT" and (state["count"] is None if sys.argv[2] == "imessage_attention" else state["counts"] is not None)
sys.exit(0 if ok else 1)
HELPER_PY
}

relay_probe() {   # local negative checks against the loopback listener; prints "name<TAB>PASS|FAIL" lines
  /usr/bin/python3 -I - "$LOCAL_PORT" "$1" <<'PROBE_PY'
import ipaddress, socket, struct, sys
port, oracle = int(sys.argv[1]), ipaddress.IPv4Address(sys.argv[2])
SIG = b"\r\n\r\n\x00\r\nQUIT\n"

def v2(src, cmd=0x21):
    return SIG + bytes([cmd, 0x11]) + struct.pack("!H", 12) + ipaddress.IPv4Address(src).packed + ipaddress.IPv4Address("127.0.0.1").packed + struct.pack("!HH", 50000, port)

def ask(payload):
    data = b""
    with socket.create_connection(("127.0.0.1", port), timeout=15) as s:
        try:
            s.sendall(payload)
            s.shutdown(socket.SHUT_WR)
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        except (BrokenPipeError, ConnectionResetError):
            pass                                              # the relay closed on a refused request
    return data

unauthorized = ipaddress.IPv4Address(int(oracle) ^ 1)
cases = [("no PROXY header is refused", b"attention\n"),
         ("a PROXY v1 (text) header is refused", b"PROXY TCP4 %s 127.0.0.1 50000 %d\r\nattention\n" % (str(oracle).encode(), port)),
         ("a malformed PROXY v2 signature is refused", b"\r\n\r\n\x00\r\nQUIX\n" + v2(oracle)[12:] + b"attention\n"),
         ("a PROXY v2 LOCAL command is refused", v2(oracle, cmd=0x20) + b"attention\n"),
         ("an unauthorized tailnet source is refused", v2(unauthorized) + b"attention\n"),
         ("send is refused", v2(oracle) + b"send\n"),
         ("SQL or a path as the request is refused", v2(oracle) + b"SELECT text FROM message\n"),
         ("an oversized request is refused", v2(oracle) + b"attention" + b"A" * 4096 + b"\n"),
         ("an out-of-range hours argument is refused", v2(oracle) + b"recent 73\n"),
         ("a zero conversation reference is refused", v2(oracle) + b"conversation 0\n"),
         ("shell text in an argument is refused", v2(oracle) + b"recent 1; id\n"),
         ("a non-numeric conversation reference is refused", v2(oracle) + b"conversation ../chat.db\n")]
for name, payload in cases:
    print("%s\t%s" % (name, "PASS" if ask(payload) == b"" else "FAIL"))
PROBE_PY
}

agent_exact() {
  /usr/bin/python3 -I - "$AGENT" "$LABEL" "$RELAY" "$LOCAL_PORT" <<'AGENT_PY'
import plistlib, sys
agent, label, program, port = sys.argv[1:5]
try:
    with open(agent, "rb") as f:
        got = plistlib.load(f)
except Exception:
    sys.exit(1)
want = {"Label": label, "Program": program, "ProgramArguments": [program], "inetdCompatibility": {"Wait": False}, "ProcessType": "Background",
        "Sockets": {"Listener": {"SockType": "stream", "SockFamily": "IPv4", "SockNodeName": "127.0.0.1", "SockServiceName": port}}}
sys.exit(0 if got == want and got["inetdCompatibility"]["Wait"] is False else 1)
AGENT_PY
}

serve_state() {   # serve_state: "ours" (exactly our forwarder, PROXY v2, no Funnel), "free" (port unused), "other", or "unknown" (status unreadable)
  local json
  json="$("$TS" serve status --json 2>/dev/null)" || { echo unknown; return 0; }
  /usr/bin/python3 -I - "$TAILNET_PORT" "$LOCAL_PORT" "$json" <<'SERVE_PY' || echo unknown
import json, sys
port, local, raw = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.loads(raw.strip() or "{}") or {}
handler = (cfg.get("TCP") or {}).get(port)
funnel = any(v and str(k).endswith(":" + port) for k, v in (cfg.get("AllowFunnel") or {}).items())
web = any(str(k).endswith(":" + port) for k in (cfg.get("Web") or {}))
if handler is None and not funnel and not web:
    print("free")
elif handler == {"TCPForward": "127.0.0.1:" + local, "ProxyProtocol": 2} and not funnel and not web:
    print("ours")
else:
    print("other")
SERVE_PY
}

listener_loopback_only() {   # every listener on the relay port is 127.0.0.1, and there is at least one
  /usr/sbin/netstat -an -p tcp | /usr/bin/awk -v p=".$LOCAL_PORT" '$NF == "LISTEN" && substr($4, length($4) - length(p) + 1) == p { n++; if ($4 != "127.0.0.1" p) bad = 1 } END { exit (n > 0 && !bad) ? 0 : 1 }'
}

check_all() {   # check_all <oracle IPv4>
  local before="$1"
  agent_exact && record "relay LaunchAgent is on-demand with one 127.0.0.1 TCP listener and nothing else" PASS || record "relay LaunchAgent is on-demand with one 127.0.0.1 TCP listener and nothing else" FAIL
  /bin/launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 && record "relay LaunchAgent loaded" PASS || record "relay LaunchAgent loaded" FAIL
  listener_loopback_only && record "relay port listens on 127.0.0.1 only (no LAN or tailnet listener)" PASS || record "relay port listens on 127.0.0.1 only (no LAN or tailnet listener)" FAIL
  [ "$(serve_state)" = ours ] && record "Serve forwards tailnet port $TAILNET_PORT to the relay with PROXY v2, tailnet only (no Funnel)" PASS \
    || record "Serve forwards tailnet port $TAILNET_PORT to the relay with PROXY v2, tailnet only (no Funnel)" FAIL
  [ "$(stat -f '%Su:%Sg %Lp' "$RELAY")" = "root:wheel 755" ] && record "relay binary is root-owned" PASS || record "relay binary is root-owned" FAIL
  while IFS=$'\t' read -r name status; do [ -n "$name" ] && record "$name" "$status"; done <<<"$(relay_probe "$2" || printf 'relay probe ran\tFAIL\n')"
  [ -n "$before" ] && [ "$(helper_cdhash)" = "$before" ] && record "Messages helper unchanged (same signed CDHash)" PASS || record "Messages helper unchanged (same signed CDHash)" FAIL
  helper_reads && record "Messages helper still reads under its own grant (health CURRENT)" PASS || record "Messages helper still reads under its own grant (health CURRENT)" FAIL
}

summary() {
  say ""
  say "=== COMM-1.4 RELAY (paste this block back; it holds no addresses, message content or identities) ==="
  say "macOS $(sw_vers -productVersion) | tailscale $("$TS" version 2>/dev/null | head -1) | ref ${REF:-n/a}"
  for line in "${RESULTS[@]}"; do say "$line"; done
  say "$1"
  say "==="
}

gate() {   # gate <required PASS count>
  local all pass other
  all="$(printf '%s\n' "${RESULTS[@]}")"
  pass="$(grep -c ': PASS$' <<<"$all" || true)"
  other="$(grep -vc ': PASS$' <<<"$all" || true)"
  if [ "$other" -eq 0 ] && [ "$pass" -eq "$1" ]; then
    summary "RESULT: RELAY PASSED ($1 of $1 required checks)"
    return 0
  fi
  REPORTED=1
  summary "RESULT: RELAY FAILED ($pass of $1 required checks passed; $other failed or unverified)"
  if [ -f "$RELAY.prev" ]; then
    say "Roll back with:  bash \"$SELF\" rollback   (puts the previous reader and relay back; nothing else changes)"
  else
    say "Roll back with:  bash \"$SELF\" uninstall   (removes only the relay and its Serve port; the Messages helper and its grant stay)"
  fi
  exit 1
}

install_relay() {
  REF="${1:-}"; local v4="${2:-}" v6="${3:-}"
  [ -n "$REF" ] && [ -n "$v4" ] && [ -n "$v6" ] || die "usage: bash relay.sh install <commit> <oracle tailnet IPv4> <oracle tailnet IPv6>"
  /usr/bin/python3 -I -c 'import ipaddress,sys; a=ipaddress.IPv4Address(sys.argv[1]); b=ipaddress.IPv6Address(sys.argv[2]); sys.exit(0 if a in ipaddress.ip_network("100.64.0.0/10") and b in ipaddress.ip_network("fd7a:115c:a1e0::/48") else 1)' "$v4" "$v6" \
    || die "the Oracle addresses are not tailnet addresses; nothing was changed"
  "$TS" serve --help 2>&1 | grep -q -- '--proxy-protocol' || die "this Tailscale has no Serve PROXY protocol support; nothing was changed"
  case "$(serve_state)" in free|ours) ;; *) die "tailnet port $TAILNET_PORT has another Serve/Funnel configuration, or Serve status is unreadable; nothing was changed";; esac
  say "1/5 Messages helper before any change"
  local before; before="$(helper_cdhash)"
  [ -n "$before" ] && helper_reads || die "the Messages helper is not reading (or has no code hash); nothing was changed"
  say "  helper CDHash $before reads Messages: yes"
  TMPD="$(mktemp -d)"; local tmp="$TMPD"
  say "2/5 Downloading the two reviewed relay files at $REF (hash-checked)"
  curl -fsSL "$REPO/$REF/lifeos/imessage/mac/relay.c" -o "$tmp/relay.c" && echo "$SHA_RELAY  $tmp/relay.c" | shasum -a 256 -c - >/dev/null 2>&1 || die "relay.c download or hash failed"
  curl -fsSL "$REPO/$REF/lifeos/imessage/mac/ai.lifeos.imessage-relay.plist" -o "$tmp/agent.plist" && echo "$SHA_AGENT  $tmp/agent.plist" | shasum -a 256 -c - >/dev/null 2>&1 \
    || die "relay plist download or hash failed"
  say "3/5 Building the relay (no privacy grant; Oracle addresses compiled in, not printed)"
  /usr/bin/cc -O2 -Wall -Werror -DALLOWED_V4="\"$v4\"" -DALLOWED_V6="\"$v6\"" -o "$tmp/lifeos-imessage-relay" "$tmp/relay.c"
  INSTALLED=1
  say "4/5 Installing the relay and its on-demand loopback LaunchAgent (macOS may ask for your password)"
  sudo /usr/bin/install -o root -g wheel -m 755 "$tmp/lifeos-imessage-relay" "$RELAY"
  install -m 644 "$tmp/agent.plist" "$AGENT"
  /bin/launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  /bin/launchctl bootstrap "$DOMAIN" "$AGENT"
  say "5/5 Adding one tailnet-only Serve TCP port ($TAILNET_PORT -> 127.0.0.1:$LOCAL_PORT, PROXY v2)"
  [ "$(serve_state)" = ours ] || "$TS" serve --bg --tcp "$TAILNET_PORT" --proxy-protocol 2 "tcp://127.0.0.1:$LOCAL_PORT" >/dev/null
  say "Checks:"
  check_all "$before" "$v4"
  gate 19
}

update_relay() {
  REF="${1:-}"; local v4="${2:-}" v6="${3:-}"
  [ -n "$REF" ] && [ -n "$v4" ] && [ -n "$v6" ] || die "usage: bash relay.sh update <commit> <oracle tailnet IPv4> <oracle tailnet IPv6>"
  /usr/bin/python3 -I -c 'import ipaddress,sys; a=ipaddress.IPv4Address(sys.argv[1]); b=ipaddress.IPv6Address(sys.argv[2]); sys.exit(0 if a in ipaddress.ip_network("100.64.0.0/10") and b in ipaddress.ip_network("fd7a:115c:a1e0::/48") else 1)' "$v4" "$v6" \
    || die "the Oracle addresses are not tailnet addresses; nothing was changed"
  [ "$(serve_state)" = ours ] && [ -x "$RELAY" ] && [ -f "$TOOL" ] || die "the COMM-1.4 relay is not installed (run install first); nothing was changed"
  say "1/4 Messages helper before any change"
  local before; before="$(helper_cdhash)"
  [ -n "$before" ] && helper_reads || die "the Messages helper is not reading (or has no code hash); nothing was changed"
  say "  helper CDHash $before reads Messages: yes"
  TMPD="$(mktemp -d)"; local tmp="$TMPD"
  say "2/4 Downloading the reviewed reader and relay at $REF (hash-checked)"
  curl -fsSL "$REPO/$REF/lifeos/imessage/attention_mcp.py" -o "$tmp/attention_mcp.py" && echo "$SHA_TOOL  $tmp/attention_mcp.py" | shasum -a 256 -c - >/dev/null 2>&1 \
    || die "reader download or hash failed"
  curl -fsSL "$REPO/$REF/lifeos/imessage/mac/relay.c" -o "$tmp/relay.c" && echo "$SHA_RELAY  $tmp/relay.c" | shasum -a 256 -c - >/dev/null 2>&1 || die "relay.c download or hash failed"
  /usr/bin/cc -O2 -Wall -Werror -DALLOWED_V4="\"$v4\"" -DALLOWED_V6="\"$v6\"" -o "$tmp/lifeos-imessage-relay" "$tmp/relay.c"
  INSTALLED=1
  say "3/4 Replacing the reader script and relay binary (previous versions kept as .prev; macOS may ask for your password)"
  sudo /bin/cp -p "$TOOL" "$TOOL.prev"
  sudo /bin/cp -p "$RELAY" "$RELAY.prev"
  sudo /usr/bin/install -o root -g wheel -m 644 "$tmp/attention_mcp.py" "$TOOL"
  sudo /usr/bin/install -o root -g wheel -m 755 "$tmp/lifeos-imessage-relay" "$RELAY"
  say "4/4 Checks:"
  check_all "$before" "$v4"
  [ "$(shasum -a 256 "$TOOL" | cut -d' ' -f1)" = "$SHA_TOOL" ] && [ "$(stat -f '%Su:%Sg %Lp' "$TOOL")" = "root:wheel 644" ] \
    && record "reader script is the reviewed version and root-owned" PASS || record "reader script is the reviewed version and root-owned" FAIL
  helper_reads imessage_counts && record "the new reader reads Messages counts under the existing grant" PASS \
    || record "the new reader reads Messages counts under the existing grant" FAIL
  gate 21
}

rollback() {
  [ -f "$TOOL.prev" ] && [ -f "$RELAY.prev" ] || die "no .prev reader and relay to restore"
  sudo /bin/mv "$TOOL.prev" "$TOOL"
  sudo /bin/mv "$RELAY.prev" "$RELAY"
  helper_reads && say "Restored the previous reader and relay; the helper still reads Messages." || say "Restored the previous reader and relay; the helper read check FAILED."
}

uninstall() {
  [ "$(serve_state)" = ours ] && "$TS" serve --tcp "$TAILNET_PORT" off >/dev/null 2>&1 || true
  /bin/launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  rm -f "$AGENT"
  sudo rm -f "$RELAY" "$RELAY.prev"
  say "Rolled back: Serve port $TAILNET_PORT removed (if it was ours), relay LaunchAgent unloaded and removed, relay binary deleted."
  say "The Messages helper, its socket and its Full Disk Access grant were not touched."
}

on_exit() {
  local rc=$?
  [ -z "${TMPD:-}" ] || rm -rf "$TMPD"
  if [ "$rc" -ne 0 ] && [ "$INSTALLED" -eq 1 ] && [ "$REPORTED" -eq 0 ]; then
    say "RESULT: RELAY FAILED (a step stopped unexpectedly, exit $rc)"
    if [ -f "$RELAY.prev" ]; then say "Roll back with:  bash \"$SELF\" rollback"; else say "Roll back with:  bash \"$SELF\" uninstall"; fi
  fi
}

INSTALLED=0
REPORTED=0
trap on_exit EXIT
preflight
case "${1:-}" in
  install) install_relay "${2:-}" "${3:-}" "${4:-}" ;;
  update) update_relay "${2:-}" "${3:-}" "${4:-}" ;;
  rollback) rollback ;;
  uninstall) uninstall ;;
  *) die "usage: bash relay.sh install|update <commit> <oracle tailnet IPv4> <oracle tailnet IPv6> | rollback | uninstall" ;;
esac
