"""The Buzz MCP release is built from one commit and holds exactly the frozen manifest, which is the service's import
closure: a new import that the manifest lacks, or a stale manifest line, fails here before any release."""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import buzz_release  # noqa: E402


class Manifest(unittest.TestCase):
    def test_the_frozen_manifest_equals_the_import_closure_of_the_service(self):
        self.assertEqual(buzz_release.read_manifest(), buzz_release.closure())

    def test_every_manifest_file_exists_and_the_memory_modules_are_in_it(self):
        entries = buzz_release.read_manifest()
        for rel in entries:
            self.assertTrue((ROOT / buzz_release.source_of(rel)).is_file(), rel)
        for needed in ("lifeos/platform/memory_honcho.py", "lifeos/platform/memory_monitor.py", "bin/honcho-alert"):
            self.assertIn(needed, entries)

    def test_the_service_imports_from_a_staged_manifest_layout_with_memory_and_its_monitor(self):
        stage = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, stage)
        for rel in buzz_release.read_manifest():
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / buzz_release.source_of(rel), dest)
        code = (
            "import lifeos.buzz.mcp as m; "
            "assert m.memory_honcho is not None and m.memory_monitor is not None and m.inbox_send is not None; "
            "print('ok')"
        )
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(stage), "PYTHONDONTWRITEBYTECODE": "1"}
        out = subprocess.run(
            [sys.executable, "-s", "-B", "-c", code], cwd=stage, env=env, capture_output=True, text=True
        )
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "ok"), out.stderr[-500:])


class GraphReader(unittest.TestCase):
    def test_the_release_ships_the_graph_reader_its_switch_and_the_vendored_driver_with_its_license(self):
        entries = buzz_release.read_manifest()
        for needed in (
            "lifeos/platform/graph_tools.py",
            "lifeos/platform/graph_index.py",
            "lifeos/platform/graph_switch.json",
            "vendor/pymysql/__init__.py",
            "vendor/pymysql/connections.py",
            "vendor/pymysql/LICENSE",
        ):
            self.assertIn(needed, entries)
        self.assertNotIn("lifeos/platform/graph_tools.py", [e for e in entries if e.startswith("vendor/")])
        self.assertEqual(buzz_release.source_of("vendor/pymysql/err.py"), "deploy/buzz-mcp/vendor/pymysql/err.py")

    def test_a_package_with_no_vendored_copy_adds_nothing_and_a_vendored_one_adds_its_files(self):
        self.assertEqual(buzz_release._vendored("import json\nfrom pathlib import Path\n", ROOT), [])
        files = buzz_release._vendored("def f():\n    import pymysql\n", ROOT)
        self.assertIn("vendor/pymysql/__init__.py", files)
        self.assertTrue(all(f.startswith("vendor/pymysql/") for f in files))
        self.assertEqual(files, sorted(files))

    def test_the_switch_file_ships_off(self):
        self.assertEqual((ROOT / "lifeos/platform/graph_switch.json").read_text().strip(), '{"enabled": false}')


if __name__ == "__main__":
    unittest.main()
