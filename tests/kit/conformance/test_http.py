"""LIFE-549: the http fakes in tests/kit conform to the real urllib (over a loopback server)."""
import unittest

from tests.kit.conformance import http_subjects as subjects
from tests.kit.conformance import runner

SPEC = runner.load("http")


class HttpConformance(unittest.TestCase):
    def test_the_real_urllib_gives_the_expected_answers_so_the_cases_are_true(self):
        with subjects.Loopback(SPEC) as server:
            self.assertEqual(runner.problems(SPEC, "real", server.observe), [])

    def test_the_fake_gives_the_same_answers_as_the_real_thing(self):
        self.assertEqual(runner.problems(SPEC, "fake", subjects.observe_fake), [])

    def test_negative_control_a_fake_that_ignores_the_read_size_is_caught(self):
        def drifted(case):
            out = subjects.observe_fake(case)
            if "partial" in out:
                out["partial"] = case["body"]                               # a fake whose read(5) returns everything
            return out
        self.assertTrue(runner.problems(SPEC, "fake", drifted))


if __name__ == "__main__":
    unittest.main()
