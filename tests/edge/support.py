"""LIFE-544: test helpers that more than one test module (often in another domain) uses. Moved here out of test_*.py files, unchanged."""
from lifeos.edge.oracle import close_release as rel
import json


C = "a" * 40


APP_SRC = b'CLOSURE = ("lifeos/hiring/close.py",)\n'


FILES = {**{p: b"# stub\n" for p in rel.EDGE}, rel.APP: APP_SRC, "lifeos/hiring/close.py": b"# close\n"}


def github(files=FILES, status="ahead", tree_extra=None, mode="100644"):
    tree = [{"path": p, "type": "blob", "mode": mode, "sha": rel.blob(d)} for p, d in files.items()] + (tree_extra or [])
    urls = {f"https://api.github.com/repos/{rel.REPO}/compare/{C}...main": json.dumps({"status": status}).encode(),
            f"https://api.github.com/repos/{rel.REPO}/git/trees/{C}?recursive=1": json.dumps({"tree": tree}).encode()}
    urls.update({f"https://raw.githubusercontent.com/{rel.REPO}/{C}/{p}": d for p, d in files.items()})
    return urls.__getitem__
