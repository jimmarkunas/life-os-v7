"""LIFE-620: lifeos/platform/runtime.json is the one place that says which module runs where.

The "where each module runs" table in docs/platform/module-inventory.md is GENERATED from it (`python -m tests.contracts.test_runtime_manifest --write`).
The drift check's surface list (lifeos/platform/drift.py) and the ticker installer's MODULES are deployed files and drift surfaces themselves, so they are CHECKED against it,
never rewritten here (editing them would show DRIFT on the Oracle VM until an install). A disagreement fails with the exact difference."""
import json
import re
import sys
import unittest
from pathlib import Path

from lifeos.platform import drift

ROOT = Path(__file__).resolve().parents[2]
MANIFEST, INVENTORY = ROOT / "lifeos/platform/runtime.json", ROOT / "docs/platform/module-inventory.md"
INSTALLER = ROOT / "lifeos/edge/ticker/oracle/install-ticker.sh"
BEGIN = "<!-- runtime-manifest:begin (generated: edit lifeos/platform/runtime.json, then python -m tests.contracts.test_runtime_manifest --write) -->"
END = "<!-- runtime-manifest:end -->"


def load():
    return json.loads(MANIFEST.read_text())


def render(data):
    rows = ["| Module | Runs on | Installed as | Activated by |", "|---|---|---|---|"]
    rows += ["| %s | %s | %s | %s |" % (m["module"], ", ".join(m["host"]), m.get("installed", ""), m.get("unit", "")) for m in data["modules"]]
    return "\n".join([BEGIN, *rows, END])


def block(text):
    return re.search(re.escape(BEGIN) + ".*?" + re.escape(END), text, re.S)


def units():
    return {"lifeos/" + p.name for p in (ROOT / "lifeos").iterdir() if p.name not in ("__init__.py", "__pycache__") and (p.is_dir() or p.suffix == ".py")}


class RuntimeManifest(unittest.TestCase):
    def test_every_row_names_a_real_path_and_a_known_host(self):
        data = load()
        for m in data["modules"]:
            self.assertTrue((ROOT / m["module"]).exists(), m["module"])
            self.assertTrue(m["host"] and set(m["host"]) <= set(data["hosts"]), m["module"])

    def test_every_top_level_lifeos_unit_has_exactly_one_row(self):
        rows = [m["module"] for m in load()["modules"] if m["module"].count("/") == 1]
        self.assertEqual(sorted(rows), sorted(set(rows)), "a unit is listed twice")
        self.assertEqual(set(rows), units())

    def test_the_inventory_table_is_the_generated_one(self):
        found = block(INVENTORY.read_text())
        self.assertIsNotNone(found, "markers missing in module-inventory.md")
        self.assertEqual(found.group(0), render(load()), "stale: run python -m tests.contracts.test_runtime_manifest --write")

    def test_drift_surfaces_equal_the_rows_that_name_a_surface(self):
        wanted = {(m["surface"], m["module"], m["installed"], m["unit"]) for m in load()["modules"] if "surface" in m}
        self.assertEqual({(s.name, s.repo_path, s.installed, s.unit) for s in drift.SURFACES}, wanted, "drift.SURFACES (first set) and runtime.json (second set) disagree")

    def test_the_installer_modules_equal_the_platform_files_the_ticker_runs_on_oracle(self):
        installer = set(re.search(r'^MODULES="([^"]+)"', INSTALLER.read_text(), re.M).group(1).split())
        wanted = {m["module"].rsplit("/", 1)[1] for m in load()["modules"] if m.get("surface", "").startswith("ticker/") and m["module"].startswith("lifeos/platform/")}
        self.assertEqual(installer, wanted, "install-ticker.sh MODULES (first set) and runtime.json (second set) disagree")


if __name__ == "__main__":
    if "--write" in sys.argv:
        INVENTORY.write_text(block(INVENTORY.read_text()).string.replace(block(INVENTORY.read_text()).group(0), render(load())))
    else:
        unittest.main()
