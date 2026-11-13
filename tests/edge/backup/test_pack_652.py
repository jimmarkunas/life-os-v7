"""LIFE-652 pack, run on a fixture (a fake root, a fake systemctl, a fake dry proof). It proves: a good run installs the new program with the same mode, a host file that is not the expected old one is refused, a running backup
stops it, a failed proof puts the old file back, a second run changes nothing, --dry-run changes nothing, and a copy without the pin overwrites a different host file (fail-then-pass). The built pack passes the runner's own check."""
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lifeos.edge.runner import runner

ROOT = Path(__file__).resolve().parents[3]
PACK = ROOT / "lifeos" / "edge" / "backup" / "pack_652"
SCRIPT = PACK / "pack" / "files" / "install.sh"
OLD, NEW = b"# old program\n", b"# new program\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, tmp, proof="dry_ok files=5 bytes=1 missing=0 skipped=0 credentials=0 optional_db_failed=1"):
        self.tmp = Path(tmp)
        self.files = self.tmp / "files"
        self.files.mkdir()
        (self.files / "offsite_backup.py").write_bytes(NEW)
        (self.files / "old.sha256").write_text(sha(OLD) + "\n")
        self.script = self.files / "install.sh"
        self.script.write_text(SCRIPT.read_text())
        self.host = self.tmp / "root/opt/lifeos-offsite/offsite_backup.py"
        self.host.parent.mkdir(parents=True)
        self.host.write_bytes(OLD)
        self.host.chmod(0o644)
        (self.tmp / "systemctl").write_text('#!/bin/bash\n[ -e "$FAKE_RUNNING" ] && echo active || { echo inactive; exit 3; }\n')
        (self.tmp / "systemctl").chmod(0o755)
        (self.tmp / "dry").write_text("#!/bin/bash\necho '%s'\n" % proof)
        (self.tmp / "dry").chmod(0o755)
        self.running = self.tmp / "running"
        self.env = dict(os.environ, IN_ROOT=str(self.tmp / "root"), IN_PY=sys.executable, IN_SYSTEMCTL=str(self.tmp / "systemctl"), IN_DRY=str(self.tmp / "dry"), FAKE_RUNNING=str(self.running))

    def run(self, *args, script=None):
        return subprocess.run(["bash", str(script or self.script), *args], env=self.env, capture_output=True, text=True, timeout=60)


class PackTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.fx = Fixture(self.t.name)

    def test_a_good_run_installs_the_new_program_keeps_the_mode_and_prints_the_proof_counts(self):
        done = self.fx.run()
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(self.fx.host.read_bytes(), NEW)
        self.assertEqual(stat.S_IMODE(self.fx.host.stat().st_mode), 0o644)
        self.assertIn("READBACK proof=dry_ok files=5", done.stdout)
        self.assertIn("READBACK result=installed", done.stdout)
        self.assertFalse(list(self.fx.host.parent.glob("*.new")))

    def test_a_second_run_changes_nothing(self):
        self.fx.run()
        done = self.fx.run()
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("READBACK host=already_new", done.stdout)
        self.assertEqual(self.fx.host.read_bytes(), NEW)

    def test_dry_run_runs_only_the_gates(self):
        done = self.fx.run("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("READBACK result=dry_run_only", done.stdout)
        self.assertEqual(self.fx.host.read_bytes(), OLD)

    def test_a_host_file_that_is_not_the_expected_old_one_is_refused_and_untouched(self):
        self.fx.host.write_bytes(b"# someone edited this\n")
        done = self.fx.run()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("READBACK failed=host_differs_from_main", done.stdout)
        self.assertEqual(self.fx.host.read_bytes(), b"# someone edited this\n")

    def test_a_running_backup_stops_it_before_any_change(self):
        self.fx.running.touch()
        done = self.fx.run()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("READBACK failed=backup_running", done.stdout)
        self.assertEqual(self.fx.host.read_bytes(), OLD)

    def test_a_failed_proof_puts_the_old_file_back(self):
        (self.fx.tmp / "dry").write_text("#!/bin/bash\necho 'backup_db_snapshot'\n")
        done = self.fx.run()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("READBACK failed=dry_proof_backup_db_snapshot_old_file_restored", done.stdout)
        self.assertEqual(self.fx.host.read_bytes(), OLD)
        self.assertEqual(stat.S_IMODE(self.fx.host.stat().st_mode), 0o644)

    def test_a_copy_without_the_pin_overwrites_a_different_host_file(self):                       # fail-then-pass
        text = SCRIPT.read_text()
        self.assertIn('|| fail host_differs_from_main', text)
        mutated = self.fx.files / "mutated.sh"
        mutated.write_text(text.replace('[ "$have" = "$OLD_SHA" ] || fail host_differs_from_main', "true"))
        self.fx.host.write_bytes(b"# someone edited this\n")
        self.fx.run(script=mutated)
        self.assertEqual(self.fx.host.read_bytes(), NEW)                                           # without the pin the edit is lost; the real script above refused

    def test_the_pack_builds_passes_the_runners_check_and_declares_the_installed_file_for_restore(self):
        out = Path(self.t.name) / "pack"
        subprocess.run([sys.executable, str(PACK / "build_pack.py"), str(out)], cwd=ROOT, check=True, capture_output=True)
        manifest = runner.load_manifest(str(out))
        self.assertEqual((manifest["steps"], manifest["files"], manifest["services"]), (["steps/10-install.sh"], ["/opt/lifeos-offsite/offsite_backup.py"], []))
        self.assertEqual((out / "files/offsite_backup.py").read_bytes(), (ROOT / "lifeos/edge/backup/offsite_backup.py").read_bytes())
        done = subprocess.run([sys.executable, str(ROOT / "lifeos" / "edge" / "runner" / "runner.py"), "check", str(out)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("PACK ok LIFE-652 steps=1 restore_files=1 restore_services=0", done.stdout)


if __name__ == "__main__":
    unittest.main()
