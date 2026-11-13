"""LIFE-643 row 5: the slim npm tree. Proven from the committed lockfile (no network, no npm): the omit rules leave 64 packages, the only install-script packages are baileys and protobufjs, no image library
survives, and the build step carries exactly the three npm flags (a copy of the step without them fails the same check). The pack builds and passes the runner's own check."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = ROOT / "lifeos" / "whatsapp" / "oracle"
PACK = ROOT / "lifeos" / "edge" / "whatsapp" / "pack_row5"
BUILD_STEP = PACK / "pack" / "steps" / "20-build.sh"
FLAGS = ("--omit=optional", "--omit=peer", "--ignore-scripts")


def lock_packages():
    return {name: meta for name, meta in json.loads((ORACLE / "package-lock.json").read_text())["packages"].items() if name}


def kept(packages):
    """What `npm ci --omit=optional --omit=peer` installs: everything not flagged optional or peer-only."""
    return {name for name, meta in packages.items() if not meta.get("optional") and not meta.get("peer")}


def npm_ci_line(script):
    lines = [line for line in script.splitlines() if " ci " in line and not line.lstrip().startswith("#")]
    return lines[0] if len(lines) == 1 else ""


def has_all_flags(script):
    line = npm_ci_line(script)
    return bool(line) and all(flag in line.split() for flag in FLAGS)


class LockfileTests(unittest.TestCase):
    def test_the_omit_rules_leave_64_of_95_packages_and_no_image_library(self):
        packages = lock_packages()
        keep = kept(packages)
        self.assertEqual((len(packages), len(keep)), (95, 64))
        self.assertEqual([n for n in keep if "sharp" in n or "@img" in n], [])
        self.assertEqual((sum(1 for m in packages.values() if m.get("optional")), sum(1 for m in packages.values() if m.get("peer"))), (27, 31))

    def test_only_baileys_and_protobufjs_have_install_scripts_and_neither_is_needed(self):
        scripted = sorted(name for name, meta in lock_packages().items() if meta.get("hasInstallScript"))
        self.assertEqual(scripted, ["node_modules/baileys", "node_modules/protobufjs"])

    def test_the_service_imports_only_baileys_pino_and_node_builtins(self):
        imports = sorted({line.split("from")[-1].strip(" ;'\"") for line in (ORACLE / "attention.mjs").read_text().splitlines() if line.startswith("import ")})
        self.assertTrue(all(i in ("baileys", "pino") or i.startswith(("node:", "./")) for i in imports), imports)


class StepTests(unittest.TestCase):
    def test_the_build_step_runs_npm_ci_with_exactly_the_three_flags(self):
        self.assertTrue(has_all_flags(BUILD_STEP.read_text()))

    def test_removing_any_flag_from_the_step_fails_the_check(self):
        text = BUILD_STEP.read_text()
        for flag in FLAGS:
            self.assertFalse(has_all_flags(text.replace(" " + flag, "")), flag)

    def test_the_pack_builds_and_passes_the_runners_own_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pack"
            subprocess.run([sys.executable, str(PACK / "build_pack.py"), str(out)], cwd=ROOT, check=True, capture_output=True)
            done = subprocess.run([sys.executable, str(ROOT / "lifeos" / "edge" / "runner" / "runner.py"), "check", str(out)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout)
            self.assertIn("PACK ok LIFE-643 steps=4 restore_files=0 restore_services=2", done.stdout)
            self.assertEqual((out / "files" / "package-lock.json").read_bytes(), (ORACLE / "package-lock.json").read_bytes())

    def test_the_preflight_names_the_missing_row1_layout_with_a_fixed_word(self):
        self.assertIn("|| fail row1_layout_missing", (PACK / "pack" / "steps" / "10-preflight.sh").read_text())


class SlimHelperTests(unittest.TestCase):
    def tree(self, tmp, names, extra=()):
        modules = Path(tmp) / "node_modules"
        modules.mkdir()
        (modules / ".package-lock.json").write_text(json.dumps({"packages": {n: {} for n in names}}))
        for e in extra:
            (modules / e).mkdir()
        return tmp

    def verify(self, tmp):
        return subprocess.run([sys.executable, "-I", str(PACK / "pack" / "files" / "slim.py"), "verify", tmp, str(ORACLE / "package-lock.json")], capture_output=True, text=True)

    def test_the_right_set_passes_and_a_missing_package_or_an_image_library_fails(self):
        want = sorted(kept(lock_packages()))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.verify(self.tree(tmp, want)).stdout.strip(), "packages=64 want=64 image_libraries=0")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("count_differs", self.verify(self.tree(tmp, want[1:])).stdout)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.verify(self.tree(tmp, want, ["sharp"])).stdout.strip(), "image_library_present")


if __name__ == "__main__":
    unittest.main()
