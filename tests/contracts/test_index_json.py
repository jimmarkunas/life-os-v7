"""docs/index.json (generated with INDEX.md from docs/index-source.json) is the machine-readable twin of INDEX.md (same global sources, same order), every path exists, and it
holds no Notion IDs.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class IndexJson(unittest.TestCase):
    DATA = json.loads((ROOT / "docs" / "index.json").read_text())

    def test_it_lists_the_same_paths_as_index_md_in_the_same_order(self):
        section = (ROOT / "INDEX.md").read_text().split("## Global sources\n", 1)[1].split("\n## ", 1)[0]
        listed = [
            re.match(r"\| \[`([^`]+)`\]", line).group(1) for line in section.splitlines() if line.startswith("| [`")
        ]
        self.assertEqual([s["path"] for s in self.DATA["sources"]], listed)

    def test_every_entry_is_complete_and_resolves(self):
        for source in self.DATA["sources"]:
            self.assertEqual(set(source), {"path", "authority", "purpose", "owner", "retrieval"})
            self.assertTrue(all(source.values()), source)
            self.assertTrue((ROOT / source["path"]).is_file(), source["path"])
        self.assertTrue((ROOT / self.DATA["master"]).is_file())

    def test_the_catalog_route_uses_a_title_and_key_never_a_notion_id(self):
        self.assertEqual(self.DATA["external_resources"]["bootstrap_route"], "external_resource_catalog")
        self.assertIsNone(re.search(r"[0-9a-f]{32}|notion\.(com|so)", json.dumps(self.DATA)))


if __name__ == "__main__":
    unittest.main()
