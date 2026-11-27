"""LIFE-1016: code and workflow source say what and why now; ticket history lives in Jira and git. A YAML comment in `.github/workflows-src/` (the parts agents edit) may not name a ticket; `.github/workflows-src/why.yml`, which builds docs/workflows.md, is the one home for ticket numbers there."""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import wfsrc  # noqa: E402

SRC = ROOT / ".github/workflows-src"
TICKET = re.compile(r"LIFE-\d+")


def comment_refs(text):
    """-> [(line number, comment text)] for every YAML comment that names a ticket; a `#` inside a script (`run: |`) or a quoted value is not a YAML comment."""
    lines = text.split("\n")
    inside = wfsrc._literal(lines)
    found = []
    for number, line in enumerate(lines, 1):
        if inside[number - 1]:
            continue
        if line.lstrip().startswith("#"):
            comment = line.strip()
        else:
            trailing = re.search(r"\s+#\s.*$", line)
            before = line[: trailing.start()] if trailing else ""
            comment = trailing.group(0).strip() if trailing and before.count('"') % 2 == 0 and before.count("'") % 2 == 0 else ""
        if comment and TICKET.search(comment):
            found.append((number, comment[:100]))
    return found


class WorkflowParts(unittest.TestCase):
    def test_no_comment_in_a_workflow_part_names_a_ticket(self):
        found = {str(p.relative_to(ROOT)): comment_refs(p.read_text()) for p in sorted(SRC.glob("*/*.yml"))}
        self.assertEqual({k: v for k, v in found.items() if v}, {})

    def test_the_why_file_is_the_one_place_that_may_name_tickets_and_it_is_what_builds_the_doc(self):
        self.assertTrue(TICKET.search((SRC / "why.yml").read_text()))
        self.assertEqual(sorted(p.name for p in SRC.glob("*.yml")), ["why.yml"])

    def test_the_checker_finds_a_ticket_in_a_comment_but_not_in_a_script_or_a_value(self):
        text = "a:\n  # LIFE-1 says so\n  b: 1  # and LIFE-2\n  run: |\n    echo x  # LIFE-3\n  description: \"LIFE-4 # LIFE-5\"\n"
        self.assertEqual([n for n, _ in comment_refs(text)], [2, 3])


if __name__ == "__main__":
    unittest.main()
