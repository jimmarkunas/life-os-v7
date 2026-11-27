"""The private, read-only iMessage alert-metadata endpoint on Oracle: one process per connection on 127.0.0.1
(systemd socket activation, Accept=yes; the connection is stdin/stdout).

Tailscale Serve forwards one tailnet port to it with a PROXY protocol v2 header. The process requires the loopback
peer to be tailscaled itself (a local process cannot forge the header), asks tailscaled who the source is (only a node
carrying TAG, the V7 hourly runner, is served), and requires the request to be one of these lines; anything else
closes with no output:
  alerts <hours 1-72>: ALL incoming messages of the lookback, classified in memory, answered as ONE JSON line of
     metadata (keyed event ids, severity, reason codes, review flag, time, pages, completeness); never text, names,
     handles or references. A scan that hit the reader's caps or any page failure says so (complete false, DEGRADED).
  comms <64 lowercase hex, a one-run key>: the 7-day unread count and the newest sender's display name SCRAMBLED under
     that key (HMAC-SHA256 keystream, encrypt-then-MAC), plus `telegram` and `whatsapp` blocks of the same shape.
  oracle1: the Oracle Cloud card's numbers from ~/.local/lib/lifeos-oracle/stats.mjs (one JSON object).
  drive1 <1..14>, gran1 <1..4096> + newline + that many bytes, concord1: forwarded unchanged to the read-only helper
     over its root-installed Unix socket under /run/lifeos-*; the one reply is passed back unread.
It writes nothing, logs nothing and never runs inside a Luna/OpenClaw session."""
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
EVENT_KEY = os.path.expanduser("~/.config/openclaw-imessage/event-key")
TAILSCALE = "/usr/bin/tailscale"
SIG = b"\r\n\r\n\x00\r\nQUIT\n"
MAX_PROXY_TAIL = 216
MAX_REQUEST = 72
GRAN_SOCKET = "/run/lifeos-gran/gran.sock"
GRAN_MAX_FRAME = 4096
GRAN_MAX_REPLY = 25_004_096
GRAN_WAIT = 82
DRIVE_SOCKET = "/run/lifeos-drive/drive.sock"
DRIVE_WAIT, DRIVE_MAX_REPLY = 60, 4096
CONCORD_SOCKET = "/run/lifeos-concord/concord.sock"
CONCORD_WAIT, CONCORD_MAX_REPLY = 40, 1024
SCHEMA = "lifeos.imessage.alerts.v2"
COMMS_SCHEMA = "lifeos.imessage.comms.v1"
COMMS2_SCHEMA = "lifeos.imessage.comms.v2"
COMMS3_SCHEMA = "lifeos.imessage.comms.v3"
WHATSAPP_SOCKET_WA = "/run/lifeos-wa/attention.sock"
WA_HEALTH = ("CURRENT", "DEGRADED", "DISCONNECTED", "NOT_LINKED", "UNAVAILABLE")
TELEGRAM = os.path.expanduser("~/.local/lib/lifeos-telegram/attention.py")
TELEGRAM_TIMEOUT = 60
TG_COUNTS = ("chats_main", "unread_messages", "unread_messages_unmuted", "unread_chats", "unread_chats_unmuted")
NODE = os.path.expanduser("~/.openclaw/tools/node/bin/node")
ORACLE_STATS = os.path.expanduser("~/.local/lib/lifeos-oracle/stats.mjs")
ORACLE_SCHEMA = "lifeos.oracle.stats.v1"
ORACLE_TIMEOUT = 70
MAX_NAME = 80
HEX = b"0123456789abcdef"
MAX_EVENTS = 500
PUSHED = ("ATTENTION", "ACTION_REQUIRED", "TIME_CRITICAL")


def _hex(host, port):
    return "%08X:%04X" % (int.from_bytes(ipaddress.IPv4Address(host).packed, "little"), port)


def peer_uid(table, peer, local):
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
    """Only a tailnet node that carries TAG. Tags are set by the tailnet's policy owner, never by the node, so an
    untagged device (any Mac, phone, Oracle itself) is refused even though the tailnet lets it reach the port.
    """
    try:
        node = (lookup(ip) or {}).get("Node") or {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return TAG in (node.get("Tags") or [])


def request(stream):
    """-> ("alerts", hours) | ("comms", 32-byte key) | ("oracle1", None) | ("gran1", the whole frame) | ("drive1", the
    request line) | ("concord1", the fixed line) | None.
    """
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
    if raw == b"oracle1":
        return "oracle1", None
    if raw.startswith(b"gran1 "):
        size = raw[6:]
        if not size.isdigit() or size[:1] == b"0" or not 1 <= int(size) <= GRAN_MAX_FRAME:
            return None
        body = read_exact(stream, int(size))
        return ("gran1", raw + b"\n" + body) if body is not None else None
    if raw == b"concord1":
        return "concord1", b"concord1\n"
    if raw.startswith(b"drive1 "):
        days = raw[7:]
        return ("drive1", raw + b"\n") if days.isdigit() and days[:1] != b"0" and 1 <= int(days) <= 14 else None
    parts = raw.split(b" ")
    if len(parts) != 2:
        return None
    if parts[0] in (b"comms", b"comms2", b"comms3") and len(parts[1]) == 64 and all(c in HEX for c in parts[1]):
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
    nonce = nonce or os.urandom(16)
    data = text.encode("utf-8")
    box = bytes(a ^ b for a, b in zip(data, _keystream(key, nonce, len(data))))
    tag = hmac.new(key, b"lifeos-comms-tag" + nonce + box, hashlib.sha256).digest()[:16]
    return {"nonce": nonce.hex(), "box": box.hex(), "tag": tag.hex()}


def comms(wrapper, key, now=None):
    """The 7-day unread count and the newest incoming sender's name (scrambled). A source that is not CURRENT gives
    null.
    """
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
    """The TDLib adapter's one observation, re-shaped: counts and states pass through, the name only scrambled. A
    missing, slow or malformed adapter is UNAVAILABLE with null counts, never a zero.
    """
    try:
        done = run(["/usr/bin/python3", "-I", TELEGRAM, "attention"], capture_output=True, text=True, timeout=TELEGRAM_TIMEOUT)
        got = json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        got = None
    healths = ("CURRENT", "DEGRADED", "UNAVAILABLE")
    got = got if isinstance(got, dict) and got.get("channel") == "telegram" else None
    block = _attention_block(got, key, healths)
    return {**block, "auth": None} if got and got.get("health") in healths and not got.get("auth") else block


def _attention_block(got, key, healths):
    blank = {"health": "UNAVAILABLE", "auth": "ADAPTER_UNAVAILABLE", "error": None, "observed_at": None, "latest_state": "UNAVAILABLE", "latest": None,
             **{k: None for k in TG_COUNTS}}
    if not isinstance(got, dict) or got.get("health") not in healths:
        return blank
    current = got["health"] == "CURRENT"
    counts = {k: got.get(k) if current and type(got.get(k)) is int and got.get(k) >= 0 else None for k in TG_COUNTS}
    name = got.get("latest_name") if current and got.get("latest_state") == "PRESENT" and isinstance(got.get("latest_name"), str) else None
    state = got.get("latest_state") if current and got.get("latest_state") in ("PRESENT", "NONE") else "UNAVAILABLE"
    if state == "PRESENT" and not name:
        state = "UNAVAILABLE"
    return {**blank, **counts, "health": got["health"] if got["health"] in ("CURRENT", "DEGRADED", "UNAVAILABLE") else "UNAVAILABLE",
            "auth": str(got.get("auth") or got["health"])[:40], "error": str(got.get("error") or "")[:40] or None,
            "observed_at": got.get("observed_at"), "latest_state": state, "latest": scramble(key, name[:MAX_NAME]) if name else None}


def _wa_snapshot(path):
    data = b""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(path)
            while len(data) <= 4096:
                chunk = s.recv(4097 - len(data))
                if not chunk:
                    break
                data += chunk
        return json.loads(data) if len(data) <= 4096 else None
    except (OSError, ValueError):
        return None


def whatsapp(key, path=None):
    got = _wa_snapshot(path or WHATSAPP_SOCKET_WA)
    if isinstance(got, dict) and got.get("channel") != "whatsapp":
        got = None
    if isinstance(got, dict) and "chats_known" in got:
        got = {**got, "chats_main": got["chats_known"]}
    if isinstance(got, dict) and got.get("health") in ("DISCONNECTED", "NOT_LINKED"):
        got = {**got, "auth": got["health"], "health": "UNAVAILABLE"}
    return _attention_block(got, key, WA_HEALTH)


def oracle_stats(run=subprocess.run):
    done = run([NODE, ORACLE_STATS], capture_output=True, text=True, timeout=ORACLE_TIMEOUT)
    got = json.loads(done.stdout) if done.returncode == 0 else None
    if not isinstance(got, dict) or got.get("schema") != ORACLE_SCHEMA:
        raise ValueError("ORACLE_STATS_OFF_CONTRACT")
    return got


def gran1(frame, path=GRAN_SOCKET, wait=GRAN_WAIT):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(wait)
        s.connect(path)
        s.sendall(frame)
        s.shutdown(socket.SHUT_WR)
        data = b""
        while len(data) <= GRAN_MAX_REPLY:
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            data += chunk
    if len(data) > GRAN_MAX_REPLY or not data.startswith(b"gran1 "):
        raise ValueError("reply is not a gran1 frame")
    return data


def drive1(frame, path=DRIVE_SOCKET, wait=DRIVE_WAIT):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(wait)
        s.connect(path)
        s.sendall(frame)
        s.shutdown(socket.SHUT_WR)
        data = b""
        while len(data) <= DRIVE_MAX_REPLY:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    if len(data) > DRIVE_MAX_REPLY or not data.startswith(b"{") or not data.endswith(b"\n"):
        raise ValueError("reply is not one JSON line")
    return data


def concord1(frame, path=CONCORD_SOCKET, wait=CONCORD_WAIT):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(wait)
        s.connect(path)
        s.sendall(frame)
        s.shutdown(socket.SHUT_WR)
        data = b""
        while len(data) <= CONCORD_MAX_REPLY:
            chunk = s.recv(1024)
            if not chunk:
                break
            data += chunk
    if len(data) > CONCORD_MAX_REPLY or not data.startswith(b"{") or not data.endswith(b"\n"):
        raise ValueError("reply is not one JSON line")
    return data


def main(stdin=None, stdout=None, lookup=whois, wrapper_path=WRAPPER, key_path=EVENT_KEY, now=None, uid=loopback_peer_uid, stats=oracle_stats, gran=gran1, drive=drive1, concord=concord1):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    try:
        if uid() != 0:
            return 1
    except (OSError, ValueError):
        return 1
    ip = proxy_source(stdin)
    if ip is None or not authorized(ip, lookup):
        return 1
    asked = request(stdin)
    if asked is None:
        return 1
    kind, arg = asked
    if kind == "drive1":
        try:
            reply = drive(arg)
        except Exception:
            return 1
        stdout.write(reply)
        stdout.flush()
        return 0
    if kind == "concord1":
        try:
            reply = concord(arg)
        except Exception:
            return 1
        stdout.write(reply)
        stdout.flush()
        return 0
    if kind == "gran1":
        try:
            reply = gran(arg)
        except Exception:
            return 1
        stdout.write(reply)
        stdout.flush()
        return 0
    try:
        if kind == "oracle1":
            payload = stats()
        elif kind in ("comms", "comms2", "comms3"):
            payload = comms(load_wrapper(wrapper_path), arg, now)
            if kind in ("comms2", "comms3"):
                payload = {**payload, "schema": COMMS2_SCHEMA, "telegram": telegram(arg)}
            if kind == "comms3":
                payload = {**payload, "schema": COMMS3_SCHEMA, "whatsapp": whatsapp(arg)}
        else:
            with open(key_path) as f:
                secret = bytes.fromhex(f.read().strip())
            if len(secret) < 32:
                return 1
            payload = scan(arg, load_wrapper(wrapper_path), secret, now)
    except Exception:
        return 1
    stdout.write((json.dumps(payload, sort_keys=True) + "\n").encode())
    stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
