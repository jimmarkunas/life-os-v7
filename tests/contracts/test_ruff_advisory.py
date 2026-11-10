"""LIFE-558: ruff is an ADVISORY check. It is configured in pyproject.toml, pinned with a hash, and its pull-request step can never fail the job or change the required check."""
import re
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:                                    # PyYAML is a test-only dependency
    yaml = None

ROOT = Path(__file__).resolve().parents[2]


class Configured(unittest.TestCase):
    def test_pyproject_holds_the_ruff_rules_and_nothing_else(self):
        text = (ROOT / "pyproject.toml").read_text()
        sections = re.findall(r"^\[([^\]]+)\]", text, flags=re.M)
        self.assertEqual(sections, ["tool.ruff", "tool.ruff.lint"])
        self.assertRegex(text, r'select = \["E", "F", "B"\]')
        self.assertIn('ignore = ["E501"]', text)                                      # existing lines run to 661 characters

    def test_ruff_is_pinned_exactly_and_hashed_in_the_test_lock(self):
        pin = re.search(r"^ruff==(\S+)$", (ROOT / "requirements-test.txt").read_text(), flags=re.M)
        self.assertIsNotNone(pin)
        block = re.search(r"^ruff==%s \\\n((?:\s+--hash=sha256:[0-9a-f]{64}\s*\\?\n?)+)" % re.escape(pin.group(1)), (ROOT / "requirements-test.lock").read_text(), flags=re.M)
        self.assertIsNotNone(block, "the lock has no hashed ruff entry at the pinned version")


@unittest.skipIf(yaml is None, "PyYAML not installed")
class StepIsAdvisory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.job = yaml.safe_load((ROOT / ".github/workflows/pr-tests.yml").read_text())["jobs"]["unit-tests"]

    def step(self):
        return next(s for s in self.job["steps"] if str(s.get("name", "")).startswith("Ruff advisory"))

    def test_the_ruff_step_cannot_fail_the_job(self):
        step = self.step()
        self.assertIs(step.get("continue-on-error"), True)
        self.assertNotIn("exit 1", step["run"])
        self.assertTrue(step["run"].rstrip().endswith("exit 0"))
        self.assertIn("--no-cache", step["run"])

    def test_the_ruff_step_runs_after_the_unit_tests_so_it_cannot_delay_their_timer(self):
        names = [str(s.get("name", "")) for s in self.job["steps"]]
        unit = next(i for i, n in enumerate(names) if n.startswith("Unit tests"))
        ruff = next(i for i, n in enumerate(names) if n.startswith("Ruff advisory"))
        self.assertGreater(ruff, unit)

    def test_the_required_check_and_its_limits_are_unchanged(self):
        self.assertIn("unit-tests", yaml.safe_load((ROOT / ".github/workflows/pr-tests.yml").read_text())["jobs"])
        unit = next(s for s in self.job["steps"] if str(s.get("name", "")).startswith("Unit tests"))
        self.assertIn("-ge 90", unit["run"])
        self.assertIn("-ge 45", unit["run"])
        self.assertIsNone(unit.get("continue-on-error"))


if __name__ == "__main__":
    unittest.main()
