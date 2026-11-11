"""LIFE-340: model.base_url points at OpenRouter while the provider is openai-codex. A temp config shaped like the real one; nothing real is touched."""
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from tests.kit import this_python

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "hermes", "fix_model_base_url.sh")
OLD, NEW = "https://openrouter.ai/api/v1", "https://chatgpt.com/backend-api/codex"
CONFIG = """# Hermes config
model:
  default: gpt-6-luna
  context_length: 64000
  provider: openai-codex
  base_url: https://openrouter.ai/api/v1   # set during an earlier switch
  api_key: SECRET-SHOULD-NEVER-PRINT

auxiliary:
  compression:
    provider: openai-codex
    model: gpt-6-luna

other:
  base_url: https://openrouter.ai/api/v1
"""


def sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class Fix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "hermes")
        os.makedirs(self.home)
        self.conf = os.path.join(self.home, "config.yaml")
        self.write(CONFIG)
        os.chmod(self.conf, 0o640)
        self.backup = os.path.join(self.tmp, "backup")

    def write(self, text):
        with open(self.conf, "w") as handle:
            handle.write(text)

    def run_script(self, *args):
        done = subprocess.run(["bash", SCRIPT, "--home", self.home, "--backup", self.backup, *args], capture_output=True, text=True, env=this_python.env())
        return done.returncode, done.stdout + done.stderr

    def test_dry_run_names_the_change_and_writes_nothing(self):
        before = sha(self.conf)
        status, out = self.run_script()
        self.assertEqual(status, 0, out)
        self.assertIn("model.base_url  %s  ->  %s" % (OLD, NEW), out)
        self.assertNotIn("SECRET-SHOULD-NEVER-PRINT", out)
        self.assertEqual(sha(self.conf), before)
        self.assertFalse(os.path.exists(self.backup))

    def test_apply_changes_only_the_model_line_keeps_comments_mode_and_backs_up(self):
        original = open(self.conf).read()
        status, out = self.run_script("--apply")
        self.assertEqual(status, 0, out)
        self.assertNotIn("SECRET-SHOULD-NEVER-PRINT", out)
        now = open(self.conf).read()
        self.assertIn("  base_url: " + NEW, now)
        self.assertEqual(now.count(OLD), 1)                      # the unrelated 'other.base_url' keeps its value
        self.assertIn("other:\n  base_url: " + OLD, now)
        self.assertEqual(now.replace("  base_url: " + NEW, "  base_url: " + OLD + "   # set during an earlier switch"), original)
        self.assertEqual(open(os.path.join(self.backup, "config.yaml")).read(), original)
        self.assertEqual(stat.S_IMODE(os.stat(self.conf).st_mode), 0o640)
        self.assertIn("read-back OK", out)
        self.assertIn("ROLLBACK: cp -p " + os.path.join(self.backup, "config.yaml"), out)

    def test_a_second_run_finds_nothing_to_do(self):
        self.run_script("--apply")
        status, out = self.run_script("--apply")
        self.assertEqual(status, 0, out)
        self.assertIn("Nothing to do", out)

    def test_refuses_other_providers_other_values_duplicates_and_bad_input(self):
        self.write(CONFIG.replace("provider: openai-codex\n  base_url", "provider: openrouter\n  base_url"))
        self.assertEqual(self.run_script("--apply")[0], 1)
        self.write(CONFIG.replace(OLD + "   #", "https://example.com/v1   #"))
        status, out = self.run_script("--apply")
        self.assertEqual(status, 1)
        self.assertIn("not " + OLD, out)
        self.write(CONFIG.replace("  api_key: SECRET-SHOULD-NEVER-PRINT\n", "  base_url: " + OLD + "\n"))
        self.assertEqual(self.run_script("--apply")[0], 1)
        self.assertEqual(self.run_script("--to", "http://plain.example")[0], 1)
        self.assertEqual(subprocess.run(["bash", SCRIPT, "--home", os.path.join(self.tmp, "none")], capture_output=True, text=True).returncode, 1)
        self.assertEqual(self.run_script("--nonsense")[0], 2)

    def test_no_restart_command_in_the_code(self):
        code = "\n".join(l for l in open(SCRIPT).read().splitlines() if not l.lstrip().startswith("#") and "echo" not in l)
        self.assertNotIn("systemctl", code)


if __name__ == "__main__":
    unittest.main()
