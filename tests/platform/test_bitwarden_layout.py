"""the Bitwarden layout document names exactly three projects, and the alert ids are the same in the code that reads
them.
"""

import re
import unittest
from pathlib import Path

from lifeos.platform import alert_store

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs/platform/bitwarden-layout.md").read_text()
PROJECTS = ("LIFE OS GitHub Actions", "LIFE OS EDGE/OPENCLAW", "LIFE OS ALERTS")


class Layout(unittest.TestCase):
    def test_the_document_names_exactly_the_three_projects(self):
        rows = [line for line in DOC.splitlines() if line.startswith("| LIFE OS")]
        self.assertEqual([row.split("|")[1].strip() for row in rows], list(PROJECTS))
        self.assertNotIn("EDGE/OUTLOOK", DOC)

    def test_the_alert_ids_in_the_store_are_the_ones_the_workflow_reader_uses(self):
        plain = (ROOT / ".github/actions/bws-plain/plain.py").read_text()
        for name, ident in alert_store.IDS.items():
            self.assertIn(f'"{name}": "{ident}"', plain, name)

    def test_the_hosts_wrappers_are_told_the_project_name_in_the_document(self):
        self.assertIn("`LIFE OS EDGE/OPENCLAW`", DOC)
        self.assertEqual(len(re.findall(r"3 projects", DOC)), 1)


if __name__ == "__main__":
    unittest.main()
