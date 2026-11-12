"""The committed code map must equal what the code generates, so it never goes stale."""
from pathlib import Path
import unittest

from lifeos import codemap

DOCS = Path(__file__).resolve().parents[2] / "docs"


class CodeMap(unittest.TestCase):
    def test_committed_map_is_current(self):
        md, js = codemap.render()
        self.assertEqual((DOCS / "CODEMAP.md").read_text(), md, "docs/CODEMAP.md is stale: run python -m lifeos.codemap")
        self.assertEqual((DOCS / "codemap.json").read_text(), js, "docs/codemap.json is stale: run python -m lifeos.codemap")

    def test_every_stage_and_table_is_listed(self):
        m = codemap.build()
        self.assertIn("network-import", m["stages"])
        self.assertIn("v7_network_people", m["tables"])
        self.assertTrue(m["packages"]["megibow"] and m["decisions"])


class RoutingPageAndFind(unittest.TestCase):
    """LIFE-592: CODEMAP.md is a short routing page; the full map stays in codemap.json and `codemap find` answers one question from it."""

    def test_the_routing_page_stays_under_its_size_limit_and_lists_every_stage_and_package(self):
        text = (DOCS / "CODEMAP.md").read_text()
        self.assertLessEqual(len(text.encode()), codemap.ROUTING_LIMIT)
        m = codemap.build()
        for stage in m["stages"]:
            self.assertIn(f"`{stage}`", text)
        for package in m["packages"]:
            self.assertIn(f"`lifeos/{package}/`", text)

    def test_find_a_package_lists_all_its_modules(self):
        m = codemap.build()
        found = codemap.find(m, "megibow")
        self.assertTrue(any(line.startswith("package `lifeos/megibow/`") for line in found))
        for path in m["packages"]["megibow"]:
            self.assertTrue(any(path in line for line in found), path)

    def test_find_a_stage_returns_its_module_and_a_decision_number_returns_its_title(self):
        m = codemap.build()
        self.assertIn("stage `amazon-retention` -> `lifeos.amazon.retention:run`", codemap.find(m, "amazon-retention"))
        self.assertEqual([line for line in codemap.find(m, "D139") if line.startswith("decision ")], [f"decision D139: {m['decisions']['D139']}"])

    def test_find_nothing_and_an_overlong_answer_are_bounded(self):
        m = codemap.build()
        self.assertEqual(codemap.find(m, "zzzz-not-a-thing"), [])
        self.assertEqual(codemap.find(m, ""), [])
        self.assertLessEqual(len(codemap.find(m, "a", limit=5)), 6)

    def test_the_command_line_find_prints_the_entry_and_a_miss_exits_nonzero(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(codemap.main(["find", "amazon-retention"]), 0)
        self.assertIn("lifeos.amazon.retention:run", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(codemap.main(["find", "zzzz-not-a-thing"]), 1)
