"""LIFE-590 PR 2, reduced by LIFE-631: of the nine old-name shims, seven are removed (buzz_*, codemap, profile_runs, profile_tickets: no caller left); `decisions` and `readiness` stay as shims that ARE the new module,
so `python -m lifeos.decisions` and `python -m lifeos.readiness` (INDEX.md, policy parts 04 and 10, AGENTS.md, a workflow) keep working until Jim approves those edits. The modules that stay at the root each have a keep reason here."""
import importlib
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOVED = {"decisions": "lifeos.sources.devtools.decisions", "readiness": "lifeos.sources.devtools.readiness"}      # LIFE-631: seven shims are gone; these two stay while Jim's INDEX.md and policy parts 04 and 10 still name them
KEPT = {"__init__": "the package marker",
        "run": "the one entry point: `python -m lifeos.run <stage>` is named by about 90 workflow lines, AGENTS.md and every stage doc",
        "hermes_requests": "a stage module that the config registry pins by path (`lifeos/hermes_requests.py`) and hermes-stage.yml names; moving it is a separate decision",
        "probe": "its path is pinned in tests/contracts/test_privacy.py (NOT_NOTION_IDS), a protected file that needs Jim's protected-file-approved label to change; the move is one line once he gives it"}


class OldNamesStillWork(unittest.TestCase):
    def test_every_old_name_is_the_new_module_itself(self):
        for old, new in MOVED.items():
            self.assertIs(importlib.import_module(f"lifeos.{old}"), importlib.import_module(new), old)

    def test_the_root_holds_only_the_shims_and_the_three_kept_modules(self):
        names = {p.stem for p in (ROOT / "lifeos").glob("*.py")}
        self.assertEqual(names, set(MOVED) | set(KEPT))

    def test_each_shim_is_small_and_names_where_the_module_went(self):
        for old, new in MOVED.items():
            text = (ROOT / "lifeos" / f"{old}.py").read_text()
            self.assertLess(len(text.splitlines()), 12, old)
            self.assertIn(new, text, old)

    def test_python_dash_m_runs_the_moved_entry_points_under_the_old_names(self):
        run = lambda *args: subprocess.run([sys.executable, "-m", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)          # noqa: E731
        shown = run("lifeos.decisions", "show", "D193")
        self.assertEqual(shown.returncode, 0)
        self.assertIn("D193", shown.stdout)
        started = run("lifeos.buzz.mcp")                                       # the unit's command (the Oracle pack switches to it): with no config it refuses by name, it does not fail to import
        self.assertIn("config error", started.stderr)
        self.assertNotIn("No module named", started.stderr)


if __name__ == "__main__":
    unittest.main()
