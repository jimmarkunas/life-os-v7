"""LIFE-310: the script-pack runner (lifeos/edge/runner/runner.py) and its installer, on temp folders and a fake systemctl. Nothing real is touched; no sudo."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from lifeos.edge.runner import runner

ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "lifeos" / "edge" / "runner" / "install-runner.sh"
RUNNER_PY = ROOT / "lifeos" / "edge" / "runner" / "runner.py"
TOKEN = "github_pat_NOTAREALTOKEN_0123456789abcdef"


class FakeSystemd:
    """Service state lives in flag files so a pack STEP (plain bash) can 'stop' a service; start/stop/enable/disable calls are recorded."""
    def __init__(self, folder):
        self.folder, self.calls = folder, []

    def flag(self, unit):
        return os.path.join(self.folder, unit + ".stopped")

    def __call__(self, *args):
        self.calls.append(args)
        verb, unit = args[0], args[1] if len(args) > 1 else None
        if verb == "is-active":
            return (3, "inactive") if os.path.exists(self.flag(unit)) else (0, "active")
        if verb == "is-enabled":
            return 0, "enabled"
        if verb == "start" and os.path.exists(self.flag(unit)):
            os.remove(self.flag(unit))
        if verb == "stop":
            Path(self.flag(unit)).touch()
        return 0, ""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = os.path.join(self.tmp, "state")
        self.inbox = os.path.join(self.tmp, "lifeos-pack-{key}")
        self.sysd = FakeSystemd(self.tmp)
        self.host = runner.Host(state=self.state, inbox=self.inbox, require_root=False, systemctl=self.sysd)

    def pack(self, key="LIFE-310", steps=None, restore=None, ticket=None, extra=None, timeout=None):
        """Write a pack under the inbox for `key`. steps: {name: bash text}."""
        root = self.inbox.format(key=key)
        shutil.rmtree(root, ignore_errors=True)
        steps = steps or {"10-ok.sh": "echo done\n"}
        os.makedirs(os.path.join(root, "steps"))
        for name, text in steps.items():
            Path(root, "steps", name).write_text("#!/bin/bash\n" + text)
        manifest = {"schema": 1, "ticket": ticket or key, "steps": ["steps/" + n for n in sorted(steps)], "restore": restore or {}}
        if timeout:
            manifest["step_timeout_seconds"] = timeout
        manifest.update(extra or {})
        Path(root, "pack.json").write_text(json.dumps(manifest))
        return root

    def run_cli(self, *argv, host=None):
        out = io.StringIO()
        with redirect_stdout(out):
            status = runner.main(list(argv), host or self.host)
        return status, out.getvalue()

    def sha8(self, root):
        return runner.pack_hash(root)[:8]

    def result(self, key="LIFE-310"):
        return json.loads(Path(self.state, "results", key + ".json").read_text())

    def everything_written(self):
        """All text the runner left under its state folder."""
        text = ""
        for base, _, names in os.walk(self.state):
            for name in names:
                text += Path(base, name).read_text(errors="replace")
        return text


class PackIdentity(Base):          # GWT-RUN-01
    def test_hash_is_the_full_sha256_of_names_and_bytes_not_times_or_modes(self):
        root = self.pack()
        first = runner.pack_hash(root)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        os.chmod(os.path.join(root, "steps", "10-ok.sh"), 0o755)
        os.utime(os.path.join(root, "pack.json"), (1, 1))
        self.assertEqual(runner.pack_hash(root), first)
        Path(root, "steps", "10-ok.sh").write_text("#!/bin/bash\necho changed\n")
        self.assertNotEqual(runner.pack_hash(root), first)

    def test_the_hash_command_and_the_help_print_the_same_number(self):
        root = self.pack()
        status, out = self.run_cli("hash", root)
        self.assertEqual((status, out.strip()), (0, runner.pack_hash(root)))
        status, out = self.run_cli("help")
        self.assertIn("LIFE-310  sha256 " + runner.pack_hash(root), out)
        self.assertIn("RESULT ok", out)                                      # the help explains the one-line result
        status, out = self.run_cli("check", root)
        self.assertIn("PACK ok LIFE-310 steps=1", out)

    def test_unsafe_packs_are_refused_symlink_extra_top_level_bad_name_and_bad_manifest(self):
        root = self.pack()
        os.symlink("/etc/hostname", os.path.join(root, "files-link"))
        self.assertEqual(self.run_cli("check", root)[1].strip(), "PACK fail E_PACK")
        os.remove(os.path.join(root, "files-link"))
        Path(root, "extra.txt").write_text("x")
        self.assertEqual(self.run_cli("check", root)[1].strip(), "PACK fail E_PACK")
        os.remove(os.path.join(root, "extra.txt"))
        for bad in ({"restore": {"files": ["/etc/sudoers.d/x"]}}, {"restore": {"files": ["relative/path"]}}, {"restore": {"files": ["/etc/../etc/shadow"]}},
                    {"restore": {"files": ["/opt/lifeos-runner/runner.py"]}}, {"restore": {"services": ["bad name.service"]}}, {"steps": ["../x.sh"]}, {"schema": 2},
                    {"step_timeout_seconds": 99999}, {"ticket": "lifeos-1"}):
            root = self.pack(extra=bad)
            self.assertEqual(self.run_cli("check", root)[1].strip(), "PACK fail E_PACK", bad)


class Approval(Base):              # GWT-RUN-02 and the negative tests
    def test_a_matching_sha8_and_ticket_runs_and_the_terminal_shows_exactly_one_line(self):
        marker = os.path.join(self.tmp, "ran")
        root = self.pack(steps={"10-a.sh": "echo step one noise\ntouch %s\n" % marker, "20-b.sh": "echo READBACK count=3\n"})
        status, out = self.run_cli("LIFE-310", self.sha8(root))
        self.assertEqual((status, out), (0, "RESULT ok\n"))
        self.assertTrue(os.path.exists(marker))
        data = self.result()
        self.assertEqual((data["outcome"], data["code"], data["steps_run"], data["steps_total"], data["readback"]), ("ok", None, 2, 2, ["count=3"]))
        self.assertNotIn("noise", self.everything_written())                 # step output is never kept, only counts
        self.assertEqual(data["step_results"][0]["output_lines"], 1)
        self.assertTrue(Path(self.state, "results", "LIFE-310-%s.json" % self.sha8(root)).exists())
        self.assertIn("LIFE-310 %s ok -" % self.sha8(root), Path(self.state, "runner.log").read_text())
        self.assertEqual(os.listdir(os.path.join(self.state, "work")), [])    # the work copy is gone
        self.assertEqual(os.listdir(os.path.join(self.state, "snapshots")), [])

    def test_wrong_hash_is_refused_and_nothing_runs(self):
        marker = os.path.join(self.tmp, "ran")
        root = self.pack(steps={"10-a.sh": "touch %s\n" % marker})
        wrong = "0" * 8 if self.sha8(root) != "0" * 8 else "f" * 8
        self.assertEqual(self.run_cli("LIFE-310", wrong), (1, "RESULT fail E_HASH\n"))
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(self.result()["code"], "E_HASH")                    # logged and readable by the agent
        self.assertIn("fail E_HASH", Path(self.state, "runner.log").read_text())

    def test_a_pack_edited_after_approval_is_refused(self):
        marker = os.path.join(self.tmp, "ran")
        root = self.pack(steps={"10-a.sh": "touch %s\n" % marker})
        approved = self.sha8(root)
        Path(root, "steps", "10-a.sh").write_text("#!/bin/bash\ntouch %s\ntouch %s.evil\n" % (marker, marker))
        self.assertEqual(self.run_cli("LIFE-310", approved), (1, "RESULT fail E_HASH\n"))
        self.assertFalse(os.path.exists(marker))

    def test_a_pack_for_another_ticket_unknown_ticket_and_bad_arguments_are_refused(self):
        marker = os.path.join(self.tmp, "ran")
        root = self.pack(steps={"10-a.sh": "touch %s\n" % marker}, ticket="LIFE-999")
        self.assertEqual(self.run_cli("LIFE-310", self.sha8(root)), (1, "RESULT fail E_TICKET\n"))
        self.assertEqual(self.run_cli("LIFE-311", "abcdef12"), (1, "RESULT fail E_UNKNOWN_TICKET\n"))
        self.assertEqual(self.run_cli("LIFE-310", "XYZ"), (1, "RESULT fail E_USAGE\n"))
        self.assertEqual(self.run_cli("../../etc", "abcdef12"), (1, "RESULT fail E_USAGE\n"))
        self.assertEqual(self.run_cli("LIFE-310"), (2, "RESULT fail E_USAGE\n"))
        self.assertFalse(os.path.exists(marker))

    def test_it_refuses_to_run_unless_root(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        root = self.pack()
        host = runner.Host(state=self.state, inbox=self.inbox, require_root=True, systemctl=self.sysd)
        self.assertEqual(self.run_cli("LIFE-310", self.sha8(root), host=host), (1, "RESULT fail E_NOTROOT\n"))

    def test_only_one_run_at_a_time(self):
        import fcntl
        root = self.pack()
        os.makedirs(self.state)
        with open(os.path.join(self.state, "lock"), "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.run_cli("LIFE-310", self.sha8(root)), (1, "RESULT fail E_BUSY\n"))


class RestoreOnFailure(Base):      # GWT-RUN-03
    def setup_world(self):
        self.edited = os.path.join(self.tmp, "etc-conf")
        self.created = os.path.join(self.tmp, "new-file")
        Path(self.edited).write_text("original\n")
        os.chmod(self.edited, 0o640)
        return {"files": [self.edited, self.created], "services": ["demo.service"]}

    def test_a_failing_step_puts_files_and_services_back_and_says_so(self):
        restore = self.setup_world()
        root = self.pack(restore=restore, steps={
            "10-change.sh": "echo changed > %s\nchmod 600 %s\necho new > %s\ntouch %s\n" % (self.edited, self.edited, self.created, self.sysd.flag("demo.service")),
            "20-fail.sh": "exit 3\n"})
        self.assertEqual(self.run_cli("LIFE-310", self.sha8(root)), (1, "RESULT fail E_STEP_2\n"))
        self.assertEqual(Path(self.edited).read_text(), "original\n")
        self.assertEqual(oct(os.stat(self.edited).st_mode & 0o777), oct(0o640))
        self.assertFalse(os.path.exists(self.created))                       # it did not exist before: removed
        self.assertFalse(os.path.exists(self.sysd.flag("demo.service")))     # the service is running again
        data = self.result()
        self.assertEqual(sorted(data["restored"]), sorted([self.edited, self.created, "demo.service"]))
        self.assertEqual((data["restore_failed"], data["steps_run"]), ([], 2))
        self.assertIn(("start", "demo.service"), self.sysd.calls)
        self.assertEqual(os.listdir(os.path.join(self.state, "snapshots")), [])

    def test_a_successful_run_keeps_its_changes(self):
        restore = self.setup_world()
        root = self.pack(restore=restore, steps={"10-change.sh": "echo changed > %s\n" % self.edited})
        self.assertEqual(self.run_cli("LIFE-310", self.sha8(root)), (0, "RESULT ok\n"))
        self.assertEqual(Path(self.edited).read_text(), "changed\n")
        self.assertEqual(self.result()["restored"], [])

    def test_a_step_that_hangs_is_killed_and_restored(self):
        restore = self.setup_world()
        root = self.pack(restore=restore, timeout=1, steps={"10-hang.sh": "echo changed > %s\nsleep 30\n" % self.edited})
        began = time.monotonic()
        self.assertEqual(self.run_cli("LIFE-310", self.sha8(root)), (1, "RESULT fail E_TIMEOUT_1\n"))
        self.assertLess(time.monotonic() - began, 15)
        self.assertEqual(Path(self.edited).read_text(), "original\n")
        self.assertTrue(self.result()["step_results"][0]["timed_out"])

    def test_an_interrupted_run_restores_before_it_exits(self):
        restore = self.setup_world()
        root = self.pack(restore=restore, steps={"10-change.sh": "echo changed > %s\nkill -TERM $PPID\nsleep 20\n" % self.edited})
        driver = ("import sys; from lifeos.edge.runner import runner; "
                  "h = runner.Host(state=sys.argv[1], inbox=sys.argv[2], require_root=False, systemctl=lambda *a: (0, 'active')); "
                  "sys.exit(runner.main(sys.argv[3:], h))")
        began = time.monotonic()
        done = subprocess.run([sys.executable, "-c", driver, self.state, self.inbox, "LIFE-310", self.sha8(root)], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, "RESULT fail E_INTERRUPTED\n", ""))
        self.assertLess(time.monotonic() - began, 15)
        self.assertEqual(Path(self.edited).read_text(), "original\n")
        self.assertIn(self.edited, self.result()["restored"])

    def test_a_restore_that_cannot_finish_says_so_and_keeps_the_snapshot(self):
        restore = self.setup_world()
        root = self.pack(restore=restore, steps={"10-break.sh": "rm -f %s\nmkdir %s\nexit 1\n" % (self.edited, self.edited)})
        status, out = self.run_cli("LIFE-310", self.sha8(root))
        self.assertEqual((status, out), (1, "RESULT fail E_RESTORE\n"))
        self.assertEqual(self.result()["restore_failed"], [self.edited])
        self.assertTrue(os.listdir(os.path.join(self.state, "snapshots")))   # the originals stay on disk for a manual put-back


class NoSecretsPrinted(Base):      # GWT-RUN-04
    def test_secret_looking_output_is_redacted_everywhere_and_only_names_and_counts_are_kept(self):
        steps = {"10-noisy.sh": ("echo token=abcdefghij1234567890\necho %s\necho 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123'\n"
                                 "echo 'password: hunter2hunter2'\necho github_pat_SHORT123456789\necho ghp_abcdefghij0123456789\necho -----BEGIN OPENSSH PRIVATE KEY-----\necho AAAAB3NzaC1yc2EAAAADAQABAAABAQC7\necho -----END OPENSSH PRIVATE KEY-----\n"
                                 "echo READBACK node_count=4 secret=%s\necho READBACK services=2\necho READBACK note=github_pat_SHORT123456789 other=ghp_abcdefghij0123456789\n" % (TOKEN, TOKEN))}
        root = self.pack(steps=steps)
        status, out = self.run_cli("LIFE-310", self.sha8(root))
        self.assertEqual((status, out), (0, "RESULT ok\n"))
        written = self.everything_written()
        for secret in (TOKEN, "github_pat_SHORT123456789", "ghp_abcdefghij0123456789", "abcdefghij1234567890", "hunter2hunter2", "abcdefghijklmnopqrstuvwxyz0123", "AAAAB3NzaC1yc2EAAAADAQABAAABAQC7"):
            self.assertNotIn(secret, written)
            self.assertNotIn(secret, out)
        data = self.result()
        self.assertGreaterEqual(data["redactions_total"], 7)
        self.assertEqual(data["readback"], ["node_count=4 secret=[REDACTED]", "services=2", "note=[REDACTED:token] other=[REDACTED:token]"])
        self.assertEqual(set(data), {"schema", "runner_version", "ticket", "sha8", "sha256", "outcome", "code", "steps_total", "steps_run", "step_results", "restored", "restore_failed", "readback",
                                     "redactions_total", "started_epoch", "finished_epoch", "finished_ct", "restored_count", "redacted_note"})

    def test_redact_keeps_paths_and_plain_numbers_and_removes_long_tokens(self):
        text, n = runner.redact("see /opt/lifeos-ticker/lifeos/platform/redact.py rows=12 ok")
        self.assertEqual((text, n), ("see /opt/lifeos-ticker/lifeos/platform/redact.py rows=12 ok", 0))
        text, n = runner.redact("x " + "A" * 44 + " y")
        self.assertEqual((text, n), ("x [REDACTED:long] y", 1))

    def test_step_stdin_is_closed_and_the_environment_is_clean(self):
        root = self.pack(steps={"10-env.sh": "read -t 1 line; echo READBACK stdin_eof=$?\nenv | grep -c '^SECRET_ENV=' | sed 's/^/READBACK leaked=/'\n"})
        os.environ["SECRET_ENV"] = "abc"
        self.addCleanup(os.environ.pop, "SECRET_ENV", None)
        self.assertEqual(self.run_cli("LIFE-310", self.sha8(root))[1], "RESULT ok\n")
        readback = self.result()["readback"]
        self.assertEqual(readback[1], "leaked=0")
        self.assertNotEqual(readback[0], "stdin_eof=142")                    # not a timeout waiting for input


class Installer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.prefix = os.path.join(self.tmp, "root")

    def run_script(self, *args):
        done = subprocess.run(["bash", str(INSTALLER), "--prefix", self.prefix, *args], capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def files(self):
        return sorted(str(p.relative_to(self.prefix)) for p in Path(self.prefix).rglob("*")) if os.path.isdir(self.prefix) else []

    def test_the_pin_equals_the_runner_so_one_printed_hash_covers_both(self):
        pin = [l for l in INSTALLER.read_text().splitlines() if l.startswith("RUNNER_SHA256=")][0].split("=")[1]
        self.assertEqual(pin, hashlib.sha256(RUNNER_PY.read_bytes()).hexdigest())

    def test_dry_run_writes_nothing_and_default_is_dry(self):
        for args in ((), ("dry",)):
            status, out = self.run_script(*args)
            self.assertEqual(status, 0, out)
            self.assertIn("DRY RUN", out)
        self.assertEqual(self.files(), [])

    def test_install_verify_uninstall_round_trip(self):
        status, out = self.run_script("install")
        self.assertEqual(status, 0, out)
        self.assertIn("ROLLBACK: sudo bash install-runner.sh uninstall", out)
        self.assertEqual(self.files(), ["opt", "opt/lifeos-runner", "opt/lifeos-runner/runner.py", "usr", "usr/local", "usr/local/sbin", "usr/local/sbin/lifeos-run", "var", "var/lib",
                                        "var/lib/lifeos-runner", "var/lib/lifeos-runner/results"])
        self.assertEqual(os.stat(os.path.join(self.prefix, "usr/local/sbin/lifeos-run")).st_mode & 0o777, 0o755)
        self.assertEqual(Path(self.prefix, "usr/local/sbin/lifeos-run").read_text(), '#!/bin/sh\nexec /usr/bin/python3 -I /opt/lifeos-runner/runner.py "$@"\n')
        self.assertEqual(self.run_script("install")[0], 0)                   # idempotent
        self.assertEqual(self.run_script("verify")[0], 0)
        installed = Path(self.prefix, "opt/lifeos-runner/runner.py")
        os.chmod(installed, 0o777)
        self.assertEqual(self.run_script("verify")[0], 1)                    # group/world writable
        os.chmod(installed, 0o755)
        installed.write_text(installed.read_text() + "# tampered\n")
        status, out = self.run_script("verify")
        self.assertEqual(status, 1)
        self.assertIn("differs from the pin", out)
        status, out = self.run_script("uninstall")
        self.assertEqual(status, 0, out)
        self.assertEqual([f for f in self.files() if f.startswith("opt/") or f.endswith("lifeos-run")], [])
        self.assertIn("var/lib/lifeos-runner/results", self.files())          # results stay
        self.run_script("uninstall", "--purge")
        self.assertNotIn("var/lib/lifeos-runner", self.files())

    def test_a_changed_runner_is_refused_and_nothing_is_installed(self):
        shutil.copy(INSTALLER, self.tmp)
        Path(self.tmp, "runner.py").write_text(RUNNER_PY.read_text() + "# changed\n")
        done = subprocess.run(["bash", os.path.join(self.tmp, "install-runner.sh"), "--prefix", self.prefix, "install"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("does not match the pinned sha256", done.stdout)
        self.assertEqual(self.files(), [])

    def test_it_installs_no_timer_service_watcher_or_sudoers_rule(self):
        for path in (INSTALLER, RUNNER_PY):
            code = "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#") and "echo" not in l and "say " not in l)
            for word in ("crontab", "systemd-run", "OnCalendar", "/etc/sudoers.d", "visudo", "nohup", "while true", "threading.Timer", "sched."):
                self.assertNotIn(word, code, "%s mentions %s" % (path.name, word))
        self.assertNotIn("systemctl enable", INSTALLER.read_text().replace("#", "", 0).split("case")[1])


if __name__ == "__main__":
    unittest.main()
