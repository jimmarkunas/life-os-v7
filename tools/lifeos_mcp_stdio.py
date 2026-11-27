"""stdio MCP bridge from a Mac client (Codex) to the Oracle LIFE OS MCP, over the documented credential path.

  python3 tools/lifeos_mcp_stdio.py --credential codex

At start it reads the client's scoped credential from the Mac Keychain (service `lifeos-mcp-<id>`, account
`lifeos-mcp`), opens its own `ssh -N -L <free port>:127.0.0.1:8787 opc@lifeos-edge-01`, then relays newline-delimited
JSON-RPC from stdin to `POST /mcp` with the bearer header and writes each reply as one line to stdout. Notifications
(no id) get no reply. The credential is never printed, logged or put on a command line; the tunnel ends with the bridge.
Stdlib only."""

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOST = "opc@lifeos-edge-01"
REMOTE = "127.0.0.1:8787"


def keychain_secret(credential, run=subprocess.run):
    out = run(
        ["security", "find-generic-password", "-s", f"lifeos-mcp-{credential}", "-a", "lifeos-mcp", "-w"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(f"no Keychain item lifeos-mcp-{credential}")
    secret = out.stdout.strip()
    return secret if secret.startswith("lm1.") else f"lm1.{credential}.{secret}"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def open_tunnel(port, popen=subprocess.Popen, connect=socket.create_connection, wait=10.0):
    proc = popen(
        ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-L", f"{port}:{REMOTE}", HOST],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            connect(("127.0.0.1", port), timeout=1).close()
            return proc
        except OSError:
            time.sleep(0.2)
    proc.terminate()
    raise SystemExit("tunnel to the LIFE OS MCP did not open")


def relay_line(line, url, token, opener=urllib.request.urlopen):
    """-> the reply line for one JSON-RPC message, or None for a notification or a blank line."""
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except ValueError:
        return json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
    request = urllib.request.Request(
        url,
        data=line.encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=60) as answer:
            body = answer.read().decode()
    except urllib.error.HTTPError as error:
        body = error.read().decode() or json.dumps({"error": f"http_{error.code}"})
        if "id" not in msg:
            return None
        return json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32000, "message": body[:200]}})
    except OSError:
        if "id" not in msg:
            return None
        return json.dumps(
            {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32000, "message": "lifeos_mcp_unreachable"}}
        )
    if "id" not in msg or not body.strip():
        return None
    return body.strip().replace("\n", " ")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--credential", required=True)
    args = ap.parse_args(argv)
    token = keychain_secret(args.credential)
    port = free_port()
    tunnel = open_tunnel(port)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for line in sys.stdin:
            reply = relay_line(line, url, token)
            if reply is not None:
                sys.stdout.write(reply + "\n")
                sys.stdout.flush()
    finally:
        tunnel.terminate()


if __name__ == "__main__":
    main()
