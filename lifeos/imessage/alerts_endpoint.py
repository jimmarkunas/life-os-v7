"""COMM-1.4b: the private, read-only iMessage alert-METADATA endpoint on Oracle (Jim's approval, LIFE-31, Oct 13 2026).

systemd starts one process per connection on 127.0.0.1 (socket activation, Accept=yes: the connection is stdin/stdout); Oracle's Tailscale Serve forwards one
tailnet TCP port to it with a PROXY protocol v2 header carrying the WireGuard-authenticated source. The process:
  0. requires the loopback peer to be tailscaled itself (the connecting local socket's owner, from /proc/net/tcp, must be root): a local non-root process
     cannot present a forged PROXY header, because it is refused before one byte is read;
  1. requires that header and asks tailscaled who the source is: only a node carrying TAG (the V7 hourly runner's ephemeral identity) is served;
  2. requires the request to be exactly "alerts <lookback hours 1-72>" or "comms <64 lowercase hex: a one-run key the runner just generated>";
  comms2 (LIFE-29/LIFE-34, Jim's approval Oct 15 2026, Jira 10203/10206): the comms answer plus a `telegram` block from the local TDLib attention
     adapter (~/.local/lib/lifeos-telegram/attention.py): Main-list unread messages/chats, total and unmuted, health and auth state, and the newest
     incoming sender's name scrambled under the same one-run key; the adapter's plaintext name never leaves this process;
  comms (LIFE-34, Jim's approval Oct 15 2026): answers the reader's unread count for the last 7 days and the display name of the newest incoming
     sender, the name SCRAMBLED under the runner's one-run key (HMAC-SHA256 keystream, encrypt-then-MAC), so it is never plain text anywhere between the
     Mac and the runner's in-memory Notion write. Never a handle, number, text or reference; nothing is kept, so no key exists after the run;
  3. alerts: reads ALL of the Mac Mini's incoming messages for that lookback through the existing wrapper path (read or unread alike), paging until the reader
     proves the end, classifies them in memory with the wrapper's deterministic rules and answers ONE JSON line of metadata: keyed event ids (HMAC of the
     source reference under a key that never leaves Oracle), severity, reason codes, review flag, observation time, pages and completeness. Never text,
     names, handles, chat or message references.
Anything else closes with no output. It writes nothing, logs nothing and never runs inside a Luna/OpenClaw session. A scan that reached the reader's 50-message
or time cap (or any page failure) cannot prove it saw everything, so it says so (complete false, health DEGRADED) instead of passing as complete."""
import hashlib
import hmac
import importlib.machinery
import importlib.util
import ipaddress
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone

TAG = "tag:v7-runner"
WRAPPER = os.path.expanduser("~/.local/bin/openclaw-imessage")
EVENT_KEY = os.path.expanduser("~/.config/openclaw-imessage/event-key")       # 64 hex characters, 0600, generated on Oracle, never copied anywhere
TAILSCALE = "/usr/bin/tailscale"
SIG = b"\r\n\r\n\x00\r\nQUIT\n"
MAX_PROXY_TAIL = 216
MAX_REQUEST = 72
SCHEMA = "lifeos.imessage.alerts.v2"
COMMS_SCHEMA = "lifeos.imessage.comms.v1"
COMMS2_SCHEMA = "lifeos.imessage.comms.v2"
TELEGRAM = os.path.expanduser("~/.local/lib/lifeos-telegram/attention.py")
TELEGRAM_TIMEOUT = 60
TG_COUNTS = ("chats_main", "unread_messages", "unread_messages_unmuted", "unread_chats", "unread_chats_unmuted")
MAX_NAME = 80
HEX = b"0123456789abcdef"
MAX_EVENTS = 500
PUSHED = ("ATTENTION", "ACTION_REQUIRED", "TIME_CRITICAL")


def _hex(host, port):
    return "%08X:%04X" % (int.from_bytes(ipaddress.IPv4Address(host).packed, "little"), port)


def peer_uid(table, peer, local):
    """The uid owning the local socket at the other end of this loopback connection: its row has local = our peer and remote = our listener."""
    want = (_hex(*peer), _hex(*local))
    for line in table.splitlines()[1:]:
        fields = line.split()
        if len(fields) > 7 and (fields[1], fields[2]) == want:
            return int(fields[7])
    return None


def loopback_peer_uid(fd=0, tcp="/proc/net/tcp"):
    s = socket.socket(fileno=os.dup(fd))
    try:
        peer, local = s.getpeername()[:2], s.getsockname()[:2]
    finally:
        s.close()
    if peer[0] != "127.0.0.1" or local[0] != "127.0.0.1":
        return None
    with open(tcp) as f:
        return peer_uid(f.read(), peer, local)


def read_exact(stream, n):
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def proxy_source(stream):
    """The source address from a PROXY v2 PROXY-command header for TCP over IPv4/IPv6, or None."""
    head = read_exact(stream, 16)
    if not head or head[:12] != SIG or head[12] != 0x21:
        return None
    size = int.from_bytes(head[14:16], "big")
    tail = read_exact(stream, size) if size <= MAX_PROXY_TAIL else None
    if tail is None:
        return None
    if head[13] == 0x11 and size >= 12:
        return str(ipaddress.IPv4Address(tail[:4]))
    if head[13] == 0x21 and size >= 36:
        return str(ipaddress.IPv6Address(tail[:16]))
    return None


def whois(ip):
    run = subprocess.run([TAILSCALE, "whois", "--json", ip], capture_output=True, timeout=5)
    return json.loads(run.stdout) if run.returncode == 0 else None


def authorized(ip, lookup=whois):
    """Only a tailnet node that carries TAG. Tags are set by the tailnet's policy owner, never by the node itself, so an untagged device (Jim's own Macs, phone,
    Oracle itself) is refused even though the tailnet lets it reach the port."""
    try:
        node = (lookup(ip) or {}).get("Node") or {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return TAG in (node.get("Tags") or [])


def request(stream):
    """-> ("alerts", hours) | ("comms", 32-byte key) | None."""
    raw = b""
    while len(raw) < MAX_REQUEST:
        c = stream.read(1)
        if not c:
            return None
        if c == b"\n":
            break
        raw += c
    else:
        return None
    parts = raw.split(b" ")
    if len(parts) != 2:
        return None
    if parts[0] in (b"comms", b"comms2") and len(parts[1]) == 64 and all(c in HEX for c in parts[1]):
        return parts[0].decode(), bytes.fromhex(parts[1].decode())
    if parts[0] != b"alerts" or not parts[1].isdigit() or parts[1][:1] == b"0":
        return None
    hours = int(parts[1])
    return ("alerts", hours) if 1 <= hours <= 72 else None


def lookback(stream):
    got = request(stream)
    return got[1] if got and got[0] == "alerts" else None


def _keystream(key, nonce, n):
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(key, b"lifeos-comms-ks" + nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        i += 1
    return out[:n]


def scramble(key, text, nonce=None):
    """Encrypt-then-MAC with two HMAC-SHA256 domains of the runner's one-run key (stdlib only)."""
    nonce = nonce or os.urandom(16)
    data = text.encode("utf-8")
    box = bytes(a ^ b for a, b in zip(data, _keystream(key, nonce, len(data))))
    tag = hmac.new(key, b"lifeos-comms-tag" + nonce + box, hashlib.sha256).digest()[:16]
    return {"nonce": nonce.hex(), "box": box.hex(), "tag": tag.hex()}


def comms(wrapper, key, now=None):
    """The 7-day unread count and the newest incoming sender's name (scrambled). A source that is not CURRENT gives null, never a guess or a zero."""
    now = now or datetime.now(timezone.utc)
    counts = wrapper.run(["counts"], now) or {}
    recent = wrapper.run(["recent", "--hours", "72"], now) or {}
    unread = None
    if counts.get("health") == "CURRENT" and isinstance(counts.get("counts"), dict):
        unread = counts["counts"]["unread"]["last_7_days"]["messages"]
    state, latest = "UNAVAILABLE", None
    if recent.get("health") == "CURRENT" and isinstance(recent.get("messages"), list):
        incoming = [m for m in recent["messages"] if not m["from_me"]]
        state = "NONE" if not incoming else "PRESENT"
        if incoming:
            newest = max(incoming, key=lambda m: m["sent_at"])
            name = " ".join(((newest["sender"] or {}).get("name") or "").split())[:MAX_NAME] or "Unknown contact"
            latest = scramble(key, name)
    health = "CURRENT" if unread is not None and state != "UNAVAILABLE" else "DEGRADED"
    return {"schema": COMMS_SCHEMA, "observed_at": counts.get("observed_at") or recent.get("observed_at"), "health": health,
            "unread_7d": unread, "latest_state": state, "latest": latest}


def load_wrapper(path=WRAPPER):
    loader = importlib.machinery.SourceFileLoader("openclaw_imessage", path)
    spec = importlib.util.spec_from_loader("openclaw_imessage", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def event_key(secret, ref):
    return hmac.new(secret, b"mac-mini/" + ref.encode(), hashlib.sha256).hexdigest()[:32]


def scan(hours, wrapper, secret, now=None):
    now = now or datetime.now(timezone.utc)
    got = wrapper.scan(hours, now)
    limit = wrapper.MAX_PAGES * wrapper.PAGE_SIZE
    events = []
    for m, verdict in wrapper.verdicts(got["messages"]):
        if verdict["level"] in PUSHED:
            events.append({"key": event_key(secret, m["ref"]), "severity": verdict["level"], "reasons": verdict["reasons"], "review": verdict["review"]})
    overflow = len(events) > MAX_EVENTS
    complete = got["complete"] and not overflow
    health = got["health"] if got["health"] in ("CURRENT", "DEGRADED", "DISCONNECTED", "STALE") else "DEGRADED"
    if not complete and health == "CURRENT":
        health = "DEGRADED"
    return {"schema": SCHEMA, "observed_at": got["observed_at"], "lookback_hours": hours, "limit": limit, "pages": got["pages"], "sync": "UNVERIFIED",
            "events": events[:MAX_EVENTS], "health": health, "complete": complete, "scanned": len(got["messages"]),
            "limit_reached": got["truncated"] or overflow}


def telegram(key, run=subprocess.run):
    """The TDLib adapter's one observation, re-shaped: counts and states pass through, the name only scrambled. A missing, slow or malformed adapter is
    UNAVAILABLE with null counts, never a zero."""
    blank = {"health": "UNAVAILABLE", "auth": "ADAPTER_UNAVAILABLE", "error": None, "observed_at": None, "latest_state": "UNAVAILABLE", "latest": None,
             **{k: None for k in TG_COUNTS}}
    try:
        done = run(["/usr/bin/python3", "-I", TELEGRAM, "attention"], capture_output=True, text=True, timeout=TELEGRAM_TIMEOUT)
        got = json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        got = None
    if not isinstance(got, dict) or got.get("channel") != "telegram" or got.get("health") not in ("CURRENT", "DEGRADED", "UNAVAILABLE"):
        return blank
    current = got["health"] == "CURRENT"
    counts = {k: got.get(k) if current and type(got.get(k)) is int and got.get(k) >= 0 else None for k in TG_COUNTS}
    name = got.get("latest_name") if current and got.get("latest_state") == "PRESENT" and isinstance(got.get("latest_name"), str) else None
    state = got.get("latest_state") if current and got.get("latest_state") in ("PRESENT", "NONE") else "UNAVAILABLE"
    if state == "PRESENT" and not name:
        state = "UNAVAILABLE"
    return {**blank, **counts, "health": got["health"], "auth": str(got.get("auth") or "")[:40] or None, "error": str(got.get("error") or "")[:40] or None,
            "observed_at": got.get("observed_at"), "latest_state": state, "latest": scramble(key, name[:MAX_NAME]) if name else None}


def main(stdin=None, stdout=None, lookup=whois, wrapper_path=WRAPPER, key_path=EVENT_KEY, now=None, uid=loopback_peer_uid):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    try:
        if uid() != 0:
            return 1                                               # not tailscaled: a local process, refused before any header is read
    except (OSError, ValueError):
        return 1
    ip = proxy_source(stdin)
    if ip is None or not authorized(ip, lookup):
        return 1
    asked = request(stdin)
    if asked is None:
        return 1
    kind, arg = asked
    try:
        if kind in ("comms", "comms2"):
            payload = comms(load_wrapper(wrapper_path), arg, now)
            if kind == "comms2":
                payload = {**payload, "schema": COMMS2_SCHEMA, "telegram": telegram(arg)}
        else:
            with open(key_path) as f:
                secret = bytes.fromhex(f.read().strip())
            if len(secret) < 32:
                return 1
            payload = scan(arg, load_wrapper(wrapper_path), secret, now)
    except Exception:
        return 1                                                   # no partial answer and no exception text: the caller sees a refused connection
    stdout.write((json.dumps(payload, sort_keys=True) + "\n").encode())
    stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
