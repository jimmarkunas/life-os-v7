"""LIFE-595: the docstring standard (lifeos/platform/docstyle.py): the rules, the pilot package that already follows it, and the rest of the repo counted but never failed."""
import unittest
from pathlib import Path

from lifeos.platform import docstyle

ROOT = Path(__file__).resolve().parents[2]
GOOD = '"""One line that says what the module is for.\n\nInputs: a thing. Outputs: another thing."""\n'


class Rules(unittest.TestCase):
    def test_a_conforming_module_has_no_problem(self):
        self.assertEqual(docstyle.problems(GOOD), [])

    def test_each_rule_has_its_own_fixed_code(self):
        self.assertEqual(docstyle.problems("x = 1\n"), ["NO_DOCSTRING"])
        self.assertEqual(docstyle.problems('"""First line\ncontinues here.\n\nBody."""\n'), ["FIRST_PARAGRAPH_NOT_ONE_LINE"])
        self.assertEqual(docstyle.problems('"""' + "w " * 90 + '\n\nBody."""\n'), ["FIRST_LINE_TOO_LONG"])
        self.assertEqual(docstyle.problems('"""Short line.\n\n' + "x" * docstyle.TOTAL_MAX + '"""\n'), ["TOTAL_TOO_LONG"])

    def test_the_limit_is_exact(self):
        line = "a" * docstyle.FIRST_LINE_MAX
        self.assertEqual(docstyle.problems(f'"""{line}"""\n'), [])
        self.assertEqual(docstyle.problems(f'"""{line}b"""\n'), ["FIRST_LINE_TOO_LONG"])

    def test_a_package_init_needs_a_one_line_summary_and_all(self):
        self.assertEqual(docstyle.problems('"""The summary."""\n\n__all__ = ["a"]\n', is_init=True), [])
        self.assertEqual(docstyle.problems('"""The summary."""\n', is_init=True), ["INIT_NO_ALL"])
        self.assertEqual(docstyle.problems('"""The summary.\n\nMore."""\n__all__ = []\n', is_init=True), ["INIT_NOT_ONE_LINE"])
        self.assertEqual(docstyle.problems("__all__ = []\n", is_init=True), ["NO_DOCSTRING"])


class PilotPackage(unittest.TestCase):
    def test_every_enforced_package_follows_the_standard(self):
        for package in docstyle.ENFORCED:
            self.assertEqual(docstyle.check(ROOT / package), {}, package)

    def test_negative_control_a_regression_in_a_pilot_file_would_be_found(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "mcp.py"
            copy.write_text((ROOT / "lifeos/buzz/mcp.py").read_text().replace('"""Remote MCP server for Buzz', '"""' + "padding " * 30 + "Remote MCP server for Buzz", 1))
            self.assertIn("FIRST_LINE_TOO_LONG", docstyle.check(copy)[str(copy)])

    def test_the_code_map_lists_the_package_summary(self):
        page = (ROOT / "docs" / "CODEMAP.md").read_text()
        self.assertIn("- `lifeos/buzz/` (4 modules): The Buzz remote MCP", page)


class RestOfTheRepoIsOnlyCounted(unittest.TestCase):
    def test_the_advisory_count_runs_over_every_package_and_never_fails(self):
        total = 0
        for package in sorted(p for p in (ROOT / "lifeos").iterdir() if p.is_dir() and (p / "__init__.py").exists()):
            total += len(docstyle.check(package))
        self.assertIsInstance(total, int)                                                      # the number goes down as packages are done; the last PR makes ENFORCED every package


if __name__ == "__main__":
    unittest.main()
