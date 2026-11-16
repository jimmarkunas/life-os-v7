"""LIFE-760: tools/preflight.sh is the gate before a push, and tools/automerge.sh arms auto-merge only for a HEAD that passed it. Runs in a throwaway git repo with no network and no gh."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
GIT = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "tools").mkdir()
        for name in ("preflight.sh", "automerge.sh"):
            shutil.copy(TOOLS / name, self.tmp / "tools" / name)
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        (self.tmp / "a.txt").write_text("a")
        subprocess.run([*GIT, "add", "a.txt", "tools"], cwd=self.tmp, check=True)
        subprocess.run([*GIT, "commit", "-q", "-m", "x"], cwd=self.tmp, check=True)
        self.sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.tmp, capture_output=True, text=True).stdout.strip()
        self.marker = self.tmp / ".git" / "lifeos-preflight-ok"

    def run_tool(self, name, *args, **env):
        base = {"PATH": "/usr/bin:/bin", "HOME": str(self.tmp), "LIFEOS_AUTOMERGE_WAIT": "0"}
        return subprocess.run(["bash", str(self.tmp / "tools" / name), *args], capture_output=True, text=True, env={**base, **env}, cwd=str(self.tmp))

    def test_automerge_refuses_without_a_passing_preflight_for_this_head(self):
        done = self.run_tool("automerge.sh", "7")
        self.assertEqual(done.returncode, 4, done.stderr)
        self.assertIn("sh tools/preflight.sh", done.stderr)

    def test_a_marker_for_another_commit_does_not_count(self):
        self.marker.write_text("0" * 40 + "\n")
        self.assertEqual(self.run_tool("automerge.sh", "7").returncode, 4)

    def test_a_marker_for_this_head_gets_past_the_gate(self):
        self.marker.write_text(self.sha + "\n")
        done = self.run_tool("automerge.sh", "7")
        self.assertNotEqual(done.returncode, 4, done.stderr)               # it goes on to the gh step (no gh here, so exit 1)

    def test_the_explicit_skip_is_named_on_stderr(self):
        done = self.run_tool("automerge.sh", "7", LIFEOS_PREFLIGHT_SKIP="1")
        self.assertNotEqual(done.returncode, 4)
        self.assertIn("preflight check skipped", done.stderr)

    def test_preflight_refuses_a_dirty_tree_and_leaves_no_marker(self):
        self.marker.write_text(self.sha + "\n")
        (self.tmp / "a.txt").write_text("changed")
        done = self.run_tool("preflight.sh")
        self.assertEqual(done.returncode, 1)
        self.assertIn("uncommitted changes", done.stderr)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
