"""stdio MCP bridge from a Mac client (Codex) to the Oracle LIFE OS MCP.

  python3 tools/lifeos_mcp_stdio.py --credential codex

At start it reads the client's scoped credential from the Mac Keychain (service `lifeos-mcp-<id>`, account
`lifeos-mcp`). Each newline-delimited JSON-RPC message from stdin is posted to the service on Oracle's loopback
(`127.0.0.1:8787/mcp`) by running curl on the host over ssh, because the `opc` key refuses port forwarding
(`no-port-forwarding`), so an `ssh -L` tunnel is not available. One multiplexed ssh connection is reused. The bearer
header travels on stdin and reaches curl through a file descriptor, so the credential is never on a command line on
either machine, never printed and never logged. Each reply is written as one line to stdout; notifications (no id)
get no reply. Stdlib only."""

import argparse
import json
import shlex
import subprocess
import sys

HOST = "opc@lifeos-edge-01"
URL = "http://127.0.0.1:8787/mcp"
# Remote side: the first stdin line is the header, the rest is the body. The header reaches curl on fd 3 (not argv).
REMOTE = (
    'IFS= read -r H; exec 3<<HDR\n$H\nHDR\n'
    f"curl -s -X POST {URL} -H @/dev/fd/3 -H 'Content-Type: application/json' "
    "-H 'Accept: application/json, text/event-stream' --data-binary @-"
)


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


def ssh_argv(control_path):
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={control_path}",
        "-o",
        "ControlPersist=120",
        HOST,
        "bash -c " + shlex.quote(REMOTE),
    ]


def relay_line(line, token, control_path, run=subprocess.run):
    """-> the reply line for one JSON-RPC message, or None for a notification or a blank line."""
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except ValueError:
        return json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
    try:
        out = run(
            ssh_argv(control_path),
            input=f"Authorization: Bearer {token}\n{line}",
            capture_output=True,
            text=True,
            timeout=90,
        )
        body = out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        body = ""
    if "id" not in msg:
        return None
    if not body:
        return json.dumps(
            {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32000, "message": "lifeos_mcp_unreachable"}}
        )
    try:
        json.loads(body)
    except ValueError:
        return json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32000, "message": body[:200]}})
    return body.replace("\n", " ")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--credential", required=True)
    args = ap.parse_args(argv)
    token = keychain_secret(args.credential)
    control = f"/tmp/lm-{args.credential}-%C"  # short: a unix socket path is capped at 104 bytes on macOS
    for line in sys.stdin:
        reply = relay_line(line, token, control)
        if reply is not None:
            sys.stdout.write(reply + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
