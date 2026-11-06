"""LIFE-334: the Hermes compression pin. A copy of the real config's shape (comments, the auxiliary section, the model section) in a temp folder; nothing real is touched."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "hermes", "set_compression.sh")
CONFIG = """# Hermes config (comments are kept)
model:
  default: "gpt-6-luna"
  context_length: 64000   # set by hand

  provider: "openai-codex"

  base_url: "https://openrouter.ai/api/v1"
  api_key: SECRET-SHOULD-NEVER-PRINT

# =============================================================================
# Context Compression (Auto-shrinks long conversations)
compression:
  enabled: true
  threshold: 0.50

delegation:
  backend: profile_routes
auxiliary:
  title_generation:
    enabled: false
moa:
  presets:
    default:
      fanout: user_turn
"""


def sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class Pin(unittest.TestCase):
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

    def test_dry_run_shows_the_model_lines_hides_secrets_and_writes_nothing(self):
        before = sha(self.conf)
        status, out = self.run_script()
        self.assertEqual(status, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn('default: "gpt-6-luna"', out)
        self.assertIn("openrouter.ai", out)
        self.assertIn("provider: openai-codex", out)
        self.assertNotIn("SECRET-SHOULD-NEVER-PRINT", out)
        self.assertEqual(sha(self.conf), before)
        self.assertFalse(os.path.exists(self.backup))

    def test_apply_inserts_the_block_keeps_every_comment_and_setting_backs_up_and_reads_back(self):
        status, out = self.run_script("--apply")
        self.assertEqual(status, 0, out)
        text = open(self.conf).read()
        self.assertIn("auxiliary:\n  compression:\n    provider: openai-codex\n    model: gpt-6-luna\n  title_generation:\n    enabled: false", text)
        for kept in ("# Hermes config (comments are kept)", "context_length: 64000   # set by hand", "# Context Compression (Auto-shrinks long conversations)", "base_url: \"https://openrouter.ai/api/v1\"", "fanout: user_turn"):
            self.assertIn(kept, text)
        self.assertEqual(open(os.path.join(self.backup, "config.yaml")).read(), CONFIG)
        self.assertEqual(os.stat(self.conf).st_mode & 0o777, 0o640)
        self.assertRegex(out, r"read-back OK")
        self.assertIn("ROLLBACK: cp -p %s" % os.path.join(self.backup, "config.yaml"), out)
        self.assertIn("Nothing was restarted", out)
        self.assertNotIn("SECRET-SHOULD-NEVER-PRINT", out)
        try:
            import yaml
        except ImportError:
            return
        new = yaml.safe_load(text)
        old = yaml.safe_load(CONFIG)
        self.assertEqual(new["auxiliary"]["compression"], {"provider": "openai-codex", "model": "gpt-6-luna"})
        del new["auxiliary"]["compression"]
        self.assertEqual(new, old)

    def test_a_second_apply_is_refused_and_changes_nothing(self):
        self.assertEqual(self.run_script("--apply")[0], 0)
        after = sha(self.conf)
        status, out = self.run_script("--apply")
        self.assertEqual(status, 1)
        self.assertIn("already exists", out)
        self.assertEqual(sha(self.conf), after)

    def test_without_an_auxiliary_section_it_is_appended_and_without_a_final_newline_too(self):
        for text in (CONFIG.replace("auxiliary:\n  title_generation:\n    enabled: false\n", ""), CONFIG.replace("auxiliary:\n  title_generation:\n    enabled: false\n", "").rstrip("\n")):
            shutil.rmtree(self.backup, ignore_errors=True)
            self.write(text)
            status, out = self.run_script("--apply")
            self.assertEqual(status, 0, out)
            new = open(self.conf).read()
            self.assertTrue(new.rstrip("\n").endswith("auxiliary:\n  compression:\n    provider: openai-codex\n    model: gpt-6-luna"))

    def test_odd_names_and_a_missing_config_are_refused_and_other_providers_can_be_named(self):
        for bad in (["--provider", "a b"], ["--model", "x;rm"], ["--provider", ""], ["--model", "$(id)"]):
            self.assertEqual(self.run_script(*bad)[0], 1, bad)
        status, out = subprocess.run(["bash", SCRIPT, "--home", os.path.join(self.tmp, "none")], capture_output=True, text=True).returncode, ""
        self.assertEqual(status, 1)
        self.assertEqual(self.run_script("--apply", "--provider", "openrouter", "--model", "anthropic/claude-haiku")[0], 0)
        self.assertIn("provider: openrouter\n    model: anthropic/claude-haiku", open(self.conf).read())

    def test_unknown_argument_and_no_restart_command_in_the_code(self):
        self.assertEqual(self.run_script("--nonsense")[0], 2)
        code = "\n".join(l for l in open(SCRIPT).read().splitlines() if not l.lstrip().startswith("#") and "echo" not in l)
        self.assertNotIn("systemctl", code)


if __name__ == "__main__":
    unittest.main()
