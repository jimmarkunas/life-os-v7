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
