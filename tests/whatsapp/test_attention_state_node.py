"""LIFE-643: Attention's own node tests (lifeos/whatsapp/oracle/state.test.mjs: the unread-count rules, the answer-socket rule and the proof that no message text, number or chat id reaches the snapshot or the saved state) run in the
unit-test job too. They need only Node 20 or newer, which the runner image has; without it they skip."""
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


def node_major():
    done = subprocess.run([NODE, "-p", "process.versions.node.split('.')[0]"], capture_output=True, text=True)
    return int(done.stdout.strip() or 0)


@unittest.skipIf(NODE is None or node_major() < 20, "Node 20 or newer not installed")
class AttentionStateTests(unittest.TestCase):
    def test_the_node_tests_pass(self):
        done = subprocess.run([NODE, "--test", str(ROOT / "lifeos" / "whatsapp" / "oracle" / "state.test.mjs")], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout[-2000:])
        self.assertIn("no message text, number or chat id reaches the snapshot", done.stdout)


if __name__ == "__main__":
    unittest.main()
