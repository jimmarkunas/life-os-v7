"""LIFE-575: the capture sweeper removes only folders no process of this user uses. A fake /proc and temp folders stand in for the VM."""
import contextlib
import io
import os
import tempfile
import time
import unittest
from unittest import mock

from lifeos.edge.openclaw import capture_sweep as sweep

LIVE = "1c9733d6-970f-45cd-af5a-baf53f51a161"
DEAD = "01fcdf26-1e88-44bc-94f2-c0cd374fdec6"
EMPTY = "79f4001d-ea62-4a16-94a7-2715b70ed8af"


def run(*args, now=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = sweep.main(list(args), now=now)
    return code, out.getvalue(), err.getvalue()


class Sandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "captures")
        self.proc = os.path.join(self._tmp.name, "proc")
        os.makedirs(self.root)
        os.makedirs(self.proc)
        self.old = time.time() - 7200

    def folder(self, name, old=True):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.join(path, "captures"))
        if old:
            os.utime(path, (self.old, self.old))
        return path

    def process(self, pid, environ="", cwd="/", maps="", fds=(), comm="node"):
        d = os.path.join(self.proc, str(pid))
        os.makedirs(os.path.join(d, "fd"))
        with open(os.path.join(d, "environ"), "wb") as f:
            f.write(environ.replace("\n", "\0").encode())
        with open(os.path.join(d, "maps"), "w") as f:
            f.write(maps)
        with open(os.path.join(d, "comm"), "w") as f:
            f.write(comm + "\n")
        os.symlink(cwd, os.path.join(d, "cwd"))
        for i, target in enumerate(fds):
            os.symlink(target, os.path.join(d, "fd", str(i)))
        return d

    def names(self):
        return sorted(os.listdir(self.root))


class Sweep(Sandbox):
    def test_orphan_is_removed_and_live_folder_kept(self):
        self.folder(LIVE)
        self.folder(DEAD)
        self.process(10, environ=f"OPENCLAW_CAPTURE={self.root}/{LIVE}")
        code, out, _ = run("--delete", "--root", self.root, "--proc", self.proc)
        self.assertEqual((code, self.names()), (0, [LIVE]))
        self.assertIn("removed 1", out)
        self.assertIn("01fcdf26", out)

    def test_each_way_a_process_can_hold_a_folder_keeps_it(self):
        for how in ("environ", "cwd", "fd", "maps"):
            with self.subTest(how=how):
                shutil_root = tempfile.mkdtemp(dir=self._tmp.name)
                self.root, self.proc = os.path.join(shutil_root, "c"), os.path.join(shutil_root, "p")
                os.makedirs(self.root)
                os.makedirs(self.proc)
                path = self.folder(DEAD)
                kw = {"environ": {"environ": f"X={path}"}, "cwd": {"cwd": path}, "fd": {"fds": [path + "/owner.sqlite"]}, "maps": {"maps": path + "/native/x.node"}}[how]
                self.process(11, **kw)
                code, _, _ = run("--delete", "--root", self.root, "--proc", self.proc)
                self.assertEqual((code, self.names()), (0, [DEAD]))

    def test_dry_run_deletes_nothing(self):
        self.folder(DEAD)
        self.process(10)
        code, out, _ = run("--root", self.root, "--proc", self.proc)
        self.assertEqual((code, self.names()), (0, [DEAD]))
        self.assertIn("dry-run", out)
        self.assertIn("orphan 1", out)

    def test_recent_folder_is_not_removed(self):
        self.folder(DEAD, old=False)
        self.process(10)
        run("--delete", "--root", self.root, "--proc", self.proc)
        self.assertEqual(self.names(), [DEAD])

    def test_non_uuid_names_symlinks_and_files_are_skipped(self):
        self.folder(LIVE)
        os.makedirs(os.path.join(self.root, "not-a-uuid"))
        os.symlink(self.root, os.path.join(self.root, EMPTY))
        with open(os.path.join(self.root, "a.txt"), "w") as f:
            f.write("x")
        self.process(10)
        _, out, _ = run("--delete", "--root", self.root, "--proc", self.proc, "--min-age", "0")
        self.assertEqual(self.names(), sorted(["a.txt", "not-a-uuid", EMPTY]))      # the only real UUID folder was an orphan here and went
        self.assertIn("skipped 3", out)
        self.assertTrue(os.path.isdir(self.root))

    def test_other_users_processes_do_not_count_and_do_not_block(self):
        self.folder(DEAD)
        self.process(10)
        self.process(11, environ=f"X={self.root}/{DEAD}")
        real = os.stat
        other = os.path.join(self.proc, "11")

        def stat(path, *a, **k):
            result = real(path, *a, **k)
            if path == other:
                return mock.Mock(st_uid=os.getuid() + 1)
            return result

        with mock.patch.object(sweep.os, "stat", stat):
            code, _, _ = run("--delete", "--root", self.root, "--proc", self.proc)
        self.assertEqual((code, self.names()), (0, []))


class Unknown(Sandbox):
    def test_unreadable_process_table_deletes_nothing(self):
        self.folder(DEAD)
        code, out, err = run("--delete", "--root", self.root, "--proc", os.path.join(self._tmp.name, "missing"))
        self.assertEqual((code, self.names()), (2, [DEAD]))
        self.assertIn("CAPTURE_SWEEP_PROCESS_TABLE_UNREADABLE", err)
        self.assertEqual(out, "")

    def test_no_process_of_this_user_deletes_nothing(self):
        self.folder(DEAD)
        code, _, _ = run("--delete", "--root", self.root, "--proc", self.proc)
        self.assertEqual((code, self.names()), (2, [DEAD]))

    def test_one_of_our_processes_unreadable_deletes_nothing(self):
        self.folder(DEAD)
        d = self.process(10)
        os.chmod(os.path.join(d, "maps"), 0)
        os.chmod(os.path.join(d, "environ"), 0)
        self.addCleanup(os.chmod, os.path.join(d, "maps"), 0o644)
        self.addCleanup(os.chmod, os.path.join(d, "environ"), 0o644)
        if os.access(os.path.join(d, "maps"), os.R_OK):
            self.skipTest("running as a user that ignores file modes")
        code, _, _ = run("--delete", "--root", self.root, "--proc", self.proc)
        self.assertEqual((code, self.names()), (2, [DEAD]))

    def test_an_unreadable_system_process_is_tolerated_but_an_unreadable_node_is_not(self):
        self.folder(DEAD)
        self.process(10)
        d = self.process(11, comm="systemd")
        os.chmod(os.path.join(d, "maps"), 0)
        os.chmod(os.path.join(d, "environ"), 0)
        self.addCleanup(os.chmod, os.path.join(d, "maps"), 0o644)
        self.addCleanup(os.chmod, os.path.join(d, "environ"), 0o644)
        if os.access(os.path.join(d, "maps"), os.R_OK):
            self.skipTest("running as a user that ignores file modes")
        code, _, _ = run("--delete", "--root", self.root, "--proc", self.proc)
        self.assertEqual((code, self.names()), (0, []))
        self.folder(DEAD)
        with open(os.path.join(d, "comm"), "w") as f:
            f.write("node\n")
        code, _, _ = run("--delete", "--root", self.root, "--proc", self.proc)
        self.assertEqual((code, self.names()), (2, [DEAD]))

    def test_missing_capture_folder_is_not_an_error(self):
        code, out, _ = run("--delete", "--root", os.path.join(self._tmp.name, "nope"), "--proc", self.proc)
        self.assertEqual(code, 0)
        self.assertIn("nothing to do", out)

    def test_output_has_counts_and_name_prefixes_only(self):
        self.folder(DEAD)
        self.process(10)
        _, out, _ = run("--root", self.root, "--proc", self.proc)
        self.assertNotIn(DEAD, out)
        self.assertNotIn(self.root, out)


if __name__ == "__main__":
    unittest.main()
