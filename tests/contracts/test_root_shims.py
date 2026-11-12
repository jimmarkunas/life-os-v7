"""LIFE-590 PR 2: ten root modules moved (lifeos/buzz/, lifeos/sources/devtools/); each old name is a tiny shim that IS the new module, so `python -m lifeos.buzz_mcp` (the installed Oracle unit),
`python -m lifeos.codemap` (AGENTS.md, .claude/settings.json) and `python -m lifeos.readiness` (a workflow) keep working. The three modules that stay at the root each have a keep reason here."""
import importlib
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOVED = {"buzz_auth": "lifeos.buzz.auth", "buzz_mcp": "lifeos.buzz.mcp", "buzz_nip98": "lifeos.buzz.nip98", "buzz_relay": "lifeos.buzz.relay",
         "codemap": "lifeos.sources.devtools.codemap", "decisions": "lifeos.sources.devtools.decisions", "probe": "lifeos.sources.devtools.probe",
         "profile_runs": "lifeos.sources.devtools.profile_runs", "profile_tickets": "lifeos.sources.devtools.profile_tickets", "readiness": "lifeos.sources.devtools.readiness"}
KEPT = {"__init__": "the package marker",
        "run": "the one entry point: `python -m lifeos.run <stage>` is named by about 90 workflow lines, AGENTS.md and every stage doc",
        "hermes_requests": "a stage module that the config registry pins by path (`lifeos/hermes_requests.py`) and hermes-stage.yml names; moving it is a separate decision"}


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
        found = run("lifeos.codemap", "find", "buzz")
        self.assertEqual(found.returncode, 0)
        self.assertIn("lifeos/buzz/", found.stdout)
        shown = run("lifeos.decisions", "show", "D193")
        self.assertEqual(shown.returncode, 0)
        self.assertIn("D193", shown.stdout)
        started = run("lifeos.buzz_mcp")                                       # the installed Oracle unit's command: with no config it refuses by name, it does not fail to import
        self.assertIn("config error", started.stderr)
        self.assertNotIn("No module named", started.stderr)


if __name__ == "__main__":
    unittest.main()
