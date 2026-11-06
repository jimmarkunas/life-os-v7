"""LIFE-348: the OpenClaw persona install. Runs the real script against a temporary home."""
import getpass
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCRIPT = os.path.join(ROOT, "lifeos", "edge", "openclaw", "deploy_persona.sh")
REAL_SRC = os.path.join(ROOT, "lifeos", "edge", "openclaw", "persona", "SOUL.md")
OLD = "# SOUL.md\n\nBe helpful, direct and competent. When Jim asks you to act, execute within the approved scope. Never send half-baked outbound communication.\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Persona(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        self.ws = os.path.join(self.home, ".openclaw", "workspace")
        os.makedirs(self.ws)
        self.dest = os.path.join(self.ws, "SOUL.md")
        self.identity = os.path.join(self.ws, "IDENTITY.md")
        self.config = os.path.join(self.home, ".openclaw", "openclaw.json")
        self.write(self.dest, OLD)
        self.write(self.identity, "- Avatar: avatars/openclaw.png\n")
        self.write(self.config, '{"keep": "me"}')
        self.backup = os.path.join(self.tmp, "backup")

    def write(self, path, text):
        with open(path, "w") as handle:
            handle.write(text)

    def read(self, path):
        with open(path) as handle:
            return handle.read()

    def run_script(self, *args, src=REAL_SRC):
        done = subprocess.run(["bash", SCRIPT, "--src", src, "--home", self.home, "--backup", self.backup, "--user", getpass.getuser(), *args],
                              capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_the_repo_persona_keeps_the_old_rules_is_short_and_has_no_secret_or_profanity(self):
        text = self.read(REAL_SRC)
        self.assertLessEqual(len(text.encode()), 8000)
        self.assertIn("approved scope", text)
        self.assertIn("Never send half-baked", text)
        self.assertIn("ONE clear next step", text)
        self.assertIn("Draft, do not send", text)
        self.assertNotIn("—", text)                                            # no em dashes, as the persona itself asks
        for word in ("fuck", "shit", "asshole"):
            self.assertNotIn(word, text.lower())

    def test_dry_run_changes_nothing_and_never_shows_the_current_text(self):
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertEqual(self.read(self.dest), OLD)
        self.assertFalse(os.path.exists(self.backup))
        self.assertNotIn("Be helpful, direct", out)

    def test_apply_backs_up_installs_reads_back_and_leaves_everything_else_alone(self):
        code, out = self.run_script("--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(sha(self.read(self.dest).encode()), sha(self.read(REAL_SRC).encode()))
        self.assertEqual(self.read(os.path.join(self.backup, "SOUL.md")), OLD)
        self.assertIn("read-back OK", out)
        self.assertIn("ROLLBACK: cp -p %s" % os.path.join(self.backup, "SOUL.md"), out)
        self.assertEqual(self.read(self.identity), "- Avatar: avatars/openclaw.png\n")
        self.assertEqual(self.read(self.config), '{"keep": "me"}')
        self.assertIn("Nothing was restarted", out)

    def test_a_second_apply_is_a_no_op(self):
        self.run_script("--apply")
        shutil.rmtree(self.backup)
        code, out = self.run_script("--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("SAME", out)
        self.assertFalse(os.path.exists(self.backup))

    def test_it_refuses_unsafe_sources(self):
        cases = {
            "empty": "",
            "no newline": "approved scope Never send half-baked",
            "lost rules": "Be nice.\n",
            "secret": "approved scope Never send half-baked\npassword: hunter2hunter2\n",
            "too big": "approved scope Never send half-baked\n" + "x" * 9000 + "\n",
        }
        for name, text in cases.items():
            src = os.path.join(self.tmp, "bad.md")
            self.write(src, text)
            code, out = self.run_script("--apply", src=src)
            self.assertEqual(code, 1, name + out)
            self.assertIn("REFUSED", out, name)
            self.assertEqual(self.read(self.dest), OLD, name)
        link = os.path.join(self.tmp, "link.md")
        os.symlink(REAL_SRC, link)
        code, out = self.run_script("--apply", src=link)
        self.assertEqual(code, 1, out)
        self.assertIn("symbolic link", out)

    def test_it_refuses_a_missing_workspace(self):
        shutil.rmtree(self.ws)
        code, out = self.run_script("--apply")
        self.assertEqual(code, 1, out)
        self.assertIn("no workspace folder", out)

    def test_it_parses_and_touches_only_the_soul_file(self):
        subprocess.run(["bash", "-n", SCRIPT], check=True)
        text = self.read(SCRIPT)
        self.assertNotIn("systemctl", text)
        self.assertNotIn("restart", text.replace("restarts NOTHING", "").replace("Nothing was restarted", "").replace("nothing is restarted", ""))


if __name__ == "__main__":
    unittest.main()
