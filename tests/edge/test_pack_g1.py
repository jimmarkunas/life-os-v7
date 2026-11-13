"""LIFE-647 group 1 pack, run on a fixture (a fake root with the four host paths, a fake systemctl). It proves: a good run installs the four trimmed programs keeping each one's mode and restarting
nothing, a second run and --dry-run change nothing, a host file that is neither the old nor the new one stops the pack before anything changes (and a copy without the pin does not), a running
service stops it, a program that fails its check puts all four back, and the built pack passes the runner's own check."""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "lifeos" / "edge" / "pack_g1"
sys.path.insert(0, str(PACK))
import build_pack  # noqa: E402

FAKE_SYSTEMCTL = """#!/bin/bash
case "$1" in
  is-active) [ -e "$EP_FAKE/active-$2" ] && echo active || { echo inactive; exit 3; } ;;
  list-units) ls "$EP_FAKE" | grep '^gran-' ;;
esac
exit 0
"""
MODES = {"offsite_backup.py": 0o644, "ticker.py": 0o640, "close_release.py": 0o644, "gran_helper.py": 0o600}


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, new_overrides=None):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="g1", dir="/tmp")))
        self.root, self.files, self.fake = self.tmp / "root", self.tmp / "files", self.tmp / "fake"
        self.fake.mkdir()
        (self.tmp / "systemctl").write_text(FAKE_SYSTEMCTL)
        (self.tmp / "systemctl").chmod(0o755)
        shutil.copytree(PACK / "pack" / "files", self.files)
        platform = self.root / "opt/lifeos-ticker/lifeos/platform"
        platform.mkdir(parents=True)
        (platform.parent / "__init__.py").write_text("")
        (platform / "__init__.py").write_text("")
        shutil.copy(ROOT / "lifeos/platform/gate.py", platform / "gate.py")
        rows = []
        self.old, self.new = {}, {}
        for name, (repo_path, host, _) in build_pack.FILES.items():
            self.old[name] = ("# old %s\n" % name).encode()
            self.new[name] = (new_overrides or {}).get(name) or (ROOT / repo_path).read_bytes()
            (self.files / name).write_bytes(self.new[name])
            target = self.root / host.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.old[name])
            target.chmod(MODES[name])
            rows.append("|".join((name, host, digest(self.old[name]), digest(self.new[name]))))
        (self.files / "table").write_text("# table\n" + "\n".join(rows) + "\n")
        self.env = dict(os.environ, EP_ROOT=str(self.root), EP_FILES=str(self.files), EP_SYSTEMCTL=str(self.tmp / "systemctl"), EP_PY=sys.executable, EP_FAKE=str(self.fake))
        for unit in ("ticker-tick", "ticker-watch", "offsite-backup", "close-release"):
            (self.fake / ("active-lifeos-%s.timer" % unit)).touch()

    def path(self, name):
        return self.root / build_pack.FILES[name][1].lstrip("/")

    def listing(self):
        return sorted((str(p.relative_to(self.root)), p.read_bytes() if p.is_file() else b"") for p in self.root.rglob("*"))

    def run(self, *args, script=PACK / "pack" / "steps" / "10-install.sh"):
        return subprocess.run(["bash", str(script), *args], env=self.env, capture_output=True, text=True, timeout=120)


class PackTests(unittest.TestCase):
    def fixture(self, **kw):
        fx = Fixture(**kw)
        self.addCleanup(shutil.rmtree, fx.tmp, True)
        return fx

    def test_a_good_run_installs_the_four_keeps_each_mode_and_restarts_nothing(self):
        fx = self.fixture()
        done = fx.run()
        self.assertEqual(done.returncode, 0, done.stdout)
        for name in build_pack.FILES:
            self.assertEqual(fx.path(name).read_bytes(), fx.new[name])
            self.assertEqual(fx.path(name).stat().st_mode & 0o777, MODES[name])
            self.assertIn("READBACK check_%s=ok" % name[:-3], done.stdout)
        self.assertIn("READBACK result=installed_4 dry_run=no", done.stdout)
        self.assertIn("READBACK timers_unchanged=yes", done.stdout)
        self.assertEqual([p for p in self.fixture_names(fx) if p.endswith(".lifeos-new")], [])

    def fixture_names(self, fx):
        return [str(p) for p in fx.root.rglob("*")]

    def test_a_second_run_and_a_dry_run_change_nothing(self):
        fx = self.fixture()
        before = fx.listing()
        done = fx.run("--dry-run")
        self.assertEqual((done.returncode, fx.listing()), (0, before), done.stdout)
        self.assertIn("READBACK result=would_install_4 dry_run=yes", done.stdout)
        fx.run()
        after = fx.listing()
        done = fx.run()
        self.assertEqual((done.returncode, fx.listing()), (0, after), done.stdout)
        self.assertIn("READBACK result=installed_0 dry_run=no", done.stdout)

    def test_a_host_file_that_is_neither_old_nor_new_stops_everything_before_a_change(self):
        fx = self.fixture()
        fx.path("ticker.py").write_text("# someone edited this\n")
        before = fx.listing()
        done = fx.run()
        self.assertEqual((done.returncode, fx.listing()), (1, before), done.stdout)
        self.assertIn("READBACK failed=pin_mismatch_ticker.py", done.stdout)

    def test_a_copy_without_the_pin_overwrites_what_the_real_script_refuses(self):         # fail-then-pass
        fx = self.fixture()
        fx.path("ticker.py").write_text("# someone edited this\n")
        text = (PACK / "pack" / "steps" / "10-install.sh").read_text()
        self.assertIn('elif [ "$cur" != "$new" ]; then fail "pin_mismatch_$name"; fi', text)
        mutated = fx.tmp / "mutated.sh"
        mutated.write_text(text.replace('elif [ "$cur" != "$new" ]; then fail "pin_mismatch_$name"; fi', 'elif [ "$cur" != "$new" ]; then OLD="$OLD$name "; fi'))
        fx.run(script=mutated)
        self.assertEqual(fx.path("ticker.py").read_bytes(), fx.new["ticker.py"])

    def test_a_running_service_or_a_pack_file_that_is_not_the_pinned_one_stops_it(self):
        fx = self.fixture()
        before = fx.listing()
        for marker, word in (("active-lifeos-offsite-backup.service", "busy_lifeos-offsite-backup"), ("gran-1", "busy_lifeos-gran")):
            (fx.fake / marker).touch()
            done = fx.run()
            self.assertEqual((done.returncode, fx.listing()), (1, before), marker)
            self.assertIn("READBACK failed=" + word, done.stdout)
            (fx.fake / marker).unlink()
        (fx.files / "close_release.py").write_text("# swapped\n")
        self.assertIn("READBACK failed=pack_file_close_release.py", fx.run().stdout)

    def test_a_program_that_fails_its_check_puts_all_four_back(self):
        broken = (ROOT / build_pack.FILES["gran_helper.py"][0]).read_text().replace("def _grans(", "def _grans_x(").encode()
        fx = self.fixture(new_overrides={"gran_helper.py": broken})
        before = fx.listing()
        done = fx.run()
        self.assertEqual((done.returncode, fx.listing()), (1, before), done.stdout)
        self.assertIn("READBACK failed=check_gran_helper.py_restored", done.stdout)

    def test_the_pack_builds_with_real_pins_and_passes_the_runners_own_check(self):
        out = Path(tempfile.mkdtemp(prefix="g1pack", dir="/tmp")) / "pack"
        self.addCleanup(shutil.rmtree, out.parent, True)
        build_pack.build(out)
        rows = [r.split("|") for r in (out / "files" / "table").read_text().splitlines() if not r.startswith("#")]
        self.assertEqual([r[0] for r in rows], list(build_pack.FILES))
        for name, _host, old, new in rows:
            self.assertRegex(old, r"^[0-9a-f]{64}$")
            self.assertEqual(digest((out / "files" / name).read_bytes()), new)
        done = subprocess.run([sys.executable, str(ROOT / "lifeos" / "edge" / "runner" / "runner.py"), "check", str(out)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("PACK ok LIFE-647 steps=1 restore_files=4 restore_services=0", done.stdout)


if __name__ == "__main__":
    unittest.main()
