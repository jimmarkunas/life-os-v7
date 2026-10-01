"""Ownership boundaries (D17): platform knows nothing about jobs, jobs knows nothing about any one source,
and the same opening is the same row whichever producer emits it."""
import ast
from pathlib import Path
import unittest

from lifeos.jobs import identity

ROOT = Path(__file__).resolve().parents[2] / "lifeos"
LAYERS = sorted(p.name for p in ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists())


def allowed(layer):
    """platform imports nothing; producers (sources) may use platform and any OS; every other layer (an OS such as
    jobs, finance, ...) may use platform only. A new OS under lifeos/ is covered automatically."""
    if layer == "platform":
        return set()
    return {"platform", *LAYERS} if layer == "sources" else {"platform"}


def layer_imports(layer):
    for path in (ROOT / layer).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                if name.startswith("lifeos."):
                    yield path.name, name.split(".")[1]


class BoundaryTests(unittest.TestCase):
    def test_imports_only_point_downward(self):
        for layer in LAYERS:
            bad = [(f, to) for f, to in layer_imports(layer) if to != layer and to not in allowed(layer)]
            self.assertEqual(bad, [], f"{layer} must not import {sorted({t for _, t in bad})}")

    def test_the_same_opening_has_one_identity(self):
        self.assertEqual(identity.fuzzy_key("Acme, Inc.", "Project  Manager", "Remote"),
                         identity.fuzzy_key("ACME INC", "project manager", "remote"))


if __name__ == "__main__":
    unittest.main()
