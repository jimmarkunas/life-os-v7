"""COMM-1.4b: the private, read-only iMessage alert-METADATA endpoint on Oracle (Jim's approval, LIFE-31, Oct 13 2026).

systemd starts one process per connection on 127.0.0.1 (socket activation, Accept=yes: the connection is stdin/stdout); Oracle's Tailscale Serve forwards one
tailnet TCP port to it with a PROXY protocol v2 header carrying the WireGuard-authenticated source. The process:
  0. requires the loopback peer to be tailscaled itself (the connecting local socket's owner, from /proc/net/tcp, must be root): a local non-root process
     cannot present a forged PROXY header, because it is refused before one byte is read;
  1. requires that header and asks tailscaled who the source is: only a node carrying TAG (the V7 hourly runner's ephemeral identity) is served;
  2. requires the request to be exactly "alerts <lookback hours 1-72>";
  3. reads ALL of the Mac Mini's incoming messages for that lookback through the existing wrapper path (read or unread alike), paging until the reader
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
MAX_REQUEST = 16
SCHEMA = "lifeos.imessage.alerts.v2"
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


def lookback(stream):
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
    if len(parts) != 2 or parts[0] != b"alerts" or not parts[1].isdigit() or parts[1][:1] == b"0":
        return None
    hours = int(parts[1])
    return hours if 1 <= hours <= 72 else None


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
    hours = lookback(stdin)
    if hours is None:
        return 1
    try:
        with open(key_path) as f:
            secret = bytes.fromhex(f.read().strip())
        if len(secret) < 32:
            return 1
        payload = scan(hours, load_wrapper(wrapper_path), secret, now)
    except Exception:
        return 1                                                   # no partial answer and no exception text: the caller sees a refused connection
    stdout.write((json.dumps(payload, sort_keys=True) + "\n").encode())
    stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
