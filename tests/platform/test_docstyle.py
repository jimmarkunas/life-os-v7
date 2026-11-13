"""LIFE-595: the docstring standard (lifeos/platform/docstyle.py): the rules, and the blocking check over every package (Oracle-installed files exempt, each with a reason)."""
import unittest
from pathlib import Path

from lifeos.platform import docstyle
from lifeos.sources.devtools import codemap

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


class EveryPackage(unittest.TestCase):
    def test_every_enforced_package_follows_the_standard_except_the_exempt_files(self):
        self.assertEqual(docstyle.enforced_problems(), {})

    def test_every_package_is_enforced_so_a_new_package_cannot_skip_the_standard(self):
        packages = sorted(f"lifeos/{p.name}" for p in (ROOT / "lifeos").iterdir() if p.is_dir() and (p / "__init__.py").exists())
        self.assertEqual(sorted(docstyle.ENFORCED), packages)

    def test_an_exempt_entry_is_still_needed_and_names_a_real_file_or_folder(self):
        for key in docstyle.EXEMPT:
            target = ROOT / key
            self.assertTrue(target.exists(), key)
            if key.endswith("/"):
                continue                                                                       # a folder entry stays while the folder holds any Oracle-installed file
            self.assertTrue(docstyle.check(target), f"{key} now follows the standard: delete its EXEMPT entry")

    def test_a_file_outside_the_exempt_list_is_not_exempt(self):
        self.assertTrue(docstyle.exempt("lifeos/edge/runner/runner.py") and docstyle.exempt(ROOT / "lifeos/platform/gate.py"))
        self.assertFalse(docstyle.exempt("lifeos/platform/gate_other.py") or docstyle.exempt("lifeos/jobs/tick.py") or docstyle.exempt("lifeos/edgewise/x.py"))

    def test_negative_control_a_regression_in_a_pilot_file_would_be_found(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "mcp.py"
            copy.write_text((ROOT / "lifeos/buzz/mcp.py").read_text().replace('"""Remote MCP server for Buzz', '"""' + "padding " * 30 + "Remote MCP server for Buzz", 1))
            self.assertIn("FIRST_LINE_TOO_LONG", docstyle.check(copy)[str(copy)])

    def test_the_code_map_lists_the_package_summary(self):
        page = codemap.render()[0]
        self.assertIn("- `lifeos/buzz/` (4 modules): The Buzz remote MCP", page)


if __name__ == "__main__":
    unittest.main()
