#!/usr/bin/python3 -I
"""LIFE-306, route C (Jim, Nov 5, 2026: "build C"): the secrets broker on lifeos-edge-01. A root-owned fixed program that USES a host credential for a registered PURPOSE, so the
value never appears in a prompt, chat, Jira, log or file an agent reads.

    sudo -n /usr/local/sbin/lifeos-secret-use PURPOSE

One argument, a purpose name. Nothing else is accepted: no secret name, URL, command, header or value (anything extra is SECRET_BAD_ARGS). The purpose registry
(/etc/lifeos-secrets/purposes.d/*.json, root-owned, not writable by anyone else) fixes, for each purpose, the ONE credential it may use (an encrypted systemd credential in
/etc/hermes-eval/creds), the users who may call it, and one action from a closed list:
  http_get_fields   GET one fixed https URL with the credential as a bearer token and return only the listed JSON fields (scalars only).
  check_credential  return the credential's NAME, its length and the first 8 hex characters of its SHA-256. Never the value.
Every output is searched for the value in raw, base64, URL-encoded, hex and JSON-escaped forms; a hit refuses the whole answer (SECRET_OUTPUT_CONTAINS_SECRET). Output is capped at 4096
bytes and a caller gets 20 calls per minute. Every call, refused or not, appends one line to /var/log/lifeos-secret-use.jsonl (epoch, Central Time, caller, pid, purpose, credential
NAME, result code, bytes returned); if the log cannot be written the call is refused before anything is used (fail closed). No network listener, no timer, no daemon: it runs when called.

Honest limit: the agent account on this host has passwordless root, so this program cannot make it technically impossible for an agent that chooses to read a credential. It makes the
normal path never show a value, shrinks what is asked for, and records every use. The caller name is SUDO_USER, which a root-capable caller could forge. Stdlib only.

Fixed result codes (reply ok false): SECRET_BAD_ARGS, SECRET_NOT_ROOT, SECRET_REGISTRY_UNSAFE, SECRET_UNKNOWN_PURPOSE, SECRET_CALLER_NOT_ALLOWED, SECRET_RATE_LIMITED, SECRET_AUDIT_UNAVAILABLE, SECRET_CRED_UNREADABLE, SECRET_FETCH_FAILED,
SECRET_BAD_REPLY, SECRET_FIELD_NOT_SCALAR, SECRET_OUTPUT_TOO_LARGE, SECRET_OUTPUT_CONTAINS_SECRET. Never a message, path or exception text."""
import base64
import binascii
import hashlib
import json
import os
import pwd
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

REGISTRY = "/etc/lifeos-secrets/purposes.d"
CREDS = "/etc/hermes-eval/creds"
LOG = "/var/log/lifeos-secret-use.jsonl"
DECRYPT = "/usr/bin/systemd-creds"
PURPOSE = re.compile(r"[a-z][a-z0-9_]{1,40}")
NAME = re.compile(r"[a-z][a-z0-9_]{1,60}")
HTTPS = re.compile(r"https://[a-z0-9.-]+(/[A-Za-z0-9._~/-]*)?")
MAX_OUT, PER_MINUTE, FETCH_WAIT, FETCH_MAX = 4096, 20, 15, 65536
ACTIONS = ("http_get_fields", "check_credential")
CT = ZoneInfo("America/Chicago")


class Refused(Exception):
    """A fixed code; carries nothing else."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def forms(value):
    """Every spelling of the value that must never appear in an answer."""
    raw = value.encode()
    out = {value, value.encode().hex(), urllib.parse.quote(value, safe=""), json.dumps(value)[1:-1]}
    for enc in (base64.b64encode, base64.urlsafe_b64encode):
        text = enc(raw).decode()
        out.update({text, text.rstrip("=")})
    return {f for f in out if f}


def systemd_decrypt(name):
    try:
        done = subprocess.run([DECRYPT, "decrypt", "--name=" + name, os.path.join(CREDS, name + ".cred"), "-"], capture_output=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise Refused("SECRET_CRED_UNREADABLE") from None
    if done.returncode != 0 or not done.stdout.strip():
        raise Refused("SECRET_CRED_UNREADABLE")
    try:
        return done.stdout.decode().strip()
    except UnicodeDecodeError:
        raise Refused("SECRET_CRED_UNREADABLE") from None


def http_fetch(url, bearer):
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + bearer, "Accept": "application/json", "User-Agent": "lifeos-secret-use"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_WAIT) as reply:                 # nosec: https only, checked against the registry pattern
            return reply.read(FETCH_MAX + 1)
    except (urllib.error.URLError, OSError, ValueError):
        raise Refused("SECRET_FETCH_FAILED") from None


class Broker:
    def __init__(self, registry=REGISTRY, creds=CREDS, log=LOG, decrypt=systemd_decrypt, fetch=http_fetch, euid=None, clock=time.time, owner=0):
        self.registry, self.creds, self.log = registry, creds, log
        self.decrypt, self.fetch, self.clock, self.owner = decrypt, fetch, clock, owner      # owner: the uid that must own the registry (root; tests pass their own)
        self.euid = os.geteuid() if euid is None else euid

    # ---- audit -------------------------------------------------------------------------------------------------------------------------------------------------
    def append(self, row):
        data = (json.dumps(row, sort_keys=True) + "\n").encode()
        fd = os.open(self.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def recent_calls(self, caller, now):
        try:
            with open(self.log, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 262144))
                tail = handle.read().decode(errors="replace").splitlines()
        except OSError:
            return 0
        count = 0
        for line in tail:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (isinstance(row, dict) and row.get("caller") == caller and row.get("result") != "START"       # a call writes START and then its outcome: count the outcome only
                    and isinstance(row.get("epoch"), (int, float)) and row["epoch"] > now - 60):
                count += 1
        return count

    # ---- registry -----------------------------------------------------------------------------------------------------------------------------------------------
    def purpose_of(self, purpose):
        path = os.path.join(self.registry, purpose + ".json")
        try:
            info = os.stat(path)
        except OSError:
            raise Refused("SECRET_UNKNOWN_PURPOSE") from None
        dir_info = os.stat(self.registry)
        if info.st_uid != self.owner or dir_info.st_uid != self.owner or info.st_mode & 0o022 or dir_info.st_mode & 0o022:
            raise Refused("SECRET_REGISTRY_UNSAFE")
        try:
            with open(path) as handle:
                spec = json.load(handle)
        except (OSError, ValueError):
            raise Refused("SECRET_REGISTRY_UNSAFE") from None
        ok = (isinstance(spec, dict) and spec.get("purpose") == purpose and isinstance(spec.get("secret"), str) and NAME.fullmatch(spec["secret"])
              and spec.get("action") in ACTIONS and isinstance(spec.get("callers"), list) and spec["callers"] and all(isinstance(c, str) for c in spec["callers"]))
        if ok and spec["action"] == "http_get_fields":
            ok = (isinstance(spec.get("url"), str) and HTTPS.fullmatch(spec["url"]) and isinstance(spec.get("fields"), list) and spec["fields"]
                  and all(isinstance(f, str) and re.fullmatch(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*", f) for f in spec["fields"]))
        if not ok:
            raise Refused("SECRET_REGISTRY_UNSAFE")
        return spec

    # ---- actions ------------------------------------------------------------------------------------------------------------------------------------------------
    def act(self, spec, value):
        if spec["action"] == "check_credential":
            return {"name": spec["secret"], "length": len(value), "sha256_prefix": hashlib.sha256(value.encode()).hexdigest()[:8]}
        body = self.fetch(spec["url"], value)
        if len(body) > FETCH_MAX:
            raise Refused("SECRET_BAD_REPLY")
        try:
            doc = json.loads(body)
        except ValueError:
            raise Refused("SECRET_BAD_REPLY") from None
        out = {}
        for path in spec["fields"]:
            node = doc
            for part in path.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if node is not None and (isinstance(node, (dict, list)) or (isinstance(node, str) and len(node) > 100)):
                raise Refused("SECRET_FIELD_NOT_SCALAR")
            out[path] = node
        return out

    # ---- one call -----------------------------------------------------------------------------------------------------------------------------------------------
    def run(self, argv, caller):
        """-> (exit code, stdout text). The audit line is written before any credential is touched and once more with the outcome."""
        now = self.clock()
        row = {"epoch": int(now), "ct": datetime.fromtimestamp(now, CT).strftime("%Y-%m-%d %I:%M:%S %p CT"), "caller": caller, "pid": os.getpid(), "purpose": "-", "secret": "-", "bytes": 0}
        code, text = "OK", ""
        try:
            try:
                row["purpose"] = argv[0] if len(argv) == 1 and PURPOSE.fullmatch(argv[0]) else "-"
                if len(argv) != 1 or row["purpose"] == "-":
                    raise Refused("SECRET_BAD_ARGS")
                if self.euid != 0:
                    raise Refused("SECRET_NOT_ROOT")
                recent = self.recent_calls(caller, now)
                try:
                    self.append(dict(row, result="START"))
                except OSError:
                    raise Refused("SECRET_AUDIT_UNAVAILABLE") from None
                if recent >= PER_MINUTE:
                    raise Refused("SECRET_RATE_LIMITED")
                spec = self.purpose_of(row["purpose"])
                row["secret"] = spec["secret"]
                if caller not in spec["callers"]:
                    raise Refused("SECRET_CALLER_NOT_ALLOWED")
                value = self.decrypt(spec["secret"])
                result = self.act(spec, value)
                text = json.dumps({"ok": True, "purpose": row["purpose"], "result": result}, sort_keys=True)
                if any(f in text for f in forms(value)):
                    raise Refused("SECRET_OUTPUT_CONTAINS_SECRET")
                if len(text.encode()) > MAX_OUT:
                    raise Refused("SECRET_OUTPUT_TOO_LARGE")
                row["bytes"] = len(text.encode())
            except Refused as refused:
                code = refused.code
                text = json.dumps({"ok": False, "code": code})
        finally:
            row["result"] = code
            try:
                self.append(row)
            except OSError:
                code, text = "SECRET_AUDIT_UNAVAILABLE", json.dumps({"ok": False, "code": "SECRET_AUDIT_UNAVAILABLE"})
        return (0 if code == "OK" else 1), text


def caller_name():
    name = os.environ.get("SUDO_USER", "")
    if name:
        return name
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return "uid%d" % os.getuid()


def main(argv=None, out=None):
    out = out or sys.stdout
    status, text = Broker().run(list(sys.argv[1:] if argv is None else argv), caller_name())
    out.write(text + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
