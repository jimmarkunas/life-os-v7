"""The CI step `ruff format --check lifeos tools` fails on an unformatted file and passes on a formatted one, and it is
in the unit-tests job.
"""

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HAS_RUFF = (
    importlib.util.find_spec("ruff") is not None
    or subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True).returncode == 0
)


def check(source):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "sample.py").write_text(source)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--check",
                "--no-cache",
                "--config",
                str(ROOT / "pyproject.toml"),
                tmp,
            ],
            capture_output=True,
        ).returncode


@unittest.skipUnless(HAS_RUFF, "ruff is not installed here; CI installs the pinned version")
class FormatCheck(unittest.TestCase):
    def test_an_unformatted_file_fails_and_a_formatted_one_passes(self):
        self.assertEqual(check("x = {'a':1,'b':2}\n"), 1)
        self.assertEqual(check("x = {'a': 1, 'b': 2}\n"), 0)


class WorkflowStep(unittest.TestCase):
    def test_the_unit_tests_job_runs_the_format_check_on_lifeos_and_tools(self):
        text = (ROOT / ".github" / "workflows" / "pr-tests.yml").read_text()
        self.assertIn("python -m ruff format --check --no-cache lifeos tools", text)


if __name__ == "__main__":
    unittest.main()
