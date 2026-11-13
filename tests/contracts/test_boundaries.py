"""Ownership boundaries (D17): platform knows nothing about jobs, jobs knows nothing about any one source,
and the same opening is the same row whichever producer emits it."""
import ast
from pathlib import Path
import unittest
from unittest import mock

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


APPS = {"lifeos.sources.apps", "lifeos.sources.close_app"}      # the Oracle Close service's screens and its front door; every other lifeos/sources module is a stage, a registry or a proof


def sources_targets(source, module):
    """Every dotted lifeos.sources module `source` imports, relative imports resolved from its own dotted name `module`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            bases = [(a.name, ()) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(filter(None, [*module.split(".")[:-node.level], base]))
            bases = [(base, [a.name for a in node.names])]
        else:
            continue
        for base, names in bases:
            for target in [base, *(f"{base}.{n}" for n in names)]:
                if target.startswith("lifeos.sources"):
                    yield target


def is_app(name):
    return any(name == a or name.startswith(a + ".") for a in APPS)


def app_boundary_breaks(path):
    """(file, import) pairs where an app imports a stage, or a stage imports an app. An app may use platform, the OS packages and other apps."""
    module = ".".join(path.relative_to(ROOT.parent).with_suffix("").parts)
    app = is_app(module)
    for target in sources_targets(path.read_text(), module):
        if target != "lifeos.sources" and app != is_app(target) and target.count(".") > 1:
            yield path.name, target


class BoundaryTests(unittest.TestCase):
    def test_apps_and_stages_do_not_import_each_other(self):
        bad = [b for path in (ROOT / "sources").rglob("*.py") for b in app_boundary_breaks(path)]
        self.assertEqual(bad, [], f"an app imports a stage, or a stage imports an app: {bad}")

    def test_the_app_rule_names_the_import_that_breaks_it(self):
        fake = ROOT / "sources" / "apps" / "fake_app.py"
        with mock.patch.object(Path, "read_text", return_value="from lifeos.sources.pipeline import hiring\nfrom lifeos.sources import web\nimport lifeos.sources.apps.signed\nfrom .signed import mac\n"):
            self.assertEqual(list(app_boundary_breaks(fake)), [("fake_app.py", "lifeos.sources.pipeline"), ("fake_app.py", "lifeos.sources.pipeline.hiring"), ("fake_app.py", "lifeos.sources.web")])
        stage = ROOT / "sources" / "web" / "fake_stage.py"
        with mock.patch.object(Path, "read_text", return_value="from lifeos.sources.apps import signed\nfrom lifeos.sources import close_app\n"):
            self.assertEqual(list(app_boundary_breaks(stage)), [("fake_stage.py", "lifeos.sources.apps"), ("fake_stage.py", "lifeos.sources.apps.signed"), ("fake_stage.py", "lifeos.sources.close_app")])

    def test_imports_only_point_downward(self):
        for layer in LAYERS:
            bad = [(f, to) for f, to in layer_imports(layer) if to != layer and to not in allowed(layer)]
            self.assertEqual(bad, [], f"{layer} must not import {sorted({t for _, t in bad})}")

    def test_the_same_opening_has_one_identity(self):
        self.assertEqual(identity.fuzzy_key("Acme, Inc.", "Project  Manager", "Remote"),
                         identity.fuzzy_key("ACME INC", "project manager", "remote"))


if __name__ == "__main__":
    unittest.main()
