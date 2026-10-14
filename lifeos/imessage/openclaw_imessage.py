#!/usr/bin/python3 -I
"""COMM-1.4: Luna's one iMessage command on Oracle, installed as ~/.local/bin/openclaw-imessage and allowlisted for exactly `openclaw-imessage attention`.

It sends the fixed request over the private tailnet to the Mac Mini's Tailscale Serve port (Serve adds the authenticated source; the Mac relay accepts only
Oracle) and prints one validated aggregate: the exact eight fields of the Messages attention contract. A reply that is late, oversized, malformed, stale or
off-contract is never passed on: the result is a fixed UNKNOWN state with its reason in `health`, never a count, a zero or exception text.
The endpoint (tailnet host and port) is read from a private local file, never from arguments or Git. Stdlib only; writes nothing; no stderr."""
import json
import os
import socket
import sys
from datetime import datetime, timedelta, timezone

ENDPOINT = os.path.expanduser("~/.config/openclaw-imessage/endpoint")      # one line: "<tailnet host> <port>"
REQUEST = b"attention\n"
SOURCE = "Apple Messages / Mac Mini"
KEYS = {"channel", "attention", "count", "count_reliable", "health", "sync", "observed_at", "source"}
MAX_REPLY = 2048
TIMEOUT = 10
MAX_AGE = timedelta(seconds=120)                                          # the helper reads at request time; anything older is a stale or replayed answer
MAX_SKEW = timedelta(seconds=60)


def unavailable(health, now):
    return {"channel": "imessage", "attention": "UNKNOWN", "count": None, "count_reliable": False, "health": health, "sync": "UNVERIFIED",
            "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "source": SOURCE}


def endpoint(path=ENDPOINT):
    with open(path) as f:
        host, port = f.read().split()
    port = int(port)
    if not 0 < port < 65536:
        raise ValueError
    return host, port


def fetch(host, port):
    """One connection: send the fixed request, read at most MAX_REPLY + 1 bytes. OSError means the Mac is unreachable or silent."""
    with socket.create_connection((host, port), timeout=TIMEOUT) as s:
        s.settimeout(TIMEOUT)
        s.sendall(REQUEST)
        s.shutdown(socket.SHUT_WR)
        data = b""
        while len(data) <= MAX_REPLY:
            chunk = s.recv(MAX_REPLY + 1 - len(data))
            if not chunk:
                break
            data += chunk
    return data


def _when(text):
    if not isinstance(text, str) or not text.endswith("Z") or len(text) != 20:
        raise ValueError
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def validate(data, now):
    """The relay's single JSON-RPC reply line -> the exact aggregate, or (health) of why it is refused."""
    if not data:
        return unavailable("DISCONNECTED", now)                         # refused or nothing answered: same as offline
    if len(data) > MAX_REPLY or not data.endswith(b"\n") or data.count(b"\n") != 1:
        return unavailable("DEGRADED", now)
    try:
        reply = json.loads(data)
        result = reply["result"]
        state = result["structuredContent"]
        ok = (set(reply) == {"jsonrpc", "id", "result"} and reply["jsonrpc"] == "2.0" and reply["id"] == 1
              and set(result) == {"content", "structuredContent", "isError"} and isinstance(state, dict) and set(state) == KEYS
              and result["content"] == [{"type": "text", "text": json.dumps(state)}] and result["isError"] is (state["health"] != "CURRENT")
              and state["channel"] == "imessage" and state["source"] == SOURCE and state["count"] is None and state["count_reliable"] is False
              and state["sync"] == "UNVERIFIED" and state["health"] in ("CURRENT", "DEGRADED", "DISCONNECTED")
              and state["attention"] in (("NEEDS_ATTENTION", "UNKNOWN") if state["health"] == "CURRENT" else ("UNKNOWN",)))
        observed = _when(state["observed_at"]) if ok else None
    except (ValueError, KeyError, TypeError):
        return unavailable("DEGRADED", now)
    if not ok:
        return unavailable("DEGRADED", now)
    if observed > now + MAX_SKEW or now - observed > MAX_AGE:
        return unavailable("STALE", now)
    return {k: state[k] for k in ("channel", "attention", "count", "count_reliable", "health", "sync", "observed_at", "source")}


def attention(now=None, path=ENDPOINT, fetcher=fetch):
    now = now or datetime.now(timezone.utc)
    try:
        host, port = endpoint(path)
    except (OSError, ValueError):
        return unavailable("DISCONNECTED", now)
    try:
        data = fetcher(host, port)
    except OSError:
        return unavailable("DISCONNECTED", now)
    return validate(data, now)


def main(argv):
    if argv != ["attention"]:
        sys.stdout.write("usage: openclaw-imessage attention\n")
        return 2
    sys.stdout.write(json.dumps(attention()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
