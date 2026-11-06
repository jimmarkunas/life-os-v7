"""LIFE-339: the two empty-setting warnings. A temp config shaped like the real one (comments, the two empty keys); nothing real is touched."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "hermes", "fix_config_warnings.sh")
CONFIG = """# Hermes config
model:
  default: "gpt-6-luna"
  context_length: 64000
  api_key: SECRET-SHOULD-NEVER-PRINT

compression:
  enabled: true
  # Optional absolute server compaction trigger in input tokens.
  codex_responses_native: false
  codex_responses_compact_threshold:

  threshold: 0.50

# Maximum number of simultaneously active chat sessions. Set to null, 0, or omit to allow unlimited.
max_concurrent_sessions:

group_sessions_per_user: true
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
        done = subprocess.run(["bash", SCRIPT, "--home", self.home, "--backup", self.backup, *args], capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_dry_run_names_both_edits_and_writes_nothing(self):
        before = sha(self.conf)
        status, out = self.run_script()
        self.assertEqual(status, 0, out)
        self.assertIn("found 1 line(s)", out)
        self.assertIn("codex_responses_compact_threshold: 24000", out)
        self.assertIn("# max_concurrent_sessions:", out)
        self.assertNotIn("SECRET-SHOULD-NEVER-PRINT", out)
        self.assertEqual(sha(self.conf), before)
        self.assertFalse(os.path.exists(self.backup))

    def test_apply_makes_exactly_the_two_edits_keeps_comments_mode_and_backs_up(self):
        status, out = self.run_script("--apply")
        self.assertEqual(status, 0, out)
        text = open(self.conf).read()
        self.assertIn("  codex_responses_compact_threshold: 24000\n", text)
        self.assertIn("# max_concurrent_sessions:   # unset = unlimited", text)
        self.assertNotIn("\nmax_concurrent_sessions:", text)
        for kept in ("# Hermes config", "context_length: 64000", "# Optional absolute server compaction trigger in input tokens.", "# Maximum number of simultaneously active chat sessions.", "group_sessions_per_user: true", "threshold: 0.50"):
            self.assertIn(kept, text)
        self.assertEqual(open(os.path.join(self.backup, "config.yaml")).read(), CONFIG)
        self.assertEqual(os.stat(self.conf).st_mode & 0o777, 0o640)
        self.assertIn("read-back", out)
        self.assertIn("ROLLBACK: cp -p %s" % os.path.join(self.backup, "config.yaml"), out)
        self.assertIn("Nothing was restarted", out)
        self.assertNotIn("SECRET-SHOULD-NEVER-PRINT", out)
        try:
            import yaml
        except ImportError:
            return
        new, old = yaml.safe_load(text), yaml.safe_load(CONFIG)
        self.assertEqual(new["compression"]["codex_responses_compact_threshold"], 24000)
        self.assertNotIn("max_concurrent_sessions", new)
        del new["compression"]["codex_responses_compact_threshold"]
        del old["compression"]["codex_responses_compact_threshold"]
        del old["max_concurrent_sessions"]
        self.assertEqual(new, old)

    def test_a_second_run_finds_nothing_to_do(self):
        self.assertEqual(self.run_script("--apply")[0], 0)
        after = sha(self.conf)
        status, out = self.run_script("--apply")
        self.assertEqual(status, 0)
        self.assertIn("Nothing to do", out)
        self.assertEqual(sha(self.conf), after)

    def test_only_one_empty_setting_present_is_fixed_alone(self):
        self.write(CONFIG.replace("max_concurrent_sessions:\n", "max_concurrent_sessions: 4\n"))
        status, out = self.run_script("--apply")
        self.assertEqual(status, 0, out)
        text = open(self.conf).read()
        self.assertIn("max_concurrent_sessions: 4", text)
        self.assertIn("codex_responses_compact_threshold: 24000", text)

    def test_duplicates_bad_threshold_and_missing_config_are_refused(self):
        self.write(CONFIG + "\nmax_concurrent_sessions:\n")
        before = sha(self.conf)
        status, out = self.run_script("--apply")
        self.assertEqual(status, 1)
        self.assertIn("more than once", out)
        self.assertEqual(sha(self.conf), before)
        self.write(CONFIG)
        for bad in ("abc", "0", "12", "1e5", "-5"):
            self.assertEqual(self.run_script("--threshold", bad)[0], 1, bad)
        self.assertEqual(subprocess.run(["bash", SCRIPT, "--home", os.path.join(self.tmp, "none")], capture_output=True, text=True).returncode, 1)
        self.assertEqual(self.run_script("--nonsense")[0], 2)

    def test_no_restart_command_in_the_code(self):
        code = "\n".join(l for l in open(SCRIPT).read().splitlines() if not l.lstrip().startswith("#") and "echo" not in l)
        self.assertNotIn("systemctl", code)


if __name__ == "__main__":
    unittest.main()
