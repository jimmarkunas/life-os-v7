"""docs/README.md (generated from docs/index-source.json) lists every markdown file under docs/ as a relative link, and every doc path it links exists."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def listed(text):
    return {m.group(1) for m in LINK.finditer(text) if not m.group(1).startswith(("http://", "https://", "/"))}


def missing(listed_text, files):
    """Files (paths relative to docs/) that the README text does not link."""
    found = listed(listed_text)
    return sorted(f for f in files if f not in found)


def dangling(listed_text, exists):
    """Linked paths (relative to docs/) for which exists(path) is false."""
    return sorted(p for p in listed(listed_text) if not exists(p))


def doc_files():
    return sorted(p.relative_to(DOCS).as_posix() for p in DOCS.rglob("*.md") if p.name != "README.md")


class DocsIndex(unittest.TestCase):
    README = (DOCS / "README.md").read_text()

    def test_every_doc_is_listed(self):
        self.assertEqual(
            missing(self.README, doc_files()),
            [],
            "add these as lines in docs/index-source.json, then run python tools/gen_indexes.py",
        )

    def test_every_link_exists(self):
        self.assertEqual(dangling(self.README, lambda p: (DOCS / p).exists()), [])

    def test_the_boot_files_reach_the_index(self):
        self.assertIn("docs/README.md", (ROOT / "CLAUDE.md").read_text())

    def test_synthetic_unlisted_doc_is_reported(self):
        text = "- [a](a.md): x\n- [b](sub/b.md): y\n"
        self.assertEqual(missing(text, ["a.md", "sub/b.md", "new-doc.md"]), ["new-doc.md"])
        self.assertEqual(dangling(text, lambda p: p == "a.md"), ["sub/b.md"])


if __name__ == "__main__":
    unittest.main()
