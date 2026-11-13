"""LIFE-625: the net-size rule is in the three places agents read (pull request template, receipt template, AGENTS.md), and AGENTS.md stays under its byte cap."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND = "python -m lifeos.platform.net_size"
AGENTS_CAP_BYTES = 15360


def asks_for_net_size(template):
    return "## Net size" in template and COMMAND in template


def asks_about_cross_package(template):
    section = template.split("## Cross-package change", 1)[1] if "## Cross-package change" in template else ""
    return all(part in section for part in ("two or more packages", "why the second package had to change", '"sweep"'))


class NetSizeRule(unittest.TestCase):
    def test_the_pull_request_template_asks_for_net_size_with_the_command(self):
        self.assertTrue(asks_for_net_size((ROOT / ".github/pull_request_template.md").read_text()))

    def test_a_template_without_the_section_is_caught(self):                    # negative control: the check can fail
        self.assertFalse(asks_for_net_size("Jira: LIFE-\n\n## What changes\n\n## Evidence\n\n## Not proven yet\n"))
        self.assertFalse(asks_for_net_size("## Net size\n\nlines, counted by hand\n"))

    def test_the_pull_request_template_asks_why_a_second_package_changed_and_how_a_sweep_is_labelled(self):   # LIFE-629: makes Jim's 1-in-5 cross-package target measurable
        template = (ROOT / ".github/pull_request_template.md").read_text()
        self.assertTrue(asks_about_cross_package(template))
        self.assertLess(template.index("## Net size"), template.index("## Cross-package change"))

    def test_a_template_without_the_cross_package_section_or_its_prompts_is_caught(self):                     # negative control
        self.assertFalse(asks_about_cross_package("## Net size\n\n## Not proven yet\n"))
        self.assertFalse(asks_about_cross_package("## Cross-package change\n\nAny comment?\n"))

    def test_the_receipt_template_names_the_command_under_what_changed(self):
        text = (ROOT / "docs/platform/receipt-template.md").read_text()
        line = next(line for line in text.splitlines() if line.startswith("**2. What changed:**"))
        self.assertIn(COMMAND, line)

    def test_agents_md_states_the_rule_once_in_the_rules_section(self):
        text = (ROOT / "AGENTS.md").read_text()
        self.assertEqual(text.count("**Net size.**"), 1)
        rules = text.index("## Rules (each enforced by a test)")
        following = text.find("\n## ", rules + 5)
        section = text[rules:following if following != -1 else len(text)]
        self.assertIn("**Net size.**", section)
        self.assertIn(COMMAND, section)

    def test_agents_md_stays_under_the_byte_cap(self):
        self.assertLessEqual(len((ROOT / "AGENTS.md").read_bytes()), AGENTS_CAP_BYTES)


if __name__ == "__main__":
    unittest.main()
