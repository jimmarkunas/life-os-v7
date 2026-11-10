"""LIFE-548: the committed test index (docs/test-index.json) must match the code, on EVERY pull request, not only in the hourly run.

When a pull request adds, renames or deletes a production file under lifeos/ without regenerating the index, this fails and says how to fix it:
  PYTHONPATH=<a folder with coverage installed> python -m tests.testindex build     (about 3 minutes), then commit docs/test-index.json
Age is NOT checked here (a pull request must not fail because the index aged); the hourly full run checks age and the same shape (tests/test_testindex.py CommittedIndexIsFresh)."""
import copy
import unittest

from tests import testindex as ti


class IndexMatchesTheCode(unittest.TestCase):
    def test_every_production_file_is_in_the_index_and_nothing_in_it_is_gone(self):
        problems = ti.check(ti.load(), max_age_days=10 ** 6)
        self.assertEqual(problems, [], "docs/test-index.json is out of date: run `python -m tests.testindex build` and commit it. " + "; ".join(problems))

    def test_negative_control_a_new_production_file_the_index_does_not_know_is_caught(self):
        index = copy.deepcopy(ti.load())
        victim = sorted(index["covers"])[0]
        del index["covers"][victim]
        problems = ti.check(index, max_age_days=10 ** 6)
        self.assertEqual(len(problems), 1)
        self.assertIn("not in the index", problems[0])
        self.assertIn(victim, problems[0])

    def test_negative_control_a_deleted_production_file_still_listed_is_caught(self):
        index = copy.deepcopy(ti.load())
        index["covers"]["jobs/a_file_that_was_deleted.py"] = [0]
        problems = ti.check(index, max_age_days=10 ** 6)
        self.assertEqual(len(problems), 1)
        self.assertIn("no longer exist", problems[0])

    def test_negative_control_an_unreadable_index_is_caught(self):
        self.assertEqual(ti.check(None), ["the test index is missing or unreadable"])


if __name__ == "__main__":
    unittest.main()
