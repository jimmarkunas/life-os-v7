"""LIFE-333: the guard installer. Everything happens in a temp folder; the real Buzz settings and guard are never touched."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCRIPT = os.path.join(ROOT, "lifeos", "edge", "buzzguard", "install.sh")
NEW = os.path.join(ROOT, "lifeos", "edge", "buzzguard", "guard.py")
OLD = "# the old test guard\nimport sys\nsys.stdout.write('old')\n"


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.dir = os.path.join(self.tmp, "LIFEOS-Buzz-Guard")
        os.makedirs(self.dir)
        self.cur = os.path.join(self.dir, "guard.py")
        with open(self.cur, "w") as handle:
            handle.write(OLD)
        self.settings = os.path.join(self.tmp, "settings.json")
        with open(self.settings, "w") as handle:
            handle.write('{"hooks": {"command": "/usr/bin/python3 -I \\"x/LIFEOS-Buzz-Guard/guard.py\\""}}')

    def run_script(self, verb, *args):
        done = subprocess.run(["bash", SCRIPT, verb, "--dir", self.dir, "--settings", self.settings, *args], capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_syntax_and_dry_run_writes_nothing(self):
        self.assertEqual(subprocess.run(["bash", "-n", SCRIPT]).returncode, 0)
        status, out = self.run_script("dry")
        self.assertEqual(status, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("Jira/GitHub changes stay OFF", out)
        self.assertEqual(open(self.cur).read(), OLD)
        self.assertEqual(sorted(os.listdir(self.dir)), ["guard.py"])

    def test_install_backs_up_installs_keeps_writes_off_and_reads_back_with_probes(self):
        status, out = self.run_script("install")
        self.assertEqual(status, 0, out)
        self.assertEqual(digest(self.cur), digest(NEW))
        names = os.listdir(self.dir)
        backups = [n for n in names if n.startswith("guard.py.bak-")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(open(os.path.join(self.dir, backups[0])).read(), OLD)
        self.assertEqual(open(os.path.join(self.dir, "writes.mode")).read().strip(), "deny")
        self.assertIn("normal Buzz message=allow", out)
        self.assertIn("file write=deny", out)
        self.assertIn("Jira comment=deny", out)
        self.assertIn("read-back OK", out)
        self.assertIn("ROLLBACK: bash install.sh rollback", out)

    @unittest.skipUnless(shutil.which("chflags"), "macOS only")
    def test_a_locked_guard_is_unlocked_replaced_and_locked_again(self):
        subprocess.run(["chflags", "uchg", self.cur], check=True)
        self.addCleanup(lambda: subprocess.run(["chflags", "-R", "nouchg", self.dir]))
        status, out = self.run_script("dry")
        self.assertIn("LOCKED (uchg)", out)
        status, out = self.run_script("install")
        self.assertEqual(status, 0, out)
        self.assertEqual(digest(self.cur), digest(NEW))
        flags = subprocess.run(["ls", "-ldO", self.cur], capture_output=True, text=True).stdout
        self.assertIn("uchg", flags)
        status, out = self.run_script("rollback")
        self.assertEqual(status, 0, out)
        self.assertEqual(open(self.cur).read(), OLD)
        self.assertIn("uchg", subprocess.run(["ls", "-ldO", self.cur], capture_output=True, text=True).stdout)

    def test_rollback_restores_the_old_guard(self):
        self.assertEqual(self.run_script("install")[0], 0)
        status, out = self.run_script("rollback")
        self.assertEqual(status, 0, out)
        self.assertEqual(open(self.cur).read(), OLD)

    def test_approvals_stop_and_status_switches(self):
        self.assertEqual(self.run_script("install")[0], 0)
        self.assertEqual(self.run_script("approvals-on")[0], 0)
        self.assertEqual(open(os.path.join(self.dir, "writes.mode")).read().strip(), "ask")
        status, out = self.run_script("status")
        self.assertIn("writes.mode: ask", out)
        self.run_script("approvals-off")
        self.assertEqual(open(os.path.join(self.dir, "writes.mode")).read().strip(), "deny")
        self.run_script("stop")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "STOP")))
        self.assertIn("STOP: ON", self.run_script("status")[1])
        self.run_script("resume")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "STOP")))

    def test_reinstall_with_approvals_on_probes_an_ask_for_jira(self):
        self.assertEqual(self.run_script("install")[0], 0)
        self.run_script("approvals-on")
        status, out = self.run_script("install")
        self.assertEqual(status, 0, out)
        self.assertIn("Jira comment=skipped", out)

    def test_pending_lists_and_approve_creates_a_single_use_approval_after_showing_the_action(self):
        self.assertEqual(self.run_script("install")[0], 0)
        self.assertIn("nothing is waiting", self.run_script("pending")[1])
        aid = "0123456789abcdef"
        os.makedirs(os.path.join(self.dir, "pending"))
        with open(os.path.join(self.dir, "pending", aid + ".json"), "w") as handle:
            handle.write('{"id": "%s", "t": "2026-11-06 12:40:00 -0600", "tool": "mcp__x__addOrEditJiraIssueComment", "why": "jira comment on LIFE", "preview": "{\\"commentBody\\": \\"hello\\"}"}' % aid)
        status, out = self.run_script("pending")
        self.assertIn(aid, out)
        self.assertIn("addOrEditJiraIssueComment", out)
        def approve(a, *flags):
            done = subprocess.run(["bash", SCRIPT, "approve", a, *flags, "--dir", self.dir, "--settings", self.settings], capture_output=True, text=True)
            return done.returncode, done.stdout + done.stderr
        status, out = approve(aid, "--yes")
        self.assertEqual(status, 0, out)
        self.assertIn("UNTRUSTED", out)
        self.assertIn("hello", out)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "approved", aid)))
        for bad in ("zzz", "0123", "0123456789ABCDEF", ""):
            self.assertEqual(approve(bad, "--yes")[0], 2, bad)
        self.assertEqual(approve("ffffffffffffffff", "--yes")[0], 1)

    def test_refusals(self):
        status, out = self.run_script("install", "--guard", os.path.join(self.tmp, "missing.py"))
        self.assertEqual(status, 1)
        self.assertIn("no new guard", out)
        broken = os.path.join(self.tmp, "broken.py")
        with open(broken, "w") as handle:
            handle.write("def (:\n")
        status, out = self.run_script("install", "--guard", broken)
        self.assertEqual(status, 1)
        self.assertIn("does not parse", out)
        self.assertEqual(open(self.cur).read(), OLD)
        os.remove(self.cur)
        status, out = self.run_script("install")
        self.assertEqual(status, 1)
        self.assertIn("no current guard", out)
        self.assertEqual(self.run_script("rollback")[0], 1)
        self.assertEqual(self.run_script("nonsense")[0], 2)

    def test_it_warns_when_the_settings_do_not_name_the_guard(self):
        with open(self.settings, "w") as handle:
            handle.write("{}")
        status, out = self.run_script("dry")
        self.assertEqual(status, 0)
        self.assertIn("WARNING", out)


if __name__ == "__main__":
    unittest.main()
