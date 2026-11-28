"""the full suite is refused locally and runs on CI; agents run tests through scripts/test-local only."""

import os
import pathlib
import subprocess
import unittest
from unittest import mock

from tests import quickselect, testindex

ROOT = pathlib.Path(__file__).resolve().parents[2]


def sh(*args, env=None):
    full = {k: v for k, v in os.environ.items() if k != "CI"}
    full.update(env or {})
    return subprocess.run(["bash", *args], cwd=ROOT, capture_output=True, text=True, env=full, timeout=120)


class FullSuiteRefusedLocally(unittest.TestCase):
    def test_test_sh_all_is_refused_without_ci_and_points_to_the_wrapper(self):
        result = sh("tools/test.sh", "--all", "--list")
        self.assertEqual(result.returncode, 4)
        self.assertIn("scripts/test-local", result.stderr)

    def test_test_sh_all_is_allowed_when_ci_is_set(self):
        result = sh("tools/test.sh", "--all", "--list", env={"CI": "true"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_wrapper_takes_no_option_but_list(self):
        for bad in ("--all", "tests.platform", "-k"):
            result = sh("scripts/test-local", bad)
            self.assertEqual(result.returncode, 4, bad)
            self.assertIn("CI's job", result.stderr)

    def test_the_wrapper_is_executable_and_prints_the_receipt_line(self):
        text = (ROOT / "scripts/test-local").read_text()
        self.assertIn("Local tests: ", text)
        self.assertTrue(os.access(ROOT / "scripts/test-local", os.X_OK))


class QuickSelectOnAnUnplaceableChange(unittest.TestCase):
    def test_locally_only_the_contracts_and_scanners_run_never_every_module(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CI", None)
            picked = quickselect.quick_targets(["pyproject.toml"])
        everything = sorted(testindex.module_set(ROOT))
        self.assertLess(len(picked), len(everything) // 2)
        self.assertTrue(all(m.startswith("tests.contracts") or m in everything for m in picked))

    def test_on_ci_the_same_change_runs_every_module(self):
        with mock.patch.dict(os.environ, {"CI": "true"}):
            self.assertEqual(quickselect.quick_targets(["pyproject.toml"]), sorted(testindex.module_set(ROOT)))


if __name__ == "__main__":
    unittest.main()
