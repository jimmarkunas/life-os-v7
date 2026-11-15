"""LIFE-689: every stage is classified in docs/platform/stage-isolation.md, and a stage that loops over external items without the helper or a reviewed reason fails this test.

The doc is the inventory: one row per stage in lifeos.run.STAGES (status helper | guarded | single | exempt | partial-red) and one row per reviewed loop that has no error handling inside it.
Adding a stage, adding a loop over items that call a client without a try inside, or moving a stage to `single` while it loops, fails here until the doc says why."""
import ast
import importlib.util
import pathlib
import re
import unittest
import unittest.mock

from lifeos import run

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/platform/stage-isolation.md"
STATUSES = {"helper", "guarded", "single", "exempt", "partial-red"}
ITEMS = re.compile(r"account|source|provider|board|project|meeting|thread|mailbox|inbox|folder|file|tab|lane|channel|link|url|page|message|record|issue|sprint|order|ticket|company|ats|host|server", re.I)
CLIENT = re.compile(r"^(read|fetch|get_|list|query|call|post|patch|send|run|resolve|scan|sync|write|create|update|search|messages|children|page)")


def stage_modules():
    return {name: stage.target[0] for name, stage in run.STAGES.items() if getattr(stage, "target", None)}


def loops(module):
    """{(function, iterable text): has a try inside} for the module's stage code (see loops_in)."""
    return loops_in(pathlib.Path(importlib.util.find_spec(module).origin).read_text())


def loops_in(source):
    """{(function, iterable text): has a try inside} for every loop over external-looking items whose body calls a client-looking function."""
    tree = ast.parse(source)
    found = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, (ast.For, ast.AsyncFor)) and (ITEMS.search(ast.unparse(node.iter)) or ITEMS.search(ast.unparse(node.target))):
                names = {c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "?") for stmt in node.body for c in ast.walk(stmt) if isinstance(c, ast.Call)}
                if any(CLIENT.match(n) for n in names):
                    found[(fn.name, ast.unparse(node.iter)[:70])] = any(isinstance(x, ast.Try) for stmt in node.body for x in ast.walk(stmt))
    return found


def table(heading):
    """The rows of the markdown table under `## heading`, as lists of stripped cells."""
    text = DOC.read_text()
    block = text.split(f"## {heading}", 1)[1].split("\n## ", 1)[0]
    rows = [[c.strip().strip("`") for c in line.strip().strip("|").split("|")] for line in block.splitlines() if line.startswith("| ") and not line.startswith("| ---")]
    return rows[1:]                                           # without the header row


class StageIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stages = {row[0]: row for row in table("Every stage")}
        cls.reviewed = {(row[0], row[1], row[2]) for row in table("Reviewed loops")}
        cls.modules = stage_modules()

    def test_every_stage_has_exactly_one_row_and_no_row_is_stale(self):
        self.assertEqual(len(self.stages), len(table("Every stage")), "a stage appears twice")
        self.assertEqual(sorted(set(run.STAGES) - set(self.stages)), [], "a stage has no row in docs/platform/stage-isolation.md: run python tools/regen.py (it adds the row; a loop with no try needs your verdict)")
        self.assertEqual(sorted(set(self.stages) - set(run.STAGES)), [], "a row names a stage that no longer exists")

    def test_every_status_is_known_and_none_is_marked_aborts(self):
        for name, row in self.stages.items():
            self.assertIn(row[2], STATUSES, f"{name}: status {row[2]!r} is not one of {sorted(STATUSES)}")
        self.assertFalse([n for n, row in self.stages.items() if "abort" in row[2].lower()])

    def test_an_exemption_and_a_partial_red_row_say_why(self):
        for name, row in self.stages.items():
            if row[2] in ("exempt", "partial-red"):
                self.assertGreaterEqual(len(row[3]), 30, f"{name}: a {row[2]} row needs its reason")

    def test_a_helper_stage_really_uses_the_helper(self):
        for name, row in self.stages.items():
            if row[2] == "helper":
                source = pathlib.Path(importlib.util.find_spec(self.modules[name]).origin).read_text()
                self.assertIn("isolate.run_items", source, f"{name} is marked helper but does not call isolate.run_items")

    def test_the_partial_failure_column_matches_the_runner(self):
        for name, row in self.stages.items():
            self.assertEqual(row[4], "yes" if name in run.PARTIAL_FAILURE else "no", f"{name}: the 'exits 1 on any failed item' column disagrees with lifeos.run.PARTIAL_FAILURE")
        for name, row in self.stages.items():
            if row[2] == "partial-red":
                self.assertIn(name, run.PARTIAL_FAILURE, f"{name}: partial-red means a failed item fails the stage through PARTIAL_FAILURE")

    def test_a_single_unit_stage_has_no_loop_over_external_items(self):
        for name, row in self.stages.items():
            if row[2] == "single" and name in self.modules:
                self.assertEqual(loops(self.modules[name]), {}, f"{name} is marked single but its module loops over items that call a client: mark it guarded or helper")

    def test_every_loop_without_error_handling_has_been_reviewed(self):
        seen = set()
        for module in sorted(set(self.modules.values())):
            for (function, iterable), guarded in loops(module).items():
                if not guarded:
                    key = (module.replace("lifeos.", ""), function, iterable)
                    seen.add(key)
                    self.assertIn(key, self.reviewed, f"{key}: a loop over external items with no try inside is not in the 'Reviewed loops' table of docs/platform/stage-isolation.md: use "
                                                      "lifeos.platform.isolate.run_items, or review it there with a verdict")
        self.assertEqual(sorted(self.reviewed - seen), [], "a reviewed loop no longer exists: remove its row")


class TheScanAndTheRowsBite(unittest.TestCase):
    """The checks above only protect the rule if they fail on a violation: prove each one does."""
    UNGUARDED = "def go(client, boards):\n    for board in boards:\n        client.fetch(board)\n"
    GUARDED = "def go(client, boards):\n    for board in boards:\n        try:\n            client.fetch(board)\n        except OSError:\n            pass\n"

    def test_the_scan_flags_a_loop_over_items_that_calls_a_client_without_error_handling(self):
        self.assertEqual(loops_in(self.UNGUARDED), {("go", "boards"): False})
        self.assertEqual(loops_in(self.GUARDED), {("go", "boards"): True})
        self.assertEqual(loops_in("def go(rows):\n    for n in range(3):\n        total = n\n"), {})                  # a loop with no items and no client call is not a finding

    def test_a_stage_without_a_row_and_a_row_without_a_stage_both_fail(self):
        with unittest.mock.patch.dict(run.STAGES, {"brand-new": lambda limit, live: {}}):
            self.assertEqual(sorted(set(run.STAGES) - {row[0] for row in table("Every stage")}), ["brand-new"])
        self.assertTrue({"granola-auto", "interview"} <= {row[0] for row in table("Every stage") if row[2] == "partial-red"})


if __name__ == "__main__":
    unittest.main()
