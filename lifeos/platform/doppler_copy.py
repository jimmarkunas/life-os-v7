"""LIFE-981: the one-time copy of the Bitwarden runtime secrets into Doppler (removed after it runs).

Jim, Nov 25 2026, comment 14289, path 2. Removed from the repository right after the copy is proven.

Runs only in the manual `doppler-copy` workflow. It reads the Bitwarden items with the pinned `bws` CLI and the runtime token (BWS_ACCESS_TOKEN), picks every `<item id> > NAME` pair the workflows load, and writes the
values to Doppler with the write token it finds by name (DOPPLER_PARITY_WRITE_TOKEN). Values live in this process's memory only: never printed, never written to a file, an artifact or a cache; every line that is
printed is a name, a count or a fixed code. Equality is by SHA-256 in memory, and the copy is read back from Doppler and compared again. Bitwarden is never written. Exit 0 only when every name matches."""
import glob
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

PAIR = re.compile(r"^\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*>\s*([A-Za-z0-9_]+)\s*(#.*)?$")
TOKEN_NAME = "_".join(("DOPPLER", "PARITY", "WRITE", "TOKEN"))
PROJECT, CONFIG = "life-os", "prd"
SECRETS_URL = "https://api.doppler.com/v3/configs/config/secrets"
BATCH = 15


def pairs(root="."):
    """-> {NAME: item id} from every workflow and action under .github; a name with two different ids is dropped to `conflicts`."""
    found, conflicts = {}, set()
    for path in sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.yml")) + glob.glob(os.path.join(root, ".github", "actions", "*", "action.yml"))):
        with open(path) as handle:
            for line in handle:
                match = PAIR.match(line)
                if match:
                    item, name = match.group(1), match.group(2)
                    if found.setdefault(name, item) != item:
                        conflicts.add(name)
    return {n: i for n, i in found.items() if n not in conflicts}, sorted(conflicts)


def bitwarden_items(run=subprocess.run, environ=os.environ):
    """-> {item id: (key, value)} from `bws secret list`, or None. The output stays in this process."""
    done = run(["bws", "secret", "list", "--output", "json"], capture_output=True, text=True, env={**environ})
    if done.returncode != 0:
        return None
    try:
        rows = json.loads(done.stdout)
    except ValueError:
        return None
    return {row["id"]: (row.get("key") or "", row.get("value")) for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}


def sha(value):
    return hashlib.sha256(value.encode()).digest()


def _call(method, url, token, body=None, opener=urllib.request.urlopen):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"})
    with opener(request, timeout=30) as reply:
        return json.loads(reply.read().decode() or "{}")


def doppler_values(token, call=_call):
    """-> {name: value} from the config's download, or None. Never prints."""
    try:
        data = call("GET", f"{SECRETS_URL}/download?format=json&project={PROJECT}&config={CONFIG}", token)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return data if isinstance(data, dict) and all(isinstance(v, str) for v in data.values()) else None


def doppler_write(token, batch, call=_call):
    try:
        call("POST", SECRETS_URL, token, {"project": PROJECT, "config": CONFIG, "secrets": batch})
        return True
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False


def main(root=".", run=subprocess.run, environ=os.environ, call=_call, out=print):
    wanted, conflicts = pairs(root)
    items = bitwarden_items(run, environ)
    if items is None:
        out("doppler-copy: FAILED code=bitwarden_unreadable")
        return 1
    token = next((value for key, value in items.values() if value and (key == TOKEN_NAME or key.endswith("/" + TOKEN_NAME))), None)
    if not token:
        out("doppler-copy: FAILED code=write_token_missing")
        return 1
    for _, value in items.values():
        for line in str(value or "").splitlines():
            if len(line.strip()) >= 8:
                out(f"::add-mask::{line.strip()}")                              # the runner hides it from here on; this process never prints a value
    values, failed = {}, {}
    for name, item in sorted(wanted.items()):
        value = (items.get(item) or (None, None))[1]
        if not isinstance(value, str):
            failed[name] = "bitwarden_item_missing"
        elif value == "":
            failed[name] = "empty_in_bitwarden"
        else:
            values[name] = value
    for name in conflicts:
        failed[name] = "two_item_ids"
    have = doppler_values(token, call)
    if have is None:
        out("doppler-copy: FAILED code=doppler_unreachable")
        return 1
    todo = {n: v for n, v in values.items() if not (n in have and hmac.compare_digest(sha(have[n]), sha(v)))}
    copied, batch_failed = [], []
    names = sorted(todo)
    for start in range(0, len(names), BATCH):
        chunk = names[start:start + BATCH]
        (copied if doppler_write(token, {n: todo[n] for n in chunk}, call) else batch_failed).extend(chunk)
    for name in batch_failed:
        failed[name] = "doppler_write_failed"
    after = doppler_values(token, call)
    mismatched, missing = [], []
    for name, value in sorted(values.items()):
        if after is None or name not in after:
            missing.append(name)
        elif not hmac.compare_digest(sha(after[name]), sha(value)):
            mismatched.append(name)
    result = {"names": len(wanted) + len(conflicts), "copied": len([n for n in copied if n not in failed]), "already_equal": len(values) - len(todo), "mismatched": len(mismatched), "missing_after": len(missing),
              "failed": len(failed), "failed_codes": {n: c for n, c in sorted(failed.items())}, "readback": "ok" if after is not None else "doppler_unreachable"}
    out("doppler-copy: written " + ", ".join(sorted(n for n in copied if n not in failed)))
    out("doppler-copy: " + json.dumps(result, sort_keys=True))
    return 0 if not (mismatched or missing or failed) and after is not None else 1


if __name__ == "__main__":
    sys.exit(main())
