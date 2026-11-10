"""LIFE-549: the Gmail and Calendar fakes keep the real clients' method signatures."""
import unittest

from tests.kit.conformance import client_subjects as subjects
from tests.kit.conformance import runner

SPEC = runner.load("clients")


class ClientConformance(unittest.TestCase):
    def test_each_subject_matches_the_expected_signatures(self):
        for name, observe in subjects.SUBJECTS.items():
            with self.subTest(name):
                self.assertEqual(runner.problems(SPEC, name, observe), [])

    def test_negative_control_a_fake_that_loses_a_parameter_is_caught(self):
        def drifted(case):
            out = subjects.SUBJECTS["fake_gmail"](case)
            if out["params"]:
                out["params"] = out["params"][:-1] or ["x"]
            return out
        self.assertTrue(runner.problems(SPEC, "fake_gmail", drifted))


if __name__ == "__main__":
    unittest.main()
