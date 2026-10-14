#!/bin/bash
# COMM-1.4 local proof on the Mac Mini: install, prove and roll back the signed, launchd-started iMessage attention helper.
#   bash install.sh install <commit>   build + sign + install, test without Full Disk Access, wait for Jim's grant, test with it, print the proof summary
#   bash install.sh verify             rerun the live tests only
#   bash install.sh uninstall          full rollback (the Full Disk Access entry is removed by hand)
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
# sha256 of the three source files at the published commit (a test keeps these in step with the repository)
SHA_TOOL="25125791dbfc72b2e1111b5e21eb9141cba37d30a9090896392172c65bedd2b4"
SHA_LAUNCHER="cc275afab87d5f1d817055641ca04985bbe1c4e3ae2ad54f49a9ca8954b4005c"
SHA_PLIST="6e22bffc15943491fdf547a8e6e06cc55bc18c356e6fad159fd78036907cadd0"

say() { printf '%s\n' "$*"; }
die() { say "STOP: $*"; exit 1; }
RESULTS=()
record() { RESULTS+=("$1: $2"); say "  [$2] $1"; }

preflight() {
  [ "$(uname -s)" = "Darwin" ] || die "not macOS"
  [ "$(id -u)" -ne 0 ] || die "run as Jim's user, not root (sudo is asked for only to install root-owned files)"
  for t in /usr/bin/cc /usr/bin/codesign /usr/bin/python3 /usr/bin/shasum /usr/bin/plutil /bin/launchctl /usr/bin/curl; do [ -x "$t" ] || die "missing $t"; done
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
    check("exactly one tool is offered", tools == ["imessage_attention"])
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

fda_grants() {   # only booleans about three recipients; Terminal's existing grant is what lets this read the system privacy database
  local rows
  rows="$(/usr/bin/sqlite3 -readonly "$TCCDB" "SELECT client || '|' || auth_value FROM access WHERE service = 'kTCCServiceSystemPolicyAllFiles'" 2>/dev/null)" || { record "Full Disk Access list readable" "UNVERIFIED"; return; }
  grep -qx "$BIN|2" <<<"$rows" && record "Full Disk Access granted to exactly $BIN" PASS || record "Full Disk Access granted to exactly $BIN" FAIL
  grep -qiE '^[^|]*openclaw[^|]*\|2$' <<<"$rows" && record "OpenClaw.app has no Full Disk Access" FAIL || record "OpenClaw.app has no Full Disk Access" PASS
  grep -qiE '^(/usr/bin/python3|com\.apple\.python3|[^|]*/python3[^|/]*)\|2$' <<<"$rows" && record "python3 has no Full Disk Access" FAIL || record "python3 has no Full Disk Access" PASS
}

identity() {
  say "Helper identity:"
  /usr/bin/codesign -dv --verbose=2 "$BIN" 2>&1 | grep -E '^(Identifier|Format|CodeDirectory|Signature|CDHash|Runtime|TeamIdentifier)' | sed 's/^/  /' || true
  /usr/bin/codesign --verify --strict "$BIN" && record "helper signature verifies" PASS || record "helper signature verifies" FAIL
  [ "$(stat -f '%Su:%Sg %Lp' "$DIR")" = "root:wheel 755" ] && [ "$(stat -f '%Su:%Sg %Lp' "$BIN")" = "root:wheel 755" ] && [ "$(stat -f '%Su:%Sg %Lp' "$TOOL")" = "root:wheel 644" ] \
    && record "helper files are root-owned and not writable by Jim's user" PASS || record "helper files are root-owned and not writable by Jim's user" FAIL
  [ "$(stat -f '%Lp' "$SOCKDIR")" = "700" ] && record "socket folder is owner-only" PASS || record "socket folder is owner-only" FAIL
  /usr/bin/plutil -lint "$AGENT" >/dev/null && ! grep -qE 'RunAtLoad|KeepAlive|StartInterval|StartCalendarInterval|SockNodeName|SockServiceName' "$AGENT" \
    && record "LaunchAgent is on-demand, Unix socket only, no timer or network listener" PASS || record "LaunchAgent is on-demand, Unix socket only, no timer or network listener" FAIL
  /bin/launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 && record "LaunchAgent loaded in $DOMAIN" PASS || record "LaunchAgent loaded in $DOMAIN" FAIL
}

install_helper() {
  [ -n "${REF:-}" ] || die "usage: bash install.sh install <commit>"
  TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT; local tmp="$TMPD"
  say "1/5 Downloading the three reviewed source files at $REF (hash-checked)"
  fetch lifeos/imessage/attention_mcp.py "$SHA_TOOL" "$tmp/attention_mcp.py"
  fetch lifeos/imessage/mac/launcher.c "$SHA_LAUNCHER" "$tmp/launcher.c"
  fetch lifeos/imessage/mac/ai.lifeos.imessage-attention.plist "$SHA_PLIST" "$tmp/agent.plist"
  say "2/5 Building and ad-hoc signing the helper (identifier $LABEL, hardened runtime)"
  /usr/bin/cc -O2 -Wall -Werror -o "$tmp/lifeos-imessage-attention" "$tmp/launcher.c"
  /usr/bin/codesign --force --sign - --identifier "$LABEL" --options runtime "$tmp/lifeos-imessage-attention"
  say "3/5 Installing root-owned files into $DIR (macOS will ask for your password)"
  sudo /usr/bin/install -d -o root -g wheel -m 755 "$DIR"
  sudo /usr/bin/install -o root -g wheel -m 755 "$tmp/lifeos-imessage-attention" "$BIN"
  sudo /usr/bin/install -o root -g wheel -m 644 "$tmp/attention_mcp.py" "$TOOL"
  say "4/5 Loading the on-demand LaunchAgent (socket: $SOCK)"
  mkdir -p "$SOCKDIR" "$HOME/Library/LaunchAgents"; chmod 700 "$SOCKDIR"
  local tpl; tpl="$(cat "$tmp/agent.plist")"; printf '%s\n' "${tpl//__SOCKET__/$SOCK}" > "$AGENT"; chmod 644 "$AGENT"
  /bin/launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  /bin/launchctl bootstrap "$DOMAIN" "$AGENT"
  say "5/5 Checks before any privacy grant"
  identity
  say "Live test WITHOUT Full Disk Access:"
  run_probe pre
  if grep -q ': FAIL$' <<<"$(printf '%s\n' "${RESULTS[@]}")"; then
    summary
    die "a check failed before any privacy grant (if the helper read Messages without its own grant, isolation failed). Do not grant Full Disk Access; run: bash install.sh uninstall"
  fi
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
  fda_grants
  say "Live test WITH Full Disk Access (Messages database must be unchanged):"
  local before after
  before="$(/usr/bin/shasum -a 256 "$DB" 2>/dev/null | cut -d' ' -f1)" || before=""
  run_probe post
  after="$(/usr/bin/shasum -a 256 "$DB" 2>/dev/null | cut -d' ' -f1)" || after=""
  [ -n "$before" ] && [ "$before" = "$after" ] && record "Messages database byte-identical before and after the read" PASS \
    || record "Messages database byte-identical before and after the read (Messages itself may have written; rerun verify)" FAIL
}

summary() {
  say ""
  say "=== COMM-1.4 LOCAL PROOF (paste this block back; it holds no message content or identities) ==="
  say "macOS $(sw_vers -productVersion) | helper $(/usr/bin/codesign -dv "$BIN" 2>&1 | sed -n 's/^CDHash=//p') | ref ${REF:-n/a}"
  for line in "${RESULTS[@]}"; do say "$line"; done
  say "final state: ${STATE:-none}"
  say "==="
}

uninstall() {
  /bin/launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  rm -f "$AGENT" "$SOCK"
  rmdir "$SOCKDIR" 2>/dev/null || true
  sudo rm -rf "$DIR"
  say "Rolled back: LaunchAgent unloaded and removed, $DIR deleted, socket removed."
  say "Last step by hand: System Settings > Privacy & Security > Full Disk Access, select lifeos-imessage-attention, click -."
}

preflight
case "${1:-}" in
  install) REF="${2:-}"; install_helper; grant; verify; summary ;;
  verify) identity; verify; summary ;;
  uninstall) uninstall ;;
  *) die "usage: bash install.sh install <commit> | verify | uninstall" ;;
esac
