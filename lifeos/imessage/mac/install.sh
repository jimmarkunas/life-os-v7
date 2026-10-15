#!/bin/bash
# COMM-1.4 local proof on the Mac Mini: install, prove and roll back the signed, launchd-started iMessage attention helper.
#   bash install.sh install <commit>   build + sign + install, test without Full Disk Access, wait for Jim's grant, test with it, print the proof summary
#   bash install.sh verify             rerun the live tests only
#   bash install.sh uninstall          full rollback (the Full Disk Access entry is removed by hand)
# Safe to rerun over an earlier attempt: files are replaced, the agent is unloaded and reloaded, the old socket is cleared.
# Every mandatory check must PASS: a FAIL, an UNVERIFIED or a missing check ends with "LOCAL PROOF FAILED", the rollback command and a nonzero exit.
# Local IPC only: one owner-only Unix socket held by launchd. No network listener, timer, credentials, Oracle or OpenClaw change. Prints aggregates and fixed codes only.
set -euo pipefail

LABEL="ai.lifeos.imessage-attention"
DIR="/usr/local/libexec/lifeos-imessage"
BIN="$DIR/lifeos-imessage-attention"
TOOL="$DIR/attention_mcp.py"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
SOCKDIR="$HOME/Library/Application Support/LifeOS"
SOCK="$SOCKDIR/imessage-attention.sock"
DOMAIN="gui/$(id -u)"
DB="$HOME/Library/Messages/chat.db"
TCCDB="/Library/Application Support/com.apple.TCC/TCC.db"
REPO="https://raw.githubusercontent.com/jimmarkunas/life-os-v7"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
OPENCLAW_APP="/Applications/OpenClaw.app"
FDA="kTCCServiceSystemPolicyAllFiles"
# sha256 of the three source files at the published commit (a test keeps these in step with the repository)
SHA_TOOL="24d0cbda96975136cab6a0b6b709c48c20b01c13998824fc25f6c6f766a69498"
SHA_LAUNCHER="cc275afab87d5f1d817055641ca04985bbe1c4e3ae2ad54f49a9ca8954b4005c"
SHA_PLIST="6e22bffc15943491fdf547a8e6e06cc55bc18c356e6fad159fd78036907cadd0"

say() { printf '%s\n' "$*"; }
die() { say "STOP: $*"; exit 1; }
RESULTS=()
record() { RESULTS+=("$1: $2"); say "  [$2] $1"; }

preflight() {
  [ "$(uname -s)" = "Darwin" ] || die "not macOS"
  [ "$(id -u)" -ne 0 ] || die "run as Jim's user, not root (sudo is asked for only to install root-owned files)"
  for t in /usr/bin/cc /usr/bin/codesign /usr/bin/python3 /usr/bin/shasum /usr/bin/plutil /bin/launchctl /usr/bin/curl /usr/bin/sqlite3 /usr/bin/csreq /usr/bin/xxd /usr/bin/defaults; do [ -x "$t" ] || die "missing $t"; done
  [ "${#SOCK}" -lt 104 ] || die "socket path too long"
  case "$HOME" in *[\&\<\>]*) die "unsupported characters in home path";; esac
}

fetch() {   # fetch <repo path> <expected sha256> <dest>
  curl -fsSL "$REPO/$REF/$1" -o "$3" || die "download failed: $1"
  echo "$2  $3" | shasum -a 256 -c - >/dev/null 2>&1 || die "hash mismatch: $1"
}

probe() {   # probe pre|post: talk to the socket as any client would; print only fixed checks and the aggregate state
  /usr/bin/python3 -I - "$SOCK" "$1" <<'PY'
import json, socket, sys
path, mode = sys.argv[1], sys.argv[2]
KEYS = {"channel", "attention", "count", "count_reliable", "health", "sync", "observed_at", "source"}

def ask(requests):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(15)
    s.connect(path)
    s.sendall(("\n".join(json.dumps(r) for r in requests) + "\n").encode())
    s.shutdown(socket.SHUT_WR)
    data = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 16384:
            raise SystemExit("REPLY_TOO_LARGE")
    s.close()
    return [json.loads(x) for x in data.decode().splitlines()], len(data)

def call(i, name, args):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}

out = []
def check(name, ok):
    out.append([name, "PASS" if ok else "FAIL"])

try:
    (reply,), size = ask([call(1, "imessage_attention", {})])
    r = reply.get("result") or {}
    st = r.get("structuredContent") or {}
    exact = (set(st) == KEYS and st["count"] is None and st["count_reliable"] is False and st["sync"] == "UNVERIFIED" and st["channel"] == "imessage"
             and st["source"] == "Apple Messages / Mac Mini" and st["attention"] in ("NEEDS_ATTENTION", "UNKNOWN") and st["health"] in ("CURRENT", "DEGRADED", "DISCONNECTED")
             and set(r) == {"content", "structuredContent", "isError"} and json.loads(r["content"][0]["text"]) == st and size < 1024)
    check("reply is exactly the aggregate contract (no content, identities or extra fields)", exact)
    if mode == "pre":
        check("without Full Disk Access the helper fails closed (DISCONNECTED / UNKNOWN, no count)", st.get("health") == "DISCONNECTED" and st.get("attention") == "UNKNOWN")
    else:
        check("with Full Disk Access the helper reads Messages (health CURRENT)", st.get("health") == "CURRENT")
    replies, _ = ask([call(2, "send", {}), call(3, "mark_read", {}), call(4, "imessage_attention", {"path": "/etc/hosts"}),
                      call(5, "imessage_attention", {"query": "SELECT text FROM message"}), {"jsonrpc": "2.0", "id": 6, "method": "resources/read"},
                      {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}])
    codes = {x["id"]: (x.get("error") or {}).get("code") for x in replies}
    check("send and mark_read are refused", codes.get(2) == codes.get(3) == -32602)
    check("caller paths and SQL are refused", codes.get(4) == codes.get(5) == -32602)
    check("unknown methods are refused", codes.get(6) == -32601)
    tools = [t["name"] for x in replies if x["id"] == 7 for t in x["result"]["tools"]]
    check("exactly the approved read-only tools are offered", tools == ["imessage_attention", "imessage_counts", "imessage_recent", "imessage_conversation", "imessage_page"])
    state = {k: st.get(k) for k in ("attention", "count", "count_reliable", "health", "sync", "observed_at")}
except Exception as e:
    check("helper reachable over the launchd socket", False)
    state = {"error": type(e).__name__}
print(json.dumps({"checks": out, "state": state}))
PY
}

run_probe() {   # run_probe pre|post
  local raw
  raw="$(probe "$1")" || raw='{"checks":[["probe ran","FAIL"]],"state":{}}'
  /usr/bin/python3 -I -c 'import json,sys; d=json.loads(sys.argv[1]); [print("%s\t%s" % (s, n)) for n, s in d["checks"]]' "$raw" | while IFS=$'\t' read -r s n; do say "  [$s] $n"; done
  STATE="$(/usr/bin/python3 -I -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["state"]))' "$raw")"
  say "  state: $STATE"
  CHECKS="$(/usr/bin/python3 -I -c 'import json,sys; print("\n".join("%s: %s" % (n, s) for n, s in json.loads(sys.argv[1])["checks"]))' "$raw")"
  while IFS= read -r line; do RESULTS+=("$1 $line"); done <<<"$CHECKS"
}

helper_cdhash() {   # the signed helper's code hash: codesign prints "CDHash=" only at verbosity 2 and above (plain -dv omits it)
  local out
  out="$(/usr/bin/codesign -d --verbose=4 "$BIN" 2>&1)" || return 0
  /usr/bin/awk -F= '$1 == "CDHash" && $2 ~ /^[0-9A-Fa-f]{40}$/ { print tolower($2); exit }' <<<"$out"
}

fda_check() {   # fda_check pre|post: reads the system privacy database (Terminal's existing grant allows it); prints only fixed check names, never the list
  local rows app_id cdhash req_hex req_text reqf
  rows="$(/usr/bin/sqlite3 -readonly "$TCCDB" "SELECT client || '|' || auth_value FROM access WHERE service = '$FDA'" 2>/dev/null)" \
    && record "Full Disk Access list readable" PASS \
    || { record "Full Disk Access list readable (run this from Terminal, which already has Full Disk Access)" UNVERIFIED; return 0; }
  app_id="$(/usr/bin/defaults read "$OPENCLAW_APP/Contents/Info" CFBundleIdentifier 2>/dev/null)" || app_id=""
  if [ -z "$app_id" ]; then
    record "OpenClaw.app has no Full Disk Access (its bundle identifier could not be read)" UNVERIFIED
  elif /usr/bin/awk -F'|' -v id="$app_id" -v app="$OPENCLAW_APP/" '($1 == id || index($1, app) == 1 || tolower($1) ~ /openclaw/) && $2 != 0 { bad = 1 } END { exit bad }' <<<"$rows"; then
    record "OpenClaw.app has no Full Disk Access" PASS
  else
    record "OpenClaw.app has no Full Disk Access" FAIL
  fi
  /usr/bin/awk -F'|' 'tolower($1) ~ /python/ && $2 != 0 { bad = 1 } END { exit bad }' <<<"$rows" \
    && record "Python has no Full Disk Access (any python entry would void the isolation proof)" PASS \
    || record "Python has no Full Disk Access (any python entry would void the isolation proof)" FAIL
  [ "$1" = post ] || return 0
  grep -qxF "$BIN|2" <<<"$rows" && record "Full Disk Access granted to $BIN" PASS || record "Full Disk Access granted to $BIN" FAIL
  cdhash="$(helper_cdhash)"
  req_hex="$(/usr/bin/sqlite3 -readonly "$TCCDB" "SELECT hex(csreq) FROM access WHERE service = '$FDA' AND client = '$BIN' AND client_type = 1" 2>/dev/null)" || req_hex=""
  reqf="$(mktemp)"
  req_text=""
  if [ -n "$req_hex" ] && printf '%s' "$req_hex" | /usr/bin/xxd -r -p > "$reqf"; then
    req_text="$(/usr/bin/csreq -r "$reqf" -t 2>/dev/null | tr 'A-F' 'a-f')" || req_text=""
  fi
  rm -f "$reqf"
  if [ -z "$cdhash" ] || [ -z "$req_text" ]; then
    record "the grant is bound to this helper build (macOS recorded no readable code requirement for it)" UNVERIFIED
  elif grep -qF "$cdhash" <<<"$req_text"; then
    record "the grant is bound to this helper build (code hash matches)" PASS
  else
    record "the grant is bound to this helper build (code requirement names another build)" FAIL
  fi
}

agent_exact() {   # parse the installed LaunchAgent and require exactly the approved keys and values: any extra key (RunAtLoad, KeepAlive, a timer, a network socket) fails
  /usr/bin/python3 -I - "$AGENT" "$LABEL" "$BIN" "$SOCK" <<'AGENT_PY'
import plistlib, sys
agent, label, program, sock = sys.argv[1:5]
try:
    with open(agent, "rb") as f:
        got = plistlib.load(f)
except Exception:
    sys.exit(1)
want = {"Label": label, "Program": program, "ProgramArguments": [program], "inetdCompatibility": {"Wait": False}, "ProcessType": "Background",
        "Sockets": {"Listener": {"SockType": "stream", "SockFamily": "Unix", "SockPathName": sock, "SockPathMode": 0o600}}}
same = got == want and got["inetdCompatibility"]["Wait"] is False and type(got["Sockets"]["Listener"]["SockPathMode"]) is int
sys.exit(0 if same else 1)
AGENT_PY
}

identity() {
  say "Helper identity:"
  /usr/bin/codesign -d --verbose=4 "$BIN" 2>&1 | grep -E '^(Identifier|Format|CodeDirectory|Signature|CDHash|Runtime|TeamIdentifier)' | sed 's/^/  /' || true
  /usr/bin/codesign --verify --strict "$BIN" && record "helper signature verifies" PASS || record "helper signature verifies" FAIL
  CDHASH="$(helper_cdhash)"
  [ -n "$CDHASH" ] && record "helper code hash read from its signature: $CDHASH" PASS || record "helper code hash read from its signature (none found)" FAIL
  [ "$(stat -f '%Su:%Sg %Lp' "$DIR")" = "root:wheel 755" ] && [ "$(stat -f '%Su:%Sg %Lp' "$BIN")" = "root:wheel 755" ] && [ "$(stat -f '%Su:%Sg %Lp' "$TOOL")" = "root:wheel 644" ] \
    && record "helper files are root-owned and not writable by Jim's user" PASS || record "helper files are root-owned and not writable by Jim's user" FAIL
  [ "$(stat -f '%Lp' "$SOCKDIR")" = "700" ] && record "socket folder is owner-only" PASS || record "socket folder is owner-only" FAIL
  /usr/bin/plutil -lint "$AGENT" >/dev/null && agent_exact \
    && record "LaunchAgent is on-demand, Unix socket only, no timer or network listener" PASS || record "LaunchAgent is on-demand, Unix socket only, no timer or network listener" FAIL
  /bin/launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 && record "LaunchAgent loaded in $DOMAIN" PASS || record "LaunchAgent loaded in $DOMAIN" FAIL
}

install_helper() {
  [ -n "${REF:-}" ] || die "usage: bash install.sh install <commit>"
  TMPD="$(mktemp -d)"; local tmp="$TMPD"
  say "1/5 Downloading the three reviewed source files at $REF (hash-checked)"
  fetch lifeos/imessage/attention_mcp.py "$SHA_TOOL" "$tmp/attention_mcp.py"
  fetch lifeos/imessage/mac/launcher.c "$SHA_LAUNCHER" "$tmp/launcher.c"
  fetch lifeos/imessage/mac/ai.lifeos.imessage-attention.plist "$SHA_PLIST" "$tmp/agent.plist"
  say "2/5 Building and ad-hoc signing the helper (identifier $LABEL, hardened runtime)"
  /usr/bin/cc -O2 -Wall -Werror -o "$tmp/lifeos-imessage-attention" "$tmp/launcher.c"
  /usr/bin/codesign --force --sign - --identifier "$LABEL" --options runtime "$tmp/lifeos-imessage-attention"
  say "3/5 Installing root-owned files into $DIR (macOS will ask for your password)"
  INSTALLED=1
  sudo /usr/bin/install -d -o root -g wheel -m 755 "$DIR"
  sudo /usr/bin/install -o root -g wheel -m 755 "$tmp/lifeos-imessage-attention" "$BIN"
  sudo /usr/bin/install -o root -g wheel -m 644 "$tmp/attention_mcp.py" "$TOOL"
  say "4/5 Loading the on-demand LaunchAgent (socket: $SOCK)"
  mkdir -p "$SOCKDIR" "$HOME/Library/LaunchAgents"; chmod 700 "$SOCKDIR"
  local tpl; tpl="$(cat "$tmp/agent.plist")"; printf '%s\n' "${tpl//__SOCKET__/$SOCK}" > "$AGENT"; chmod 644 "$AGENT"
  /bin/launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true   # an earlier attempt's agent is unloaded and replaced in place
  rm -f "$SOCK"
  /bin/launchctl bootstrap "$DOMAIN" "$AGENT"
  say "5/5 Checks before any privacy grant"
  identity
  say "Privacy grants before the helper's grant (booleans only):"
  fda_check pre
  say "Live test WITHOUT Full Disk Access:"
  run_probe pre
  gate pre 15
}

grant() {
  say ""
  say "ACTION FOR JIM - grant Full Disk Access to this one file only:"
  say "  $BIN"
  say "  System Settings > Privacy & Security > Full Disk Access is opening, and Finder is showing the file."
  say "  Click +, press Cmd+Shift+G, paste the path above, click Open, and make sure its switch is ON."
  say "  Do NOT add OpenClaw, python3 or anything else."
  /usr/bin/open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" || true
  /usr/bin/open -R "$BIN" || true
  read -r -p "Press Return here after the switch is ON... " _ </dev/tty
}

verify() {
  say "Privacy grants (system privacy database, booleans only):"
  fda_check post
  say "Live test WITH Full Disk Access (Messages database must be unchanged):"
  local before after
  before="$(/usr/bin/shasum -a 256 "$DB" 2>/dev/null | cut -d' ' -f1)" || before=""
  run_probe post
  after="$(/usr/bin/shasum -a 256 "$DB" 2>/dev/null | cut -d' ' -f1)" || after=""
  [ -n "$before" ] && [ "$before" = "$after" ] && record "Messages database byte-identical before and after the read" PASS \
    || record "Messages database byte-identical before and after the read (Messages itself may have written; rerun verify)" FAIL
}

summary() {   # summary <RESULT line>
  say ""
  say "=== COMM-1.4 LOCAL PROOF (paste this block back; it holds no message content or identities) ==="
  say "macOS $(sw_vers -productVersion) | helper CDHash $(helper_cdhash) | ref ${REF:-n/a}"
  for line in "${RESULTS[@]}"; do say "$line"; done
  say "final state: ${STATE:-none}"
  say "$1"
  say "==="
}

rollback_help() {   # rollback_help pre|post
  say "Roll back with:  bash \"$SELF\" uninstall"
  if [ "$1" = pre ]; then
    say "Do NOT grant Full Disk Access to the helper."
  else
    say "Then, if you granted it, revoke the helper's Full Disk Access by hand (this installer never changes privacy settings):"
    say "  System Settings > Privacy & Security > Full Disk Access, select lifeos-imessage-attention, click -."
  fi
}

gate() {   # gate <phase> <required PASS count>: anything but exactly that many PASS lines and no other status stops with a nonzero exit
  local all pass other
  all="$(printf '%s\n' "${RESULTS[@]}")"
  pass="$(grep -c ': PASS$' <<<"$all" || true)"
  other="$(grep -vc ': PASS$' <<<"$all" || true)"
  if [ "$other" -eq 0 ] && [ "$pass" -eq "$2" ]; then
    return 0
  fi
  REPORTED=1
  summary "RESULT: LOCAL PROOF FAILED ($1: $pass of $2 required checks passed; $other failed or unverified)"
  rollback_help "$1"
  exit 1
}

uninstall() {
  /bin/launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  rm -f "$AGENT" "$SOCK"
  rmdir "$SOCKDIR" 2>/dev/null || true
  sudo rm -rf "$DIR"
  say "Rolled back: LaunchAgent unloaded and removed, $DIR deleted, socket removed."
  say "Last step by hand: System Settings > Privacy & Security > Full Disk Access, select lifeos-imessage-attention, click -."
}

on_exit() {   # any stop after files were installed, other than a reported gate, still ends with the failure line and the rollback
  local rc=$?
  [ -z "${TMPD:-}" ] || rm -rf "$TMPD"
  if [ "$rc" -ne 0 ] && [ "$INSTALLED" -eq 1 ] && [ "$REPORTED" -eq 0 ]; then
    say "RESULT: LOCAL PROOF FAILED (an installer step stopped unexpectedly, exit $rc)"
    rollback_help post
  fi
}

INSTALLED=0
REPORTED=0
trap on_exit EXIT
preflight
case "${1:-}" in
  install) REF="${2:-}"; install_helper; grant; verify; gate post 27; summary "RESULT: LOCAL PROOF PASSED (27 of 27 required checks)" ;;
  verify) INSTALLED=1; identity; verify; gate post 18; summary "RESULT: LOCAL PROOF PASSED (18 of 18 required checks)" ;;
  uninstall) uninstall ;;
  *) die "usage: bash install.sh install <commit> | verify | uninstall" ;;
esac
