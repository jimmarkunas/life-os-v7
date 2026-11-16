"""LIFE-763: the quick gate always carries the contracts and the repo-wide scanners, maps a changed file to the tests that name it, splits into processes without losing or repeating a module, and never grows to the whole suite."""
import unittest

from tests import quickselect, testindex


class QuickSelect(unittest.TestCase):
    def test_docs_only_runs_the_contracts_and_the_scanners(self):
        got = set(quickselect.quick_targets(["docs/platform/anything.md"]))
        self.assertTrue(set(testindex.contract_modules()) <= got)
        self.assertTrue({m for m in testindex.REPO_WIDE if m in testindex.module_set()} <= got)

    def test_a_changed_platform_module_adds_its_named_tests_and_importers_but_not_everything(self):
        got = set(quickselect.quick_targets(["lifeos/platform/jira.py"]))
        everything = testindex.module_set()
        self.assertTrue(any("jira" in m for m in got - set(testindex.contract_modules()) - set(testindex.REPO_WIDE)), "a test that names lifeos.platform.jira must be picked")
        self.assertLess(len(got), len(everything) // 2, "a shared-path change must not escalate to the whole suite")

    def test_a_changed_test_module_runs_itself(self):
        self.assertIn("tests.contracts.test_quickselect", quickselect.quick_targets(["tests/contracts/test_quickselect.py"]))

    def test_partition_covers_each_module_exactly_once(self):
        modules = quickselect.quick_targets(["lifeos/platform/jira.py", "lifeos/inbox/model.py"])
        for n in (1, 3, 8):
            groups = quickselect.partition(modules, n)
            flat = [m for g in groups for m in g]
            self.assertEqual(sorted(flat), sorted(set(modules)))
            self.assertLessEqual(len(groups), n)

    def test_every_target_is_a_real_test_module(self):
        known = testindex.module_set()
        for change in (["lifeos/run.py"], ["lifeos/inbox/model.py"], [".github/workflows/hourly.yml"], ["tools/test.sh"]):
            self.assertTrue(set(quickselect.quick_targets(change)) <= known)


if __name__ == "__main__":
    unittest.main()
