"""the parked packages (job search and Interview OS) are only reachable through lifeos/sources and the top-level entry
points.
"""

import ast
import pathlib
import unittest

from tests import testindex

ROOT = pathlib.Path(__file__).resolve().parents[2]
LIFEOS = ROOT / "lifeos"
PARKED = ("jobs", "interview", "hiring", "recruiters", "megibow")
ALLOWED_IMPORTERS = {"sources", "(top)"}


def _package(path):
    parts = path.relative_to(LIFEOS).parts
    return parts[0] if len(parts) > 1 else "(top)"


def _targets(node, path):
    """> the lifeos packages a statement imports, absolute or relative."""
    names = []
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
        base = node.module or ""
        if node.level:
            here = list(path.relative_to(ROOT).with_suffix("").parts)[:-1]
            here = here[: len(here) - (node.level - 1)] if node.level > 1 else here
            base = ".".join(here + ([node.module] if node.module else []))
        names = [base] + [base + "." + alias.name for alias in node.names]
    found = set()
    for name in names:
        parts = name.split(".")
        if parts[0] == "lifeos" and len(parts) > 1:
            found.add(parts[1])
    return found


def importers():
    """> {parked package: sorted packages outside it that import it}."""
    found = {p: set() for p in PARKED}
    for path in LIFEOS.rglob("*.py"):
        own = _package(path)
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            for target in _targets(node, path):
                if target in found and target != own:
                    found[target].add(own)
    return {p: sorted(v) for p, v in found.items()}


class ParkedBoundary(unittest.TestCase):
    def test_every_parked_package_exists(self):
        for package in PARKED:
            self.assertTrue(
                (LIFEOS / package).is_dir(),
                "%s is declared parked but lifeos/%s is gone: update PARKED" % (package, package),
            )

    def test_parked_packages_are_imported_only_from_sources_and_the_entry_points(self):
        for package, found in importers().items():
            outside = [p for p in found if p not in ALLOWED_IMPORTERS and p not in PARKED]
            self.assertEqual(
                [],
                outside,
                "lifeos/%s is imported from %s: its tests must run when those change, so it is no longer parked (LIFE-772)"
                % (package, outside),
            )

    def test_a_change_outside_the_boundary_selects_no_parked_test(self):
        """Selector behaviour with an index that maps each production file to its own mirror tests, which is what
        coverage gives for code that does not import a.
        """
        modules = testindex.test_modules(ROOT)
        ids = sorted(modules)
        covers = {}
        for path in testindex.production_files(ROOT):
            package = path.split("/")[0] if "/" in path else None
            if package in PARKED or package == "platform":
                continue
            covers[path] = [i for i, t in enumerate(ids) if package and t.startswith("tests.%s." % package)]
        index = {
            "schema": testindex.SCHEMA,
            "generated_at": "2026-11-16T00:00:00Z",
            "commit": "x",
            "tests": ids,
            "modules": {},
            "skipped": [],
            "covers": covers,
            "uncovered": [],
        }
        checked = 0
        for path in sorted(covers):
            if "/" not in path:
                continue
            picked = testindex.select(["lifeos/" + path], index, ROOT)
            self.assertNotEqual(testindex.FULL, picked, path)
            leaked = [t for t in picked if t.split(".")[1] in PARKED]
            self.assertEqual([], leaked, "a change to lifeos/%s selected parked tests %s" % (path, leaked[:3]))
            checked += 1
        self.assertGreater(checked, 50)

    def test_a_shared_path_change_still_runs_everything(self):
        for path in ("lifeos/platform/due.py", ".github/workflows/domains.yml"):
            self.assertEqual(
                testindex.FULL,
                testindex.select(
                    [path],
                    {
                        "schema": testindex.SCHEMA,
                        "generated_at": "2026-11-16T00:00:00Z",
                        "tests": [],
                        "modules": {},
                        "skipped": [],
                        "covers": {},
                        "uncovered": [],
                    },
                    ROOT,
                ),
                path,
            )


if __name__ == "__main__":
    unittest.main()
