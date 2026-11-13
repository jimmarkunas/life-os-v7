"""LIFE-624 step 2 pack, run on a fixture (a temp /opt/lifeos-close, fake systemctl and a fake installer; the steps' paths come from two test-only environment variables): the preflight refuses a tool that is neither the old nor the new one
and a live tree that lacks the new module or the shim, and passes a re-run; the apply step installs the two tools, rewrites the unit through the installer, and a unit that still names the old path stops it. The pack builds and passes the
runner's own check, and its restore list names the two tools and the unit and socket files."""
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lifeos.edge.runner import runner

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "lifeos" / "edge" / "oracle" / "pack_624"
sys.path.insert(0, str(PACK))
import build_pack                                                                                          # noqa: E402
NEW_LINE = "ExecStart=x; from lifeos.sources.apps.close_app import handle; y"
OLD_LINE = "ExecStart=x; from lifeos.sources.close_app import handle; y"


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Fixture:
    def __init__(self, unit_line):
        self.tmp = Path(tempfile.mkdtemp(prefix="p624"))
        self.opt, self.units, self.pack = self.tmp / "opt", self.tmp / "units", self.tmp / "pack"
        (self.opt / "bin").mkdir(parents=True)
        self.units.mkdir()
        (self.pack / "files").mkdir(parents=True)
        (self.pack / "steps").mkdir()
        for step in (PACK / "pack" / "steps").iterdir():
            (self.pack / "steps" / step.name).write_text(step.read_text())
        for name, body in (("install-close.sh", "NEW-INSTALLER"), ("close_release.py", "NEW-RELEASE")):
            (self.pack / "files" / name).write_text(body)
            (self.opt / "bin" / name).write_text("OLD-" + name)
        news = {"install-close.sh": "NEW-INSTALLER", "close_release.py": "NEW-RELEASE"}
        (self.pack / "files" / "expected.txt").write_text("".join(f"old {n} {h('OLD-' + n)}\nnew {n} {h(body)}\n" for n, body in news.items()))
        fake = self.tmp / "fakebin"
        fake.mkdir()
        (fake / "systemctl").write_text("#!/bin/bash\nexit 0\n")
        (fake / "flock").write_text("#!/bin/bash\nexit 0\n")
        (fake / "sha256sum").write_text('#!/bin/bash\n' + f'{sys.executable} -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],\'rb\').read()).hexdigest() + \'  \' + sys.argv[1])" "$1"\n')
        self.fake_unit_line = unit_line
        for name in ("systemctl", "flock", "sha256sum"):
            (fake / name).chmod(0o755)
        self.env = {"PATH": f"{fake}:/usr/bin:/bin", "LIFEOS_CLOSE_OPT": str(self.opt), "LIFEOS_CLOSE_UNITS": str(self.units), "PACK_DIR": str(self.pack)}

    def tree(self, new=True, shim=True):
        for flag, rel in ((new, "lifeos/sources/apps/close_app.py"), (shim, "lifeos/sources/close_app.py")):
            if flag:
                (self.opt / "current" / rel).parent.mkdir(parents=True, exist_ok=True)
                (self.opt / "current" / rel).write_text("handle = 1\n")

    def run(self, step):
        done = subprocess.run(["bash", str(self.pack / "steps" / step)], env=self.env, capture_output=True, text=True)
        return done.returncode, done.stdout


class PackTests(unittest.TestCase):
    def test_the_preflight_passes_with_the_old_tools_and_both_files_in_the_live_tree(self):
        f = Fixture(NEW_LINE)
        f.tree()
        code, out = f.run("10-preflight.sh")
        self.assertEqual(code, 0, out)
        self.assertIn("READBACK install-close.sh=old", out)
        self.assertIn("READBACK live_tree_has_both_close_app_files=yes", out)

    def test_a_tool_that_is_neither_old_nor_new_stops_it_with_code_11(self):
        f = Fixture(NEW_LINE)
        f.tree()
        (f.opt / "bin" / "close_release.py").write_text("SOMETHING ELSE")
        code, out = f.run("10-preflight.sh")
        self.assertEqual((code, "READBACK close_release.py=unexpected" in out), (11, True))

    def test_a_live_tree_without_the_new_module_or_without_the_shim_stops_it_with_code_12(self):
        for new, shim in ((False, True), (True, False)):
            f = Fixture(NEW_LINE)
            f.tree(new=new, shim=shim)
            self.assertEqual(f.run("10-preflight.sh")[0], 12, (new, shim))

    def test_a_second_run_with_the_tools_already_new_passes_the_preflight(self):
        f = Fixture(NEW_LINE)
        f.tree()
        for name in ("install-close.sh", "close_release.py"):
            (f.opt / "bin" / name).write_text({"install-close.sh": "NEW-INSTALLER", "close_release.py": "NEW-RELEASE"}[name])
        code, out = f.run("10-preflight.sh")
        self.assertEqual(code, 0, out)
        self.assertIn("already_new", out)

    def apply(self, unit_line):
        f = Fixture(unit_line)
        f.tree()
        (f.pack / "files" / "install-close.sh").write_text(f'#!/bin/bash\n[ "$1" = units ] && {{ printf "%s\\n" \'{unit_line}\' > "$2/lifeos-close@.service"; exit 0; }}\n'
                                                              '[ "$1" = verify ] && { echo "  [PASS] one"; exit 0; }\nexit 2\n')
        return f, f.run("20-apply.sh")

    def test_apply_installs_the_tools_rewrites_the_unit_and_reads_back_one_new_import_and_no_old_one(self):
        f, (code, out) = self.apply(NEW_LINE)
        self.assertEqual(code, 0, out)
        self.assertIn("READBACK unit_new_import_lines=1", out)
        self.assertIn("READBACK unit_old_import_lines=0", out)
        self.assertEqual((f.opt / "bin" / "close_release.py").read_text(), "NEW-RELEASE")
        self.assertTrue((f.opt / "bin.bak-LIFE-624" / "close_release.py").read_text().startswith("OLD-"))

    def test_a_unit_that_still_names_the_old_path_stops_it_with_code_22(self):
        _, (code, out) = self.apply(OLD_LINE)
        self.assertEqual(code, 22, out)

    def test_the_pack_builds_passes_the_runners_check_and_restores_the_tools_and_the_unit_files(self):
        out = Path(tempfile.mkdtemp()) / "pack"
        build_pack.build(out, "HEAD")
        manifest = runner.load_manifest(str(out))
        self.assertEqual(set(manifest["files"]), set(build_pack.HOST))
        self.assertTrue({"/opt/lifeos-close/bin/install-close.sh", "/etc/systemd/system/lifeos-close@.service"} <= set(manifest["files"]))
        done = subprocess.run([sys.executable, str(ROOT / "lifeos" / "edge" / "runner" / "runner.py"), "check", str(out)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
