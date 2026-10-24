"""Edge auto-release, run for real: install-close.sh `auto`, `install`, `rollback` against a temporary tree, with the host's tools (systemctl, tailscale, ss, ...)
and close_release.py replaced by stubs whose behaviour each test sets. The real script, bash, flock and install(1) run; nothing outside the temporary tree is touched."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
A, B, C, P = "a" * 12 + "1" * 28, "b" * 12 + "2" * 28, "c" * 12 + "3" * 28, "9" * 12

STUBS = {
    "systemctl": 'echo "systemctl $*" >> "$SIM/log"; case "$1" in is-enabled) [ "${SOCK_ENABLED:-1}" = 1 ];; *) true;; esac',
    "tailscale": 'echo "tailscale $*" >> "$SIM/log"; [ "$2" = status ] && echo "{}"; true',
    "ss": 'echo "x x x 127.0.0.1:47344"',
    "stat": "echo 600:root",
    "runuser": "exit 1",
    "find": "true",
    "chown": "true",
}
REL = r'''import os, sys
E = os.environ
open(E["SIM"] + "/log", "a").write("rel " + " ".join(sys.argv[1:]) + "\n")
c = sys.argv[1]
body = lambda d: open(os.path.join(d, "MANIFEST")).read().splitlines()[1:]
if c == "serve-state":
    print(E.get("SERVE", "ours"))
elif c == "latest":
    print(E["HEAD"]) if E.get("HEAD") else sys.exit("REFUSED: NOT_GREEN")
elif c == "stage":
    if E.get("STAGE_FAIL") == "1":
        sys.exit("REFUSED: stage")
    os.makedirs(sys.argv[3])
    open(sys.argv[3] + "/MANIFEST", "w").write("commit %s\n%s\n" % (sys.argv[2], E.get("BODY", "x lifeos/a.py")))
elif c == "check":
    d = os.path.realpath(sys.argv[2])
    if os.path.basename(d) in E.get("CHECK_FAIL", "").split() or not os.path.isfile(d + "/MANIFEST"):
        sys.exit("REFUSED: TREE_DIFFERS")
elif c == "same":
    sys.exit(0 if body(sys.argv[2]) == body(sys.argv[3]) else 1)
'''


@unittest.skipUnless(shutil.which("bash") and shutil.which("flock") and shutil.which("install"), "needs bash, flock and install(1)")
class AutoReleaseRun(unittest.TestCase):
    def setUp(self):
        self.sim = tempfile.mkdtemp()
        app, bin_ = os.path.join(self.sim, "app"), os.path.join(self.sim, "bin")
        for d in ("opt", "etc", "creds", "units", app, bin_):
            os.makedirs(os.path.join(self.sim, d), exist_ok=True)
        script = open(os.path.join(ROOT, "lifeos/edge/oracle/install-close.sh")).read()
        script = script.replace("OPT=/opt/lifeos-close; ETC=/etc/lifeos-close; CREDS=/etc/credstore.encrypted; UNITS=/etc/systemd/system",
                                'OPT=$SIM/opt; ETC=$SIM/etc; CREDS=$SIM/creds; UNITS=$SIM/units', 1)
        script = script.replace('[ "$(id -u)" = 0 ] ||', "true ||", 1)
        self.assertIn("OPT=$SIM/opt", script)
        self.script = os.path.join(app, "install-close.sh")
        open(self.script, "w").write(script)
        os.chmod(self.script, 0o755)
        open(os.path.join(app, "close_release.py"), "w").write(REL)
        for name, text in STUBS.items():
            path = os.path.join(bin_, name)
            open(path, "w").write("#!/bin/sh\n" + text + "\n")
            os.chmod(path, 0o755)
        for tree in (P, A[:12]):
            self.tree(tree)
        self.link("current", A[:12])
        self.link("previous", P)
        for f in ("etc/policy.json", "creds/lifeos-close.notion", "creds/lifeos-close.link"):
            open(os.path.join(self.sim, f), "w").write("x")
        open(os.path.join(self.sim, "log"), "w").close()
        self.env = {**os.environ, "SIM": self.sim, "PATH": bin_ + os.pathsep + os.environ["PATH"]}

    def tearDown(self):
        shutil.rmtree(self.sim, ignore_errors=True)

    def tree(self, name, commit=None):
        os.makedirs(os.path.join(self.sim, "opt", name), exist_ok=True)
        open(os.path.join(self.sim, "opt", name, "MANIFEST"), "w").write(f"commit {commit or name}\nx lifeos/a.py\n")

    def link(self, name, target):
        path = os.path.join(self.sim, "opt", name)
        if os.path.lexists(path):
            os.remove(path)
        os.symlink(os.path.join(self.sim, "opt", target), path)

    def run_(self, *args, **env):
        done = subprocess.run(["bash", self.script, *args], env={**self.env, **env}, capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout + done.stderr

    def at(self, name):
        path = os.path.join(self.sim, "opt", name)
        return os.path.basename(os.readlink(path)) if os.path.lexists(path) else None

    def etc(self, name):
        path = os.path.join(self.sim, "etc", name)
        return open(path).read().split() if os.path.exists(path) else None

    def stages(self):
        return open(os.path.join(self.sim, "log")).read().count("rel stage")

    def test_a_green_changed_commit_is_released_and_a_rerun_or_unchanged_commit_is_not(self):
        rc, out = self.run_("auto", HEAD=B, BODY="y lifeos/a.py")
        self.assertEqual((rc, self.at("current"), self.at("previous"), self.etc("auto.checked")), (0, B[:12], A[:12], [B]), out)
        self.assertFalse(os.path.exists(os.path.join(self.sim, "opt", P)))                       # only the live tree and its previous are kept
        staged = self.stages()                                                                    # the probe, then install's own proven stage
        self.assertEqual(self.run_("auto", HEAD=B, BODY="y lifeos/a.py")[0], 0)
        self.assertEqual(self.stages(), staged)                                                   # the same commit again: nothing downloaded
        rc, out = self.run_("auto", HEAD=C, BODY="y lifeos/a.py")                                # the closure did not change
        self.assertEqual((rc, self.at("current"), self.etc("auto.checked")), (0, B[:12], [C]), out)

    def test_a_failing_stage_is_retried_three_times_then_left(self):
        for attempt in (1, 2, 3):
            self.assertEqual(self.run_("auto", HEAD=B, STAGE_FAIL="1")[0], 1)
            self.assertEqual(self.etc("auto.attempts"), [B, str(attempt)])
        self.assertEqual(self.run_("auto", HEAD=B)[0], 0)
        self.assertEqual((self.stages(), self.at("current")), (3, A[:12]))

    def test_a_release_that_fails_verify_is_restored_exactly(self):
        rc, out = self.run_("auto", HEAD=B, BODY="y lifeos/a.py", CHECK_FAIL=B[:12])
        self.assertEqual((rc, self.at("current"), self.at("previous"), self.etc("auto.attempts")), (1, A[:12], P, [B, "1"]), out)
        self.assertIn("restored", out)
        self.assertIsNone(self.etc("auto.pending"))

    def test_a_release_cut_off_after_the_switch_is_proven_or_undone_whatever_main_is_now(self):
        self.tree(B[:12], B)
        self.link("current", B[:12])
        self.link("previous", A[:12])
        open(os.path.join(self.sim, "etc", "auto.pending"), "w").write(f"{B} {self.sim}/opt/{A[:12]} {self.sim}/opt/{P}\n")
        open(os.path.join(self.sim, "etc", "auto.attempts"), "w").write(f"{B} 3\n")
        rc, out = self.run_("auto", HEAD=C, BODY="y lifeos/a.py", CHECK_FAIL=B[:12])              # main moved on, and B was the third attempt
        self.assertEqual((rc, self.at("current"), self.at("previous"), self.etc("auto.pending")), (0, A[:12], P, None), out)
        self.assertEqual(self.stages(), 0)                                                        # settled first; C waits for the next tick
        self.assertIn("unverified after a cut-off run", out)

    def test_a_cut_off_release_that_verifies_stays(self):
        self.tree(B[:12], B)
        self.link("current", B[:12])
        open(os.path.join(self.sim, "etc", "auto.pending"), "w").write(f"{B} {self.sim}/opt/{A[:12]} {self.sim}/opt/{P}\n")
        self.assertEqual(self.run_("auto", HEAD=B)[0], 0)
        self.assertEqual((self.at("current"), self.etc("auto.pending")), (B[:12], None))
        self.assertEqual((self.run_("auto", HEAD=B)[0], self.etc("auto.checked")), (0, [B]))

    def test_jims_own_install_on_a_free_port_is_not_refused(self):
        rc, out = self.run_("install", B, SERVE="free", BODY="y lifeos/a.py")
        self.assertNotIn("never re-adds Serve", out)
        self.assertEqual(self.at("current"), B[:12], out)

    def test_auto_never_acts_on_a_service_that_is_not_installed_and_serving(self):
        for env in ({"SERVE": "free"}, {"SERVE": "unknown"}, {"SOCK_ENABLED": "0"}):
            rc, out = self.run_("auto", HEAD=B, BODY="y lifeos/a.py", **env)
            self.assertEqual((rc, self.at("current"), self.stages()), (0, A[:12], 0), env)

    def test_a_commit_jim_rolled_back_is_never_put_back(self):
        self.run_("auto", HEAD=B, BODY="y lifeos/a.py")
        rc, out = self.run_("rollback")
        self.assertEqual((rc, self.at("current")), (0, A[:12]), out)
        os.remove(os.path.join(self.sim, "etc", "auto.checked"))
        self.assertEqual(self.run_("auto", HEAD=B, BODY="y lifeos/a.py")[0], 0)
        self.assertEqual(self.at("current"), A[:12])

    def test_a_later_commit_carrying_the_rolled_back_code_is_not_released(self):
        self.run_("auto", HEAD=B, BODY="y lifeos/a.py")
        self.run_("rollback")
        rc, out = self.run_("auto", HEAD=C, BODY="y lifeos/a.py")                                # a docs-only commit on top of B: B's code again
        self.assertEqual((rc, self.at("current")), (0, A[:12]), out)
        rc, out = self.run_("auto", HEAD=C[:-1] + "4", BODY="z lifeos/a.py")                    # a real fix changes the code: released
        self.assertEqual(self.at("current"), C[:12], out)

    def test_every_rolled_back_code_stays_out_after_more_rollbacks(self):
        self.run_("auto", HEAD=B, BODY="y lifeos/a.py")
        self.run_("rollback")
        self.run_("rollback")                                                                     # again, with current and previous the same tree: pins nothing new
        self.assertEqual(self.run_("auto", HEAD=C, BODY="y lifeos/a.py")[0], 0)
        self.assertEqual(self.at("current"), A[:12])
        D, E = "d" * 12 + "5" * 28, "e" * 12 + "6" * 28
        self.run_("auto", HEAD=D, BODY="z lifeos/a.py")                                          # a fix is released, then rolled back too
        self.assertEqual(self.at("current"), D[:12])
        self.run_("rollback")
        self.assertEqual(self.run_("auto", HEAD=E, BODY="y lifeos/a.py")[0], 0)                  # reverting the fix brings B's code back: still out
        self.assertEqual(self.at("current"), A[:12])

    def test_jims_manual_install_is_not_undone_by_the_next_merge(self):
        self.run_("auto", HEAD=B, BODY="y lifeos/a.py")
        self.assertEqual(self.run_("install", A, BODY="x lifeos/a.py")[0], 0)                   # Jim goes back to A by hand
        self.assertEqual(self.at("current"), A[:12])
        self.assertEqual(self.run_("auto", HEAD=C, BODY="y lifeos/a.py")[0], 0)                  # a docs-only merge still carrying B's code
        self.assertEqual(self.at("current"), A[:12])
        D = "d" * 12 + "5" * 28
        self.run_("auto", HEAD=D, BODY="z lifeos/a.py")                                          # new code is released
        self.assertEqual(self.at("current"), D[:12])

    def test_code_that_was_ever_live_is_never_brought_back_however_jim_moved_away(self):
        self.run_("install", B, BODY="y lifeos/a.py")                                           # Jim installs main's head himself
        self.run_("install", A, BODY="x lifeos/a.py")                                           # and goes back by hand, not by rollback
        self.assertEqual(self.run_("auto", HEAD=C, BODY="y lifeos/a.py")[0], 0)                  # a docs-only merge still carrying B's code
        self.assertEqual(self.at("current"), A[:12])
        self.tree(B[:12], B)                                                                      # a cut-off release of B that settles as verified
        open(os.path.join(self.sim, "opt", B[:12], "MANIFEST"), "w").write(f"commit {B}\nw lifeos/a.py\n")
        self.link("current", B[:12])
        open(os.path.join(self.sim, "etc", "auto.pending"), "w").write(f"{B} {self.sim}/opt/{A[:12]} {self.sim}/opt/{P}\n")
        self.run_("auto", HEAD=B)
        self.run_("install", A, BODY="x lifeos/a.py")
        D = "d" * 12 + "5" * 28
        self.assertEqual(self.run_("auto", HEAD=D, BODY="w lifeos/a.py")[0], 0)
        self.assertEqual(self.at("current"), A[:12])

    def test_a_release_that_went_live_and_failed_never_overrides_jim(self):
        for jim in (("rollback",), ("install", P)):
            self.setUp()
            self.assertEqual(self.run_("auto", HEAD=B, BODY="y lifeos/a.py", CHECK_FAIL=B[:12])[0], 1)   # live, failed verify, restored
            rc, out = self.run_(*jim, BODY="x lifeos/a.py")                                              # Jim reacts by hand
            chosen = self.at("current")
            self.assertEqual(self.run_("auto", HEAD=B, BODY="y lifeos/a.py")[0], 0)                      # B would now verify, but it was live once
            self.assertEqual(self.at("current"), chosen, jim)

    def test_a_cut_off_between_the_two_links_puts_previous_back(self):
        self.link("previous", A[:12])                                                             # install set previous to the live tree, then the run died
        open(os.path.join(self.sim, "etc", "auto.pending"), "w").write(f"{B} {self.sim}/opt/{A[:12]} {self.sim}/opt/{P}\n")
        self.assertEqual(self.run_("auto", HEAD=B)[0], 0)
        self.assertEqual((self.at("current"), self.at("previous"), self.etc("auto.pending")), (A[:12], P, None))

    def test_an_empty_pending_file_is_cleared(self):
        open(os.path.join(self.sim, "etc", "auto.pending"), "w").close()
        rc, out = self.run_("auto", HEAD=B, BODY="y lifeos/a.py")
        self.assertEqual((rc, self.at("current"), self.etc("auto.pending")), (0, B[:12], None), out)

    def test_a_malformed_attempts_file_reads_as_none(self):
        for junk in ("garbage", f"{B} -5", f"{B} 99999999999999999999", f"{B} 3x", ""):
            open(os.path.join(self.sim, "etc", "auto.attempts"), "w").write(junk)
            rc, out = self.run_("auto", HEAD=B, STAGE_FAIL="1")
            self.assertEqual((rc, self.etc("auto.attempts")), (1, [B, "1"]), (junk, out))


if __name__ == "__main__":
    unittest.main()
