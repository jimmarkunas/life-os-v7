"""the quick gate always carries the contracts and the repo-wide scanners, maps a changed file to the tests that name it,
splits into processes without.
"""

import pathlib
import tempfile
import os
import unittest
from unittest import mock

from tests import quickselect, testindex


class QuickSelect(unittest.TestCase):
    def test_docs_only_runs_the_contracts_and_the_scanners(self):
        got = set(quickselect.quick_targets(["docs/platform/anything.md"]))
        self.assertTrue(set(testindex.contract_modules()) <= got)
        self.assertTrue({m for m in testindex.REPO_WIDE if m in testindex.module_set()} <= got)

    def test_a_changed_platform_module_adds_its_named_tests_and_importers_but_not_everything(self):
        got = set(quickselect.quick_targets(["lifeos/platform/jira.py"]))
        everything = testindex.module_set()
        self.assertTrue(
            any("jira" in m for m in got - set(testindex.contract_modules()) - set(testindex.REPO_WIDE)),
            "a test that names lifeos.platform.jira must be picked",
        )
        self.assertLess(len(got), len(everything) // 2, "a shared-path change must not escalate to the whole suite")

    def test_on_ci_a_change_it_cannot_place_runs_every_test_module_never_fewer(self):
        every = sorted(testindex.module_set())
        for change in (
            [".github/workflows/hourly.yml"],
            ["pyproject.toml"],
            ["tools/blob.bin"],
            ["tests/kit/clock.py"],
        ):
            with mock.patch.dict(os.environ, {"CI": "true"}):
                self.assertEqual(quickselect.quick_targets(change), every, change)

    def test_a_changed_test_module_runs_itself(self):
        self.assertIn(
            "tests.contracts.test_quickselect", quickselect.quick_targets(["tests/contracts/test_quickselect.py"])
        )

    def test_partition_covers_each_module_exactly_once(self):
        modules = quickselect.quick_targets(["lifeos/platform/jira.py", "lifeos/inbox/model.py"])
        for n in (1, 3, 8):
            groups = quickselect.partition(modules, n)
            flat = [m for g in groups for m in g]
            self.assertEqual(sorted(flat), sorted(set(modules)))
            self.assertLessEqual(len(groups), n)

    def test_every_target_is_a_real_test_module(self):
        known = testindex.module_set()
        for change in (
            ["lifeos/run.py"],
            ["lifeos/inbox/model.py"],
            [".github/workflows/hourly.yml"],
            ["tools/test.sh"],
        ):
            self.assertTrue(set(quickselect.quick_targets(change)) <= known)


def tree(files):
    """A throwaway repo root holding `files` ({relative path: text})"""
    root = pathlib.Path(tempfile.mkdtemp())
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


class ImportGraph(unittest.TestCase):
    """a change to a module reaches the tests of what imports it, so `credential_list` selects the credentials screen
    tests that CI failed on after PR 1287.
    """

    def test_every_test_that_names_a_direct_importer_of_a_changed_module_is_selected(self):
        reverse = quickselect.reverse_graph()
        references = quickselect.test_references()
        checked = 0
        for module, importers in sorted(reverse.items()):
            path = quickselect.lifeos_modules()[module]
            if not 0 < len(importers) <= 4 or path.name == "__init__.py":
                continue
            wanted = {t for t, refs in references.items() if refs & importers}
            got = set(quickselect.quick_targets([str(path.relative_to(quickselect.ROOT))]))
            self.assertTrue(wanted <= got, (module, sorted(wanted - got)))
            checked += 1
            if checked == 12:
                break
        self.assertGreater(checked, 0)

    def test_the_credential_list_reaches_the_tests_of_the_screen_that_reads_it(self):
        self.assertIn("lifeos.platform.credentials", quickselect.reverse_graph()["lifeos.platform.credential_list"])
        self.assertIn(
            "tests.platform.test_credentials", quickselect.quick_targets(["lifeos/platform/credential_list.py"])
        )

    def test_a_chain_is_followed_and_a_name_in_an_all_list_is_not_an_import(self):
        root = tree(
            {
                "lifeos/__init__.py": "",
                "lifeos/a.py": "X = 1\n",
                "lifeos/b.py": "from lifeos import a\n",
                "lifeos/c.py": "import lifeos.b\n",
                "lifeos/d.py": "def f():\n    from lifeos.c import X\n",
                "lifeos/e.py": "__all__ = ['a']\n",
            }
        )
        self.assertEqual(quickselect.reached({"lifeos.a"}, root), {"lifeos.a", "lifeos.b", "lifeos.c", "lifeos.d"})
        self.assertNotIn("lifeos.e", quickselect.reached({"lifeos.a"}, root))

    def test_a_hub_follows_its_direct_importers_only(self):
        root = tree(
            {
                "lifeos/__init__.py": "",
                "lifeos/a.py": "",
                "lifeos/b.py": "import lifeos.a\n",
                "lifeos/c.py": "import lifeos.b\n",
                "lifeos/d.py": "import lifeos.c\n",
            }
        )
        with mock.patch.object(quickselect, "HUB_CLOSURE", 2):
            self.assertEqual(quickselect.reached({"lifeos.a"}, root), {"lifeos.a", "lifeos.b"})

    def test_a_test_that_patches_a_module_by_its_dotted_name_counts_as_naming_it(self):
        root = tree(
            {
                "lifeos/__init__.py": "",
                "lifeos/a.py": "",
                "tests/test_x.py": "import unittest\nfrom unittest import mock\n\nmock.patch('lifeos.a.helper')\n",
            }
        )
        self.assertEqual(quickselect.reaching_tests({"lifeos.a"}, root), {"tests.test_x"})

    def test_a_changed_data_file_selects_the_tests_that_read_it_by_path(self):
        root = tree(
            {
                "lifeos/x/codes.json": "{}",
                "tests/test_reads.py": "PATH = 'lifeos/x/codes.json'\n",
                "tests/test_other.py": "PATH = 'lifeos/x/other.txt'\n",
            }
        )
        self.assertEqual(testindex.data_readers("lifeos/x/codes.json", root), {"tests.test_reads"})
        self.assertEqual(testindex.data_readers("lifeos/x/none.json", root), set())


if __name__ == "__main__":
    unittest.main()
