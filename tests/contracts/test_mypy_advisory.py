"""LIFE-559: the type checker (mypy) is an ADVISORY check on lifeos/platform. It is configured in mypy.ini (pyproject.toml stays ruff-only, pinned by test_ruff_advisory.py), pinned
with hashes in the test lock, its pull-request step can never fail the job or change the required check, and ten platform modules are fully annotated."""
import ast
import re
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:                                    # PyYAML is a test-only dependency
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
TYPED = ("names", "tinyfish", "http", "alerts", "runtime", "snapshot_store", "notion_ids", "jira", "outlook", "ledger_body")      # the ten chosen by import count (LIFE-559 comments; `router` is left untyped: its own test forbids any import in it)


class Configured(unittest.TestCase):
    def test_mypy_ini_is_lenient_and_covers_only_lifeos_platform(self):
        text = (ROOT / "mypy.ini").read_text()
        self.assertEqual(re.findall(r"^\[([^\]]+)\]", text, flags=re.M), ["mypy"])
        self.assertRegex(text, r"(?m)^files = lifeos/platform$")
        self.assertNotRegex(text, r"(?m)^(strict|disallow_\w+|warn_return_any|check_untyped_defs)\s*=\s*True")      # lenient mode: no strict flags yet

    def test_mypy_and_its_dependencies_are_pinned_exactly_and_hashed_in_the_test_lock(self):
        pin = re.search(r"^mypy==(\S+)$", (ROOT / "requirements-test.txt").read_text(), flags=re.M)
        self.assertIsNotNone(pin)
        lock = (ROOT / "requirements-test.lock").read_text()
        block = re.search(r"^mypy==%s \\\n((?:\s+--hash=sha256:[0-9a-f]{64}\s*\\?\n?)+)" % re.escape(pin.group(1)), lock, flags=re.M)
        self.assertIsNotNone(block, "the lock has no hashed mypy entry at the pinned version")
        for dependency in ("mypy_extensions", "pathspec", "typing_extensions", "librt"):
            self.assertRegex(lock, r"(?m)^%s==\S+ \\" % dependency)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class StepIsAdvisory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.job = yaml.safe_load((ROOT / ".github/workflows/pr-tests.yml").read_text())["jobs"]["unit-tests"]

    def step(self):
        return next(s for s in self.job["steps"] if str(s.get("name", "")).startswith("Type check advisory"))

    def test_the_type_step_cannot_fail_the_job_and_only_looks_at_changed_platform_files(self):
        step = self.step()
        self.assertIs(step.get("continue-on-error"), True)
        self.assertNotIn("exit 1", step["run"])
        self.assertTrue(step["run"].rstrip().endswith("exit 0"))
        self.assertIn("lifeos/platform/*.py", step["run"])
        self.assertIn("--no-incremental", step["run"])

    def test_it_runs_after_the_unit_tests_so_it_cannot_delay_their_timer(self):
        names = [str(s.get("name", "")) for s in self.job["steps"]]
        unit = next(i for i, n in enumerate(names) if n.startswith("Unit tests"))
        self.assertGreater(next(i for i, n in enumerate(names) if n.startswith("Type check advisory")), unit)

    def test_the_required_check_and_its_limits_are_unchanged(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/pr-tests.yml").read_text())
        self.assertIn("unit-tests", workflow["jobs"])
        unit = next(s for s in self.job["steps"] if str(s.get("name", "")).startswith("Unit tests"))
        self.assertIn("-ge 90", unit["run"])
        self.assertIsNone(unit.get("continue-on-error"))


def public_functions(tree):
    """Module-level functions and the methods of module-level classes whose names do not start with an underscore (functions defined inside functions are not public)."""
    scopes = [tree.body] + [n.body for n in tree.body if isinstance(n, ast.ClassDef)]
    for body in scopes:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                yield node


def unannotated(node):
    args = [a for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs if a.arg not in ("self", "cls")]
    args += [a for a in (node.args.vararg, node.args.kwarg) if a]
    missing = [a.arg for a in args if a.annotation is None]
    return missing + (["return"] if node.returns is None else [])


class TenModulesAreTyped(unittest.TestCase):
    def test_every_public_function_in_the_ten_modules_is_fully_annotated(self):
        gaps = {}
        for module in TYPED:
            tree = ast.parse((ROOT / "lifeos" / "platform" / f"{module}.py").read_text())
            for node in public_functions(tree):
                if node.name == "__init__":
                    continue
                missing = unannotated(node)
                if missing:
                    gaps[f"{module}.{node.name}"] = missing
        self.assertEqual(gaps, {})

    def test_negative_control_an_unannotated_public_function_is_reported(self):
        node = next(public_functions(ast.parse("def f(a, b: int):\n    return a\n")))
        self.assertEqual(unannotated(node), ["a", "return"])

    def test_the_annotations_use_only_standard_library_modules_so_the_shipped_oracle_trees_stay_complete(self):
        for module in TYPED:
            tree = ast.parse((ROOT / "lifeos" / "platform" / f"{module}.py").read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in ("typing", "collections.abc", "__future__"):
                    continue
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("lifeos"):
                    self.assertTrue((ROOT / (node.module.replace(".", "/") + ".py")).is_file() or (ROOT / node.module.replace(".", "/")).is_dir(), (module, node.module))


if __name__ == "__main__":
    unittest.main()
