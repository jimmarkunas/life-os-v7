#!/usr/bin/python3 -I
"""Read-only Concord health helper on Oracle; adds no credential, timer or scheduler. One process per connection as
the `concord` user (socket activation, stdin/stdout); the only caller is lifeos/imessage/alerts_endpoint.py. Request
`concord1\\n`; one JSON reply line {"ok", "code" ("" or a fixed word), "messages" (a count), "at" (epoch seconds)}. It
runs `concord -C <repo> messages --json` with a short timeout, checks the shape of each message record and counts
them; nothing else of the CLI's output leaves. Codes: CONCORD_BAD_FRAME, CONCORD_CLI_MISSING, CONCORD_READ_FAILED,
CONCORD_TIMEOUT, CONCORD_BAD_OUTPUT. Stdlib only.
"""

import json
import os
import subprocess
import sys
import time

CLI = "/home/concord/.npm-global/bin/concord"
REPO = "/home/concord/life-os-v7"
WAIT = 25
# the same shape lifeos/inbox/concord_source.py requires of a record
FIELDS = ("messageId", "senderAgentId", "recipientAgentId", "content", "createdAt", "status")


def _valid(message):
    return isinstance(message, dict) and all(isinstance(message.get(k), str) for k in FIELDS)


def count_messages(stdout):
    """-> the number of valid message records in the CLI's output (an optional notice line first, then one JSON array), or None when it is not that."""
    text = stdout if isinstance(stdout, str) else ""
    start = text.find("[")
    if start < 0:
        return None
    try:
        items = json.loads(text[start:])
    except ValueError:
        return None
    return len(items) if isinstance(items, list) and all(_valid(m) for m in items) else None


def answer(raw, run=subprocess.run, now=time.time, cli=CLI, repo=REPO):
    if raw != b"concord1\n":
        return {"ok": False, "code": "CONCORD_BAD_FRAME", "messages": 0, "at": int(now())}
    env = {"PATH": "/usr/bin:/bin", "HOME": os.path.dirname(repo), "CONCORD_TELEMETRY_DISABLED": "1"}
    try:
        done = run(
            [cli, "-C", repo, "messages", "--json"], capture_output=True, text=True, timeout=WAIT, env=env, check=False
        )
    except FileNotFoundError:
        return {"ok": False, "code": "CONCORD_CLI_MISSING", "messages": 0, "at": int(now())}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "CONCORD_TIMEOUT", "messages": 0, "at": int(now())}
    except Exception:  # noqa: BLE001
        return {"ok": False, "code": "CONCORD_READ_FAILED", "messages": 0, "at": int(now())}
    if done.returncode != 0:
        return {"ok": False, "code": "CONCORD_READ_FAILED", "messages": 0, "at": int(now())}
    count = count_messages(done.stdout)
    if count is None:
        return {"ok": False, "code": "CONCORD_BAD_OUTPUT", "messages": 0, "at": int(now())}
    return {"ok": True, "code": "", "messages": count, "at": int(now())}


def main(stdin=None, stdout=None, run=subprocess.run, now=time.time):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    reply = answer(stdin.readline(16), run, now)
    stdout.write(json.dumps(reply, separators=(",", ":"), sort_keys=True).encode() + b"\n")
    stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
