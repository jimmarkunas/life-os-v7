"""LIFE-79 (Jim, Nov 2 2026): the Development Policy is a concise canonical entrypoint plus verbatim topic parts under docs/development-policy/.
The parts, joined in manifest order (each part's two-line header removed, the one re-based link reversed), are the whole policy; these checks prove nothing was
dropped, reordered or silently changed, and that the entrypoint indexes every original section. No network, Jira or Notion."""
import hashlib
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "docs" / "life-os-development-policy.md"
MANIFEST = json.loads((ROOT / "docs" / "development-policy" / "manifest.json").read_text())


def reassemble():
    """The whole Development Policy text, rebuilt from its parts."""
    text = "".join((ROOT / p["path"]).read_text().split("\n", MANIFEST["header_lines"])[MANIFEST["header_lines"]] for p in MANIFEST["parts"])
    for rewrite in MANIFEST["link_rewrites"]:
        text = text.replace(rewrite["to"], rewrite["from"])
    return text


def anchor(heading):
    return re.sub(r"[^\w\- ]", "", heading.lstrip("#").strip().lower()).replace(" ", "-")


class Split(unittest.TestCase):
    def test_the_parts_reassemble_to_the_recorded_policy(self):
        text = reassemble()
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), MANIFEST["reassembled_sha256"],
                         "a part changed without a manifest amendment (docs/life-os-development-policy.md, Changing the policy)")
        if not MANIFEST["amendments"]:                                          # until an approved amendment, the parts ARE the original file
            base = MANIFEST["baseline"]
            self.assertEqual((MANIFEST["reassembled_sha256"], len(text), len(text.splitlines())), (base["sha256"], base["characters"], base["lines"]))
        for amendment in MANIFEST["amendments"]:
            self.assertTrue(all(amendment.get(k) for k in ("date", "pr", "approval")), amendment)

    def test_the_baseline_blob_itself_when_git_history_has_it(self):
        base = MANIFEST["baseline"]
        got = subprocess.run(["git", "-C", str(ROOT), "cat-file", "blob", base["blob"]], capture_output=True)
        if got.returncode:
            self.skipTest("baseline blob not in this clone's history")
        self.assertEqual(hashlib.sha256(got.stdout).hexdigest(), base["sha256"])
        if not MANIFEST["amendments"]:
            self.assertEqual(reassemble().encode(), got.stdout)

    def test_parts_are_contiguous_ordered_topic_files_within_size(self):
        lines, previous = [p["baseline_lines"] for p in MANIFEST["parts"]], 0
        for start, end in lines:
            self.assertEqual(start, previous + 1)
            previous = end
        self.assertEqual(previous, MANIFEST["baseline"]["lines"])
        for p in MANIFEST["parts"]:
            path = ROOT / p["path"]
            self.assertTrue(path.parent.name == "development-policy" and path.suffix == ".md", p["path"])
            self.assertLessEqual(len(path.read_bytes()), 15 * 1024, p["path"])
            body = path.read_text().split("\n", 2)
            self.assertTrue(body[0].startswith("<!-- LIFE OS Development Policy, part ") and "same authority as the whole policy" in body[0] and body[1] == "", p["path"])
            self.assertEqual([h for h in body[2].splitlines() if h.startswith("#")], p["headings"], p["path"])
        self.assertEqual(sorted(p.name for p in (ROOT / "docs" / "development-policy").glob("*.md")), sorted(Path(p["path"]).name for p in MANIFEST["parts"]))

    def test_every_original_section_is_indexed_and_linked_from_the_concise_entrypoint(self):
        entry = ENTRY.read_text()
        self.assertLessEqual(len(entry.encode()), 20 * 1024)
        self.assertIn("the part governs", entry)
        headings = [h for h in reassemble().splitlines() if h.startswith("#")]
        self.assertEqual(headings, [h for p in MANIFEST["parts"] for h in p["headings"]])
        self.assertEqual(len(headings), len(set(headings)))                     # anchors stay unique
        for p in MANIFEST["parts"]:
            name = Path(p["path"]).name
            for h in p["headings"]:
                self.assertIn(f"](development-policy/{name}#{anchor(h)})", entry, h)
        for rule in ("Deterministic agent work selection", "Jira-first development lifecycle", "Deadline Mode", "Requirement-change reconciliation"):
            self.assertEqual(sum(rule in h for h in headings), 1, rule)        # each named rule lives in exactly one place

    def test_no_rule_text_is_duplicated_outside_its_part(self):
        entry = ENTRY.read_text()
        for line in reassemble().splitlines():
            if len(line) > 120 and not line.startswith("#"):
                self.assertNotIn(line, entry)
