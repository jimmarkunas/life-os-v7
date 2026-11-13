"""The code map is generated on demand (not committed, LIFE-644): the generator runs, lists every stage and package, and `find` answers from it."""
import unittest

from lifeos.sources.devtools import codemap


class CodeMap(unittest.TestCase):
    def test_the_generator_runs_and_the_files_it_writes_are_not_committed(self):
        md, js = codemap.render()
        self.assertTrue(md.startswith("# V7 code map") and js.startswith("{"))
        ignored = (codemap.ROOT / ".gitignore").read_text()
        self.assertIn("docs/CODEMAP.md", ignored)
        self.assertIn("docs/codemap.json", ignored)

    def test_every_stage_and_table_is_listed(self):
        m = codemap.build()
        self.assertIn("network-import", m["stages"])
        self.assertIn("v7_network_people", m["tables"])
        self.assertTrue(m["packages"]["megibow"] and m["decisions"])


class RoutingPageAndFind(unittest.TestCase):
    """LIFE-592: CODEMAP.md is a short routing page; the full map stays in codemap.json and `codemap find` answers one question from it."""

    def test_the_routing_page_stays_under_its_size_limit_and_lists_every_stage_and_package(self):
        text = codemap.render()[0]
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
