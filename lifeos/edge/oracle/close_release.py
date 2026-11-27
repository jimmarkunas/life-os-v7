"""Stdlib-only helper used by install-close.sh as root; changes nothing live. `stage COMMIT DEST`: a full SHA on main;
exactly the Edge files plus the closure the Career entry point declares (read with ast, never executed); blobs whose
git hashes match the commit tree; any surprise refuses before DEST is written. `check DEST`: exactly its MANIFEST.
`serve-state`: unknown unless the status is well-formed. `latest`: main's head once its CI is green. `same A B`: two
trees carry the same files. `policy PORT LOGIN LEDGER PIPELINE NODE...`: host, login, canonical ids and approved
nodes.
"""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = "jimmarkunas/life-os-v7"
APP = "lifeos/sources/apps/close_app.py"
EDGE = ("lifeos/__init__.py", "lifeos/edge/close_gate.py", "lifeos/imessage/__init__.py", "lifeos/imessage/alerts_endpoint.py", "lifeos/platform/__init__.py",
        "lifeos/platform/notion_client.py", "lifeos/platform/limits.py", "lifeos/hiring/__init__.py", "lifeos/sources/__init__.py", APP)
NOTION_ID = re.compile(r"[0-9a-f]{32}\Z")
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
    os.makedirs(dest, mode=0o755)
    for path, data in files.items():
        os.makedirs(os.path.join(dest, os.path.dirname(path)), mode=0o755, exist_ok=True)
        with open(os.path.join(dest, path), "wb") as f:
            f.write(data)
    with open(os.path.join(dest, "MANIFEST"), "w") as f:
        f.write(f"commit {commit}\n" + "".join(f"{blob(d)} {p}\n" for p, d in sorted(files.items())))
    (importer or imports)(dest)


def imports(dest):
    code = (f"import sys; sys.path = [{dest!r}] + [p for p in sys.path if 'packages' not in p and p]; import lifeos.sources.apps.close_app; "
            "from lifeos.edge import close_gate as g; g.ReadOnly(g.notion_client(b'dry-run', {'ledger': '0' * 32}))")
    probe = [sys.executable, "-I", "-B", "-c", code]
    if os.geteuid() == 0 and os.path.isdir("/run/systemd/system"):
        probe = ["systemd-run", "--quiet", "--pipe", "--wait", "--collect", "-p", "DynamicUser=yes", "-p", "ProtectSystem=strict", "-p", "ProtectHome=yes",
                 "-p", "PrivateNetwork=yes", "-p", "PrivateTmp=yes", "-p", "NoNewPrivileges=yes", "-p", "CapabilityBoundingSet=", "-p", "RuntimeMaxSec=30"] + probe
    if subprocess.run(probe, capture_output=True, timeout=45).returncode != 0:
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


def head(fetch=get):
    commit = json.loads(fetch(f"https://api.github.com/repos/{REPO}/commits/main")).get("sha") or ""
    if not SHA.match(commit):
        raise Refused("NOT_A_COMMIT")
    return commit


PR_TESTS = ".github/workflows/pr-tests.yml"
HOURLY = ".github/workflows/hourly.yml"
# same word as lifeos/platform/release_proof.py FLAG_LABEL (a test keeps them equal)
FLAG_LABEL = "release-paused"
PROOF_EXIT = {"PROOF_PAUSED": 3, "PROOF_STATE_UNREADABLE": 4}


def _runs(workflow, commit, fetch):
    runs = json.loads(fetch(f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow}/runs?branch=main&head_sha={commit}&per_page=20")).get("workflow_runs") or []
    return sorted((r for r in runs if r.get("head_sha") == commit and r.get("head_branch") == "main" and r.get("path") == f".github/workflows/{workflow}"),
                  key=lambda r: (r.get("id") or 0, r.get("run_attempt") or 0), reverse=True)


def proof_pause(fetch=get):
    """A failed proof run opens an issue labelled `release-paused`; while one is open the timer deploys nothing. ->
    None when none is open. Raises Refused(PROOF_PAUSED) when one is, and Refused(PROOF_STATE_UNREADABLE) for anything
    it cannot read: never deploy on an unknown state. A pull request carrying the label is not the flag.
    """
    try:
        issues = json.loads(fetch(f"https://api.github.com/repos/{REPO}/issues?state=open&labels={FLAG_LABEL}&per_page=30"))
    except Exception:                                                   # noqa: BLE001
        raise Refused("PROOF_STATE_UNREADABLE") from None
    if not isinstance(issues, list) or not all(isinstance(i, dict) for i in issues):
        raise Refused("PROOF_STATE_UNREADABLE")
    if any("pull_request" not in i for i in issues):
        raise Refused("PROOF_PAUSED")
    if len(issues) >= 30:
        raise Refused("PROOF_STATE_UNREADABLE")


def latest(fetch=get):
    """Edge auto-release: main's head commit once the `pr-tests` push run on main for that exact commit is green (read
    from Actions runs, never a check run). Queued, running, failed or no run at all is NOT_GREEN. A green head is
    still held while an open `release-paused` issue exists (PROOF_PAUSED) or the flag cannot be read
    (PROOF_STATE_UNREADABLE).
    """
    commit = head(fetch)
    for run in _runs("pr-tests.yml", commit, fetch):
        if run.get("event") not in (None, "push"):
            continue
        if run.get("status") == "completed" and run.get("conclusion") == "success":
            proof_pause(fetch)
            return commit
        raise Refused("NOT_GREEN")
    raise Refused("NOT_GREEN")


def hourly_report(commit, fetch=get):
    try:
        for run in _runs("hourly.yml", commit, fetch):
            if run.get("status") != "completed":
                return "running"
            return "green" if run.get("conclusion") == "success" else "red"
        return "none"
    except Exception:                                                   # noqa: BLE001
        return "unknown"


def same(a, b):
    body = lambda d: open(os.path.join(d, "MANIFEST")).read().splitlines()[1:]
    return body(a) == body(b)


def policy(status, port, login, ledger, pipeline, nodes):
    ids = {k: str(v).replace("-", "").lower() for k, v in (("ledger", ledger), ("pipeline", pipeline))}
    if not all(NOTION_ID.match(v) for v in ids.values()):
        raise Refused("NOTION_ID_INVALID")
    users = {str(k): v.get("LoginName") for k, v in (status.get("User") or {}).items()}
    peers = {p.get("ID"): p for p in (status.get("Peer") or {}).values()}
    for n in nodes:
        p = peers.get(n)
        if n == status["Self"].get("ID") or p is None or p.get("Tags") or users.get(str(p.get("UserID"))) != login:
            raise Refused("NODE_REFUSED:" + n)
    if not nodes:
        raise Refused("NO_NODES")
    return {"host": status["Self"]["DNSName"].rstrip(".") + ":" + port, "login": login, **ids, "nodes": list(nodes)}


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


STALL_SECONDS = 7200


def waiting(now, since, window=STALL_SECONDS):
    """The wait for a green main head. `since` is the stored marker (digits) or anything else for none. -> (marker to
    store, True once the wait has lasted `window` seconds). A future marker restarts the wait.
    """
    text = str(since).strip()
    start = int(text) if text.isdigit() else now
    if start > now:
        start = now
    return start, (now - start) >= window


def main(argv):
    try:
        if argv[:1] == ["serve-state"]:
            print(serve_state(sys.stdin.read()))
        elif argv[:1] == ["stage"] and len(argv) == 3:
            stage(argv[1], argv[2])
        elif argv[:1] == ["policy"] and len(argv) >= 6:
            print(json.dumps(policy(json.load(sys.stdin), *argv[1:5], argv[5:])))
        elif argv[:1] == ["check"] and len(argv) == 2:
            check(argv[1])
        elif argv[:1] == ["latest"]:
            try:
                sha = latest()
            except Refused as e:
                if str(e) in PROOF_EXIT:
                    print(f"REFUSED: {e}", file=sys.stderr)
                    sys.exit(PROOF_EXIT[str(e)])
                raise
            print(f"hourly run on {sha[:12]}: {hourly_report(sha)} (reported, does not block)", file=sys.stderr)
            print(sha)
        elif argv[:1] == ["head"]:
            print(head())
        elif argv[:1] == ["same"] and len(argv) == 3:
            sys.exit(0 if same(argv[1], argv[2]) else 1)
        elif argv[:1] == ["wait"] and len(argv) == 3:
            since, stalled = waiting(int(argv[1]), argv[2])
            print(since, int(stalled))
        else:
            sys.exit("usage: close_release.py stage COMMIT DEST | check DEST | serve-state | wait NOW SINCE")
    except (Refused, OSError, ValueError, KeyError) as e:
        sys.exit(f"REFUSED: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
