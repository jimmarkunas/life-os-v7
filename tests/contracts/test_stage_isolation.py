"""a loop over external items without error handling inside, and without a reviewed reason in docs/platform/stage-
isolation.md, fails this test.
"""
import ast
import importlib.util
import pathlib
import re
import unittest
import unittest.mock

from lifeos import run
from lifeos.platform import stages

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/platform/stage-isolation.md"
ITEMS = re.compile(r"account|source|provider|board|project|meeting|thread|mailbox|inbox|folder|file|tab|lane|channel|link|url|page|message|record|issue|sprint|order|ticket|company|ats|host|server", re.I)
CLIENT = re.compile(r"^(read|fetch|get_|list|query|call|post|patch|send|run|resolve|scan|sync|write|create|update|search|messages|children|page)")


def stage_modules():
    return {name: stage.target[0] for name, stage in run.STAGES.items() if getattr(stage, "target", None)}


def loops(module):
    """{(function, iterable text): has a try inside} for the module's stage code (see loops_in)."""
    return loops_in(pathlib.Path(importlib.util.find_spec(module).origin).read_text())


def loops_in(source):
    """{(function, iterable text): has a try inside} for every loop over external-looking items whose body calls a
    client-looking function.
    """
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
    return rows[1:]  # without the header row


class StageIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reviewed = {(row[0], row[1], row[2]) for row in table("Reviewed loops")}
        cls.modules = stage_modules()

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
        self.assertEqual(loops_in("def go(rows):\n    for n in range(3):\n        total = n\n"), {})

    def test_a_stage_without_a_manifest_entry_fails(self):
        with unittest.mock.patch.dict(run.STAGES, {"brand-new": lambda limit, live: {}}):
            self.assertEqual(sorted(set(run.STAGES) - set(stages.load()["stages"])), ["brand-new"])
        self.assertTrue({"granola-auto", "interview"} <= {name for name, entry in stages.load()["stages"].items() if entry["status"] == "partial-red"})


if __name__ == "__main__":
    unittest.main()
