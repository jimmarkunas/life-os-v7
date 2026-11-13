"""LIFE-548 / LIFE-588: the committed test index (docs/test-index.json) must be readable, and a production file the index does not list is never a failure: adding, renaming or deleting a source needs no
regeneration (about 3 minutes with coverage), because `select` sends an unlisted file to its mirror test folder, or to FULL. Age is checked only by the hourly full run (tests/test_testindex.py CommittedIndexIsFresh)."""
import os
import unittest

from tests import testindex as ti


@unittest.skipIf(os.environ.get("TESTINDEX_BUILDING"), "the index is being rebuilt by `python -m tests.testindex build`; this guard runs on every normal run")
class IndexIsUsable(unittest.TestCase):
    def test_the_committed_index_is_readable_and_has_no_other_problem_than_age(self):
        self.assertEqual(ti.check(ti.load(), max_age_days=10 ** 6), [])

    def test_a_production_file_the_index_does_not_list_still_gets_its_tests(self):
        index = ti.load()
        index["covers"].pop("sources/web/boards.py", None)
        picked = ti.select(["lifeos/sources/web/boards.py"], index)
        self.assertIn("tests.sources.web.test_boards", picked)                 # the whole mirror folder (tests/sources) runs, never nothing


if __name__ == "__main__":
    unittest.main()
