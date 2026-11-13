"""LIFE-643 row 5: the slim npm tree. Proven from the committed lockfile (no network, no npm): the omit rules leave 64 packages, the only install-script packages are baileys and protobufjs, no image library
survives, and the build step PRUNES a copy of the installed tree (it never runs npm: the only npm on the host is in the ubuntu account's private home). A fixture tree is pruned and verified; a build step or tree that skips the prune
fails the same check. The pack builds and passes the runner's own check."""
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
def lock_packages():
    return {name: meta for name, meta in json.loads((ORACLE / "package-lock.json").read_text())["packages"].items() if name}


def kept(packages):
    """What `npm ci --omit=optional --omit=peer` installs: everything not flagged optional or peer-only."""
    return {name for name, meta in packages.items() if not meta.get("optional") and not meta.get("peer")}


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
    def test_the_build_step_copies_and_prunes_and_never_runs_npm_or_the_network(self):
        script = BUILD_STEP.read_text()
        code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        for word in (" npm ", "npm ci", "npm install", "curl", "wget", "as_user \"$NEW_USER\" env \"HOME=$NEW_HOME\" \"PATH"):
            self.assertNotIn(word, code)
        self.assertIn("cp -a", code)
        self.assertIn("slim.py\" prune", code)

    def test_a_build_step_without_the_prune_fails_the_same_check(self):
        def prunes(script):
            return any('slim.py" prune' in line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertTrue(prunes(BUILD_STEP.read_text()))
        self.assertFalse(prunes("\n".join(line for line in BUILD_STEP.read_text().splitlines() if 'slim.py" prune' not in line)))

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
    SLIM = str(PACK / "pack" / "files" / "slim.py")

    def fixture(self, tmp, drop=()):
        """A copy of what the host has: the lockfile's packages minus the platform-specific optional ones npm skips there (70 of 95), each folder with a package.json, the hidden lockfile, a dangling .bin link."""
        packages = lock_packages()
        installed = {n for n, m in packages.items() if not (m.get("optional") and ("darwin" in n or "win32" in n or "musl" in n or "arm" in n or "ppc" in n or "s390x" in n or "riscv" in n or "wasm" in n or "freebsd" in n or "ia32" in n))} - set(drop)
        modules = Path(tmp) / "node_modules"
        for name in installed:
            folder = Path(tmp) / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "package.json").write_text("{}")
        (modules / ".package-lock.json").write_text(json.dumps({"packages": {n: {} for n in sorted(installed)}}))
        (modules / ".bin").mkdir()
        (modules / ".bin" / "gone").symlink_to("../sharp/bin/none.js")
        return installed

    def slim(self, command, tmp):
        return subprocess.run([sys.executable, "-I", self.SLIM, command, tmp, str(ORACLE / "package-lock.json")], capture_output=True, text=True)

    def test_pruning_a_copy_of_the_installed_tree_leaves_exactly_the_64_and_verify_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed = self.fixture(tmp)
            self.assertIn("sharp", " ".join(installed))                                    # the fixture really has the image libraries
            self.assertEqual(self.slim("verify", tmp).stdout.strip(), "image_library_present")      # before the prune it fails
            done = self.slim("prune", tmp)
            self.assertEqual(done.returncode, 0, done.stdout)
            self.assertIn("dangling_links_removed=1", done.stdout)
            self.assertEqual(self.slim("verify", tmp).stdout.strip(), "packages=64 want=64 image_libraries=0")
            self.assertFalse((Path(tmp) / "node_modules" / "@img").exists())
            self.assertFalse((Path(tmp) / "node_modules" / ".bin" / "gone").is_symlink())

    def test_a_tree_missing_a_needed_package_or_still_holding_an_omitted_one_fails(self):
        want = sorted(kept(lock_packages()))
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(tmp, drop=[want[5]])
            self.slim("prune", tmp)
            self.assertIn("count_differs", self.slim("verify", tmp).stdout)
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(tmp)
            self.slim("prune", tmp)
            leftover = Path(tmp) / "node_modules" / "detect-libc"                          # an omitted peer-only package that survived the prune
            leftover.mkdir()
            (leftover / "package.json").write_text("{}")
            self.assertEqual(self.slim("verify", tmp).stdout.strip(), "omitted_package_present")


if __name__ == "__main__":
    unittest.main()
