"""LIFE-605: tools/test.sh, the one local test command. It must find its own Python, say exactly what is missing when it finds none, and never install anything by itself."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "test.sh"


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TestCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "tools").mkdir()
        shutil.copy(SCRIPT, self.tmp / "tools" / "test.sh")                           # a folder with no .git, no .venv and no tests: only the script's own logic runs

    def run_script(self, *args, **env):
        base = {"PATH": "/usr/bin:/bin", "HOME": str(self.tmp)}
        return subprocess.run(["bash", str(self.tmp / "tools" / "test.sh"), *args], capture_output=True, text=True, env={**base, **env}, cwd=str(self.tmp))

    def test_no_usable_python_names_the_missing_piece_and_exits_three_without_installing_anything(self):
        done = self.run_script("--list", LIFEOS_PYTHON="/nonexistent/python")
        if done.returncode == 0:
            self.skipTest("this host has a Python 3.12 with PyYAML on /usr/bin")
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn("Python 3.12 or newer with PyYAML", done.stderr)
        self.assertIn("typed yes", done.stderr)                                       # the Oracle VM rule
        self.assertEqual(list(self.tmp.iterdir()), [self.tmp / "tools"])               # nothing was created or installed

    def test_the_chosen_python_is_reported_and_everything_runs_outside_a_git_checkout(self):
        done = self.run_script("--list", LIFEOS_PYTHON=sys.executable)
        if sys.version_info < (3, 12):
            self.assertEqual(done.returncode, 3)
            return
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed in the interpreter running the tests")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("python %d.%d" % sys.version_info[:2], done.stdout)
        self.assertIn("running everything (two parallel shards)", done.stdout)

    def test_an_unknown_option_is_refused(self):
        done = self.run_script("--frobnicate")
        self.assertEqual((done.returncode, "unknown option" in done.stderr), (2, True))


class ReceiptTemplate(unittest.TestCase):
    """LIFE-605: one receipt template with the six parts, the Jira settings page exists too."""
    ROOT = Path(__file__).resolve().parents[2]
    PARTS = ("1. Context in plain words", "2. What changed", "3. How it was verified", "4. What is unverified", "5. Next steps", "6. How to verify (click only)")

    def test_the_template_carries_all_six_parts_in_order_and_the_time_helper(self):
        text = (self.ROOT / "docs/platform/receipt-template.md").read_text()
        positions = [text.index(part) for part in self.PARTS]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("python -m lifeos.platform.ct_stamp", text)

    def test_both_pages_exist(self):
        for name in ("receipt-template.md", "agent-jira-settings.md"):
            self.assertTrue((self.ROOT / "docs/platform" / name).is_file(), name)

    def test_the_jira_settings_page_says_which_sizes_are_estimates(self):
        text = (self.ROOT / "docs/platform/agent-jira-settings.md").read_text()
        self.assertIn("estimated", text)
        self.assertIn("fields", text)


if __name__ == "__main__":
    unittest.main()
