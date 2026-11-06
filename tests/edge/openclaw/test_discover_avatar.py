"""LIFE-331: the OpenClaw avatar discovery is READ-ONLY and never prints secret-shaped lines."""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCRIPT = os.path.join(ROOT, "lifeos", "edge", "openclaw", "discover_avatar.sh")
CONFIG = """{
  // a comment
  "gateway": { "auth": { "token": "SECRETVALUE-123" }, "port": 18789 },
  "agents": { "list": [ { "id": "main", "workspace": "~/.openclaw/workspace",
      "identity": { "name": "OpenClaw", "theme": "lobster", "emoji": "x", "avatar": "avatars/openclaw.png" } } ] },
  "channels": { "discord": { "botToken": "DISCORDSECRET" } },
  "model": "anthropic/claude"
}
"""


def tree_hash(path):
    digest = hashlib.sha256()
    for folder, dirs, files in sorted(os.walk(path)):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(folder, name)
            digest.update(full.encode())
            digest.update(open(full, "rb").read())
    return digest.hexdigest()


class Discover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        state = os.path.join(self.home, ".openclaw")
        ws = os.path.join(state, "workspace")
        os.makedirs(os.path.join(ws, "avatars"))
        with open(os.path.join(state, "openclaw.json"), "w") as handle:
            handle.write(CONFIG)
        with open(os.path.join(ws, "IDENTITY.md"), "w") as handle:
            handle.write("- Name: OpenClaw\n- Emoji: x\n- Avatar: avatars/openclaw.png\n- Secret token: LEAKME\n")
        with open(os.path.join(ws, "avatars", "openclaw.png"), "wb") as handle:
            handle.write(b"\x89PNG" + b"0" * 100)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        fake = os.path.join(self.bin, "openclaw")
        with open(fake, "w") as handle:
            handle.write("#!/bin/bash\necho 'openclaw 9.9.9-test'\n")
        os.chmod(fake, 0o755)

    def run_script(self, *args, env=None):
        e = dict(os.environ, OPENCLAW_BIN=os.path.join(self.bin, "openclaw"))
        e.update(env or {})
        done = subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=e)
        return done.returncode, done.stdout + done.stderr

    def test_it_shows_the_avatar_lines_the_folder_and_the_version_and_changes_nothing(self):
        before = tree_hash(self.home)
        status, out = self.run_script("--home", self.home)
        self.assertEqual(status, 0, out)
        self.assertIn("openclaw 9.9.9-test", out)
        self.assertIn('"avatar": "avatars/openclaw.png"', out)
        self.assertIn("workspace", out)
        self.assertIn("openclaw.png", out)
        self.assertIn("nothing was changed", out)
        self.assertEqual(tree_hash(self.home), before)

    def test_no_secret_shaped_line_and_no_unrelated_config_line_is_printed(self):
        _, out = self.run_script("--home", self.home)
        for banned in ("SECRETVALUE", "DISCORDSECRET", "LEAKME", "botToken", "anthropic/claude", "18789"):
            self.assertNotIn(banned, out, banned)

    def test_missing_folders_stop_politely(self):
        status, out = self.run_script("--home", os.path.join(self.tmp, "nobody"))
        self.assertEqual(status, 0)
        self.assertIn("no home folder", out)
        os.makedirs(os.path.join(self.tmp, "empty"))
        status, out = self.run_script("--home", os.path.join(self.tmp, "empty"))
        self.assertEqual(status, 0)
        self.assertIn("no .openclaw folder", out)

    def test_unknown_argument_is_refused(self):
        status, _ = self.run_script("--apply")
        self.assertEqual(status, 2)

    def test_the_script_contains_no_command_that_writes_or_restarts(self):
        code = "\n".join(l for l in open(SCRIPT).read().splitlines() if not l.lstrip().startswith("#"))
        for pattern in (r"\bsed\s+-i", r"\bcp\b", r"\bmv\b", r"\brm\b", r"\bchmod\b", r"\bchown\b", r"\btee\b", r"systemctl", r"\bkill\b", r"\bmkdir\b",
                        r"\btouch\b", r"openclaw\s+(config|agents|gateway)", r">\s*[\"$/.\w]"):
            self.assertIsNone(re.search(pattern, code.replace("2>/dev/null", "").replace(">&", "").replace("2>&1", "")), pattern)
        self.assertEqual(subprocess.run(["bash", "-n", SCRIPT]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
