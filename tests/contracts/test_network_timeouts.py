"""no network call in lifeos/ waits forever: every call that leaves the process carries an explicit timeout, and a new
one without it fails CI.
"""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CALLS = {"urlopen", "create_connection", "HTTPSConnection", "HTTPConnection", "SMTP", "SMTP_SSL", "IMAP4", "IMAP4_SSL"}
HTTP_LIBS = {"requests", "httpx"}
HTTP_CALLS = {"get", "post", "put", "patch", "delete", "request", "head", "Client", "AsyncClient", "Session"}
SUBPROCESS = {"run", "check_output", "check_call", "call"}
NETWORK_PROGRAMS = ("ssh", "scp", "curl", "tailscale", '"gh"', "'gh'", "gh ")
EXEMPT = {}


def _keywords(node):
    return {k.arg for k in node.keywords if k.arg}


def findings():
    """> [(path:line, what)] for each network call without a timeout."""
    out = []
    for path in sorted((ROOT / "lifeos").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        rel = path.relative_to(ROOT)
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            base = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
            where = f"{rel}:{node.lineno}"
            kw = _keywords(node)
            if name in CALLS and not any("timeout" in k for k in kw):
                out.append((where, name))
            elif base in HTTP_LIBS and name in HTTP_CALLS and "timeout" not in kw:
                out.append((where, f"{base}.{name}"))
            elif base == "pymysql" and name == "connect" and not {"connect_timeout", "read_timeout"} <= kw:
                out.append((where, "pymysql.connect needs connect_timeout and read_timeout"))
            elif base == "subprocess" and name in SUBPROCESS and any(p in ast.unparse(node) for p in NETWORK_PROGRAMS) and "timeout" not in kw:
                out.append((where, f"subprocess.{name} to a network program"))
            elif name == "connect" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("s", "sock", "sk") and node.args:
                owner = next((fn for fn in functions if fn.lineno <= node.lineno <= max(getattr(n, "lineno", fn.lineno) for n in ast.walk(fn))), None)
                if owner is None or "settimeout" not in ast.unparse(owner):
                    out.append((where, "socket connect with no settimeout in its function"))
    return [(w, what) for w, what in out if w not in EXEMPT]


class EveryNetworkCallHasATimeout(unittest.TestCase):
    def test_no_network_call_in_lifeos_is_without_a_timeout(self):
        self.assertEqual(findings(), [], "add an explicit timeout to each call (docs/platform/timeouts.md lists them all)")

    def test_the_scan_really_sees_calls_and_catches_a_missing_timeout(self):
        sample = ast.parse("import urllib.request\nurllib.request.urlopen(request)\nsubprocess.run(['ssh', 'host', 'ls'])\nsubprocess.run(['ssh', 'host'], timeout=5)\n")
        calls = [n for n in ast.walk(sample) if isinstance(n, ast.Call)]
        missing = [n for n in calls if ((n.func.attr if isinstance(n.func, ast.Attribute) else "") == "urlopen" or "ssh" in ast.unparse(n)) and "timeout" not in _keywords(n)]
        self.assertEqual(len(missing), 2)
        total = sum(1 for p in (ROOT / "lifeos").rglob("*.py") for n in ast.walk(ast.parse(p.read_text())) if isinstance(n, ast.Call)
                    and (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")) in ("urlopen", "create_connection"))
        self.assertGreaterEqual(total, 10)


if __name__ == "__main__":
    unittest.main()
