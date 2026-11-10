"""LIFE-549: the Notion block fake conforms to the recorded response shapes."""
import unittest

from tests.kit.conformance import notion_subjects as subjects
from tests.kit.conformance import runner

SPEC = runner.load("notion")


class NotionConformance(unittest.TestCase):
    def test_the_recorded_real_shapes_satisfy_every_case(self):
        self.assertEqual(runner.problems(SPEC, "recording", subjects.observe_recording), [])

    def test_the_block_fake_returns_the_same_shapes(self):
        self.assertEqual(runner.problems(SPEC, "fake_blocks", subjects.observe_fake_blocks), [])

    def test_negative_control_a_fake_that_drops_a_field_is_caught(self):
        def drifted(case):
            out = subjects.observe_fake_blocks(case)
            if case["action"] == "list":
                out["missing_keys"] = ["next_cursor"]                         # a fake whose page listing forgot the cursor
            return out
        self.assertTrue(runner.problems(SPEC, "fake_blocks", drifted))


if __name__ == "__main__":
    unittest.main()
