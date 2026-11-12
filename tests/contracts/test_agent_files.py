"""LIFE-593: the Concord block in the per-tool instruction files is generated from AGENTS.md and cannot drift from it."""
import shutil
import tempfile
import unittest
from pathlib import Path

from lifeos.platform import agent_files as af

REAL = Path(__file__).resolve().parents[2]


def scratch():
    """A throwaway copy of the files the generator touches, so no test edits the real repo."""
    root = Path(tempfile.mkdtemp())
    for name in ("AGENTS.md", *af.GENERATED, *af.VARIANTS):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REAL / name, target)
    return root


class GeneratedCopies(unittest.TestCase):
    def test_every_committed_copy_equals_the_block_in_agents_md(self):
        self.assertEqual(af.drifted(REAL), [], "a generated agent file differs from AGENTS.md: run python -m lifeos.platform.agent_files")

    def test_editing_agents_md_changes_all_generated_files_and_keeps_their_other_content(self):
        root = scratch()
        text = (root / "AGENTS.md").read_text()
        (root / "AGENTS.md").write_text(text.replace("Keep each claim small", "Keep each claim tiny", 1))
        self.assertEqual(sorted(af.drifted(root)), sorted(af.GENERATED))                            # all of them are now behind
        self.assertEqual(sorted(af.sync(root)), sorted(af.GENERATED))                               # and one run brings them all along
        self.assertEqual(af.drifted(root), [])
        cursor = (root / ".cursor/rules/concord.mdc").read_text()
        self.assertTrue(cursor.startswith("---"))                                                   # the Cursor header outside the block is kept
        self.assertIn("Keep each claim tiny", cursor)

    def test_a_hand_edit_of_a_generated_file_is_caught_and_named(self):
        root = scratch()
        path = root / ".codex/concord.md"
        path.write_text(path.read_text().replace("Keep each claim small", "Keep each claim whatever", 1))
        self.assertEqual(af.drifted(root), [".codex/concord.md"])
        self.assertEqual(af.sync(root), [".codex/concord.md"])
        self.assertEqual(af.drifted(root), [])

    def test_a_missing_generated_file_or_missing_block_counts_as_drift_and_sync_refuses_a_file_without_markers(self):
        root = scratch()
        (root / ".github/copilot-instructions.md").unlink()
        self.assertEqual(af.drifted(root), [".github/copilot-instructions.md"])
        (root / ".codex/concord.md").write_text("no markers here\n")
        with self.assertRaises(FileNotFoundError):
            af.sync(root)                                                                           # a deleted generated file is a person's decision, not silently recreated
        shutil.copy(REAL / ".github/copilot-instructions.md", root / ".github/copilot-instructions.md")
        with self.assertRaises(ValueError):
            af.sync(root)                                                                           # a file without the markers is refused, never overwritten

    def test_the_variant_file_is_listed_with_a_reason_and_is_not_compared(self):
        root = scratch()
        self.assertEqual(list(af.VARIANTS), [".agents/rules/concord.md"])
        self.assertNotEqual(af.block_of((root / ".agents/rules/concord.md").read_text()), af.source(root))     # it really is a different block
        self.assertNotIn(".agents/rules/concord.md", af.drifted(root))
        self.assertTrue(all(len(reason.split()) > 5 for reason in af.VARIANTS.values()))

    def test_every_other_file_in_the_agent_folders_that_carries_a_concord_block_is_either_generated_or_a_listed_variant(self):
        known = set(af.GENERATED) | set(af.VARIANTS)
        found = set()
        for folder in (".agents", ".claude", ".codex", ".cursor", ".gemini", ".github"):
            for path in (REAL / folder).rglob("*"):
                if path.is_file() and path.suffix in (".md", ".mdc") and "concord:start" in path.read_text():
                    found.add(str(path.relative_to(REAL)))
        self.assertEqual(found - known, set(), "a new per-tool file repeats the Concord block: add it to GENERATED or to VARIANTS with a reason")


if __name__ == "__main__":
    unittest.main()
