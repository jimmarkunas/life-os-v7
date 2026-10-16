"""LIFE-47 S2 (Edge): proof that the Close Opportunity tree on Oracle is exactly an approved V7 commit, and a fail-closed reading of Tailscale Serve.
Stdlib only; used by install-close.sh as root. Nothing here changes the live tree, units or Serve.
  stage COMMIT DEST   the commit must be a full SHA already on main; the file set is the fixed Edge files plus the CLOSURE the Career entry point
                      declares (read with ast, never executed); every entry must be a regular git blob whose content hash matches the commit's tree;
                      the staged tree must import with nothing outside it. Any surprise refuses before DEST is written.
  check DEST          the tree holds exactly its MANIFEST: same files, same hashes, no extra file and no symlink.
  serve-state         Serve status JSON on stdin -> ours | free | other | unknown; only a well-formed status can say free."""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = "jimmarkunas/life-os-v7"
APP = "lifeos/hiring/close_app.py"
EDGE = ("lifeos/__init__.py", "lifeos/edge/close_gate.py", "lifeos/imessage/__init__.py", "lifeos/imessage/alerts_endpoint.py", "lifeos/hiring/__init__.py", APP)
PATH = re.compile(r"lifeos(/[a-z0-9_]+)+\.py\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


class Refused(Exception):
    pass


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lifeos-close"}), timeout=20) as r:
        return r.read()


def blob(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def closure(app_source):
    for node in ast.parse(app_source).body:
        if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == ["CLOSURE"]:
            paths = ast.literal_eval(node.value)
            if not isinstance(paths, tuple) or not all(isinstance(p, str) and PATH.match(p) for p in paths):
                raise Refused("UNSAFE_CLOSURE")
            return sorted(set(EDGE) | set(paths))
    raise Refused("NO_CLOSURE")


def stage(commit, dest, fetch=get, importer=None):
    if not SHA.match(commit):
        raise Refused("NOT_A_COMMIT")
    if json.loads(fetch(f"https://api.github.com/repos/{REPO}/compare/{commit}...main")).get("status") not in ("ahead", "identical"):
        raise Refused("NOT_ON_MAIN")
    tree = {e.get("path"): e for e in json.loads(fetch(f"https://api.github.com/repos/{REPO}/git/trees/{commit}?recursive=1")).get("tree") or []}
    raw = lambda p: fetch(f"https://raw.githubusercontent.com/{REPO}/{commit}/{p}")
    if APP not in tree:
        raise Refused("ENTRY_POINT_MISSING")
    files = {}
    for path in closure(raw(APP)):
        entry = tree.get(path) or {}
        if entry.get("type") != "blob" or entry.get("mode") != "100644":
            raise Refused("NOT_A_REGULAR_FILE:" + path)
        data = raw(path)
        if blob(data) != entry.get("sha"):
            raise Refused("CONTENT_MISMATCH:" + path)
        files[path] = data
    os.makedirs(dest, mode=0o755)                                       # must not exist yet
    for path, data in files.items():
        os.makedirs(os.path.join(dest, os.path.dirname(path)), mode=0o755, exist_ok=True)
        with open(os.path.join(dest, path), "wb") as f:
            f.write(data)
    with open(os.path.join(dest, "MANIFEST"), "w") as f:
        f.write(f"commit {commit}\n" + "".join(f"{blob(d)} {p}\n" for p, d in sorted(files.items())))
    (importer or imports)(dest)


def imports(dest):
    code = f"import sys; sys.path = [{dest!r}] + [p for p in sys.path if 'packages' not in p and p]; import lifeos.edge.close_gate, lifeos.hiring.close_app"
    if subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, timeout=30).returncode != 0:
        raise Refused("CLOSURE_INCOMPLETE")


def check(dest):
    lines = open(os.path.join(dest, "MANIFEST")).read().splitlines()
    want = dict(reversed(line.split(" ", 1)) for line in lines[1:])
    seen = {}
    for root, dirs, names in os.walk(dest):
        for n in dirs + names:
            if os.path.islink(os.path.join(root, n)):
                raise Refused("SYMLINK")
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), dest)
            if rel != "MANIFEST":
                seen[rel] = blob(open(os.path.join(root, n), "rb").read())
    if seen != want:
        raise Refused("TREE_DIFFERS")


def serve_state(text, tailnet="47443", local="47344"):
    try:
        cfg = json.loads(text)
    except (TypeError, ValueError):
        return "unknown"
    if not isinstance(cfg, dict) or not all(isinstance(cfg.get(k, {}), dict) for k in ("TCP", "AllowFunnel", "Web")):
        return "unknown"
    h = (cfg.get("TCP") or {}).get(tailnet)
    if any(v and str(k).endswith(":" + tailnet) for k, v in (cfg.get("AllowFunnel") or {}).items()) or any(str(k).endswith(":" + tailnet) for k in cfg.get("Web") or {}):
        return "other"
    if h is None:
        return "free"
    ours = isinstance(h, dict) and h.get("TCPForward") == "127.0.0.1:" + local and h.get("ProxyProtocol") == 2 and bool(h.get("TerminateTLS"))
    return "ours" if ours else "other"


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["serve-state"]:
            print(serve_state(sys.stdin.read()))
        elif sys.argv[1:2] == ["stage"] and len(sys.argv) == 4:
            stage(sys.argv[2], sys.argv[3])
        elif sys.argv[1:2] == ["check"] and len(sys.argv) == 3:
            check(sys.argv[2])
        else:
            sys.exit("usage: close_release.py stage COMMIT DEST | check DEST | serve-state")
    except (Refused, OSError, ValueError, KeyError) as e:
        sys.exit(f"REFUSED: {e}")
