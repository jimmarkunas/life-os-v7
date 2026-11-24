#!/usr/bin/python3 -I
"""LIFE-950: keep the Honcho memory token alive. The Oracle LIFE OS MCP reads HONCHO_MEMORY_TOKEN (a Honcho JWT scoped to the `personal` workspace) from /etc/lifeos-buzz-mcp/config.env; it used to end Dec 5 2026.

Run once a day by lifeos-honcho-token-renew.timer (3:45 AM CT, after the nightly backup). When the token has 30 days or fewer left it mints a new 60-day one for the same workspace (Honcho's own generate_jwt.py inside
the API container), proves the new token on the live Honcho API, swaps only that one line of config.env atomically (mode 0600 kept), restarts the MCP and checks it answers 401 without credentials. If any step after the swap
fails, the old line is put back and the MCP restarted. Any failure sends one Agent Inbox alert (honcho-alert; same subject, same row) and exits non-zero; a failed renewal never leaves the MCP without a working token.
Prints days left and the outcome; the token is never printed, logged or put in an alert. No agent is started. Stdlib only."""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONFIG = "/etc/lifeos-buzz-mcp/config.env"
KEY = "HONCHO_MEMORY_TOKEN"
WORKSPACE = "personal"
PEER = "lifeos-memory"
LIFETIME = "60d"
RENEW_WITHIN = 30 * 86400                                   # renew when this much or less is left: the token always has at least 30 days, so it never reaches its end
MIN_NEW = 50 * 86400                                        # a freshly minted token must be good for at least this long
API = "http://127.0.0.1:8000"
MCP = "http://127.0.0.1:8787/mcp"
ALERT = "/opt/lifeos-buzz-mcp/current/bin/honcho-alert"
SERVICE = "lifeos-buzz-mcp"
MINT = ["docker", "exec", "honcho-api-1", "/app/.venv/bin/python", "scripts/generate_jwt.py", "--workspace", WORKSPACE, "--expires", LIFETIME, "--print-only"]


def claims(token):
    """-> the JWT's claims (read, not verified: it is our own token), or None."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, AttributeError):
        return None


def read_token(path=CONFIG):
    with open(path) as handle:
        for line in handle:
            if line.startswith(KEY + "="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def write_token(token, path=CONFIG):
    """Replace only the KEY line (atomic, same mode); a missing line is added."""
    with open(path) as handle:
        lines = handle.read().splitlines()
    out, done = [], False
    for line in lines:
        if line.startswith(KEY + "="):
            out.append(f"{KEY}={token}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{KEY}={token}")
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, os.stat(path).st_mode & 0o777)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(out) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def mint(run=subprocess.run):
    out = run(MINT, capture_output=True, text=True, timeout=60)
    token = (out.stdout or "").strip().splitlines()[-1].strip() if out.returncode == 0 and (out.stdout or "").strip() else ""
    c = claims(token)
    return token if c and c.get("w") == WORKSPACE and "exp" in c else ""


def answers(token, opener=urllib.request.urlopen):
    """True when Honcho accepts the token for the personal workspace (the same read the memory client makes)."""
    request = urllib.request.Request(f"{API}/v3/workspaces/{WORKSPACE}/conclusions/list?page=1&size=1", data=json.dumps({"filters": {"observer_id": PEER}}).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with opener(request, timeout=20) as reply:
            return reply.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def mcp_ok(run=subprocess.run, opener=urllib.request.urlopen, sleep=time.sleep):
    """The MCP is active and answers 401 to an unauthenticated call."""
    sleep(3)
    if run(["systemctl", "is-active", "--quiet", SERVICE]).returncode != 0:
        return False
    try:
        request = urllib.request.Request(MCP, data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', method="POST", headers={"Content-Type": "application/json"})
        opener(request, timeout=20)
        return False
    except urllib.error.HTTPError as err:
        return err.code == 401
    except (urllib.error.URLError, OSError):
        return False


def alert(why, run=subprocess.run):
    try:
        run([ALERT, "send", "Honcho memory token renewal failed", why + " The current token keeps working until it ends; memory is untouched.", "--urgency", "Today"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass


def renew(now=None, path=CONFIG, run=subprocess.run, opener=urllib.request.urlopen, sleep=time.sleep, out=print):
    """-> 0 ok (renewed or not yet due), 1 failed (alert sent, old token in place)."""
    now = now if now is not None else time.time()
    old = read_token(path)
    c = claims(old)
    if not c or "exp" not in c:
        alert("The configured memory token is missing or unreadable.", run)
        out("token missing or unreadable")
        return 1
    left = int(c["exp"] - now)
    out(f"token days left: {left // 86400}")
    if left > RENEW_WITHIN:
        out("not due")
        return 0
    new = mint(run)
    nc = claims(new)
    if not new or nc["exp"] - now < MIN_NEW:
        alert("A new token could not be issued.", run)
        out("mint failed")
        return 1
    if not answers(new, opener):
        alert("The new token was not accepted by Honcho.", run)
        out("new token not accepted")
        return 1
    write_token(new, path)
    run(["systemctl", "restart", SERVICE], timeout=60)
    if not mcp_ok(run, opener, sleep):
        write_token(old, path)
        run(["systemctl", "restart", SERVICE], timeout=60)
        alert("The MCP did not come back with the new token; the old token was put back.", run)
        out("rolled back")
        return 1
    out(f"renewed: new token days left: {int(nc['exp'] - now) // 86400}")
    return 0


if __name__ == "__main__":
    sys.exit(renew())
