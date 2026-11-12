"""LIFE-606: Jim's nine delivery rules (LOS-119, November 11 2026) are in AGENTS.md exactly once, in order, each with its bold title."""
import pathlib
import re
import unittest

AGENTS = pathlib.Path(__file__).resolve().parents[2] / "AGENTS.md"
TITLES = ("Fewer, bigger tickets.", "One approval pass at the start.", "Measurement never waits on the hourly schedule.", "Watches live outside the epic.",
          "An epic closes only when its done-when is measured.", "Dependency pre-flight per ticket.", "Estimate in gates, not effort.",
          "One end-to-end prompt, one final receipt.", "Claude does not relay.")


class DeliveryRules(unittest.TestCase):
    def section(self):
        text = AGENTS.read_text()
        self.assertEqual(text.count("## Delivery rules for every epic"), 1)           # the rules appear once, never as a second copy
        start = text.index("## Delivery rules for every epic")
        end = text.index("\n## ", start + 5)
        return text[start:end]

    def test_the_nine_rules_are_numbered_in_order_with_their_titles(self):
        rules = re.findall(r"^(\d)\. \*\*(.+?)\*\*", self.section(), flags=re.M)
        self.assertEqual(rules, [(str(i + 1), title) for i, title in enumerate(TITLES)])

    def test_the_section_points_to_where_the_rules_were_written(self):
        self.assertIn("LOS-119", self.section())

    def test_no_title_appears_elsewhere_in_the_file(self):
        text = AGENTS.read_text()
        outside = text.replace(self.section(), "")
        for title in TITLES:
            self.assertNotIn(title, outside, title)


if __name__ == "__main__":
    unittest.main()
