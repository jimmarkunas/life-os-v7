"""LIFE-528: the Close service tree ships only the files its CLOSURE (and the gate's own list) names, so every lifeos module those files import must be in that set.
The release tool's own import check only imports close_app and the gate, so a screen loaded later by name (the Alert settings screen) could ship without a module it needs: on Nov 10 2026
`lifeos.platform.credentials` was missing and the screen answered 503. This test reads the imports of every shipped file statically and fails on a module that is not shipped."""
import ast
import unittest
from pathlib import Path

from lifeos.edge.oracle import close_release as rel
from lifeos.sources import close_app

ROOT = Path(__file__).resolve().parents[2]
# Imports that are deliberately not shipped: each is wrapped so the screen answers a clear 503 instead of failing (close_app: "a screen this release does not ship yet").
NOT_SHIPPED_ON_PURPOSE = set()


def shipped(closure=None, edge=None):
    return set(rel.EDGE if edge is None else edge) | set(close_app.CLOSURE if closure is None else closure)


def lifeos_modules(source, known):
    """Repo files a source file imports: `import lifeos.a.b`, `from lifeos.a import b` (b a module or a name) and `from lifeos.a.b import c`."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        for name in names:
            if name.split(".")[0] != "lifeos":
                continue
            path = name.replace(".", "/") + ".py"
            if path in known or (ROOT / path).is_file():
                found.add(path)
    return found


def missing(files, ship):
    out = {}
    for path in sorted(files):
        for need in lifeos_modules((ROOT / path).read_text(), ship):
            if need not in ship and need not in NOT_SHIPPED_ON_PURPOSE and not need.endswith("/__init__.py"):
                out.setdefault(path, set()).add(need)
    return out


class ClosureIsComplete(unittest.TestCase):
    def test_every_module_a_shipped_file_imports_is_shipped(self):
        ship = shipped()
        self.assertEqual(missing(ship, ship), {})

    def test_the_alert_settings_screen_ships_the_credential_list(self):
        ship = shipped()
        for need in ("lifeos/sources/alerts_app.py", "lifeos/platform/credentials.py", "lifeos/platform/credential_list.py"):
            self.assertIn(need, ship)

    def test_negative_control_a_missing_module_is_reported(self):
        ship = shipped() - {"lifeos/platform/credentials.py"}
        self.assertIn("lifeos/platform/credentials.py", missing(ship, ship).get("lifeos/sources/alerts_app.py", set()))

    def test_every_closure_path_is_a_python_file_the_release_tool_accepts(self):
        for path in shipped():
            self.assertRegex(path, rel.PATH)                                                       # the release tool copies only .py files, so the list data lives in a .py module
            self.assertTrue((ROOT / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
