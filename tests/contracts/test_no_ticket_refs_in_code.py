"""LIFE-1016: code and workflow source say what and why now; ticket history lives in Jira and git. A comment or docstring in `lifeos/**/*.py` may not name a ticket unless `ticket_ref_allowlist.json` (at most 25 entries, each with a reason) lists it. A YAML comment in `.github/workflows-src/` (the parts agents edit) may not name a ticket; `.github/workflows-src/why.yml`, which builds docs/workflows.md, is the one home for ticket numbers there."""
import ast
import io
import json
import pathlib
import re
import sys
import tokenize
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


ALLOW = ROOT / "tests/contracts/ticket_ref_allowlist.json"


def python_refs(text):
    """-> {ticket refs} named in the comments and docstrings of one Python file (string literals that are not docstrings are data and stay)."""
    found = set()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                found.update(TICKET.findall(first.value.value))
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            found.update(TICKET.findall(token.string))
    return found


class PythonCode(unittest.TestCase):
    def test_no_comment_or_docstring_in_lifeos_names_a_ticket_beyond_the_allowlist(self):
        allow = json.loads(ALLOW.read_text())["entries"]
        allowed = {(e["file"], e["ref"]) for e in allow}
        found = set()
        for path in sorted((ROOT / "lifeos").rglob("*.py")):
            try:
                refs = python_refs(path.read_text(errors="ignore"))
            except (SyntaxError, tokenize.TokenError):
                continue
            found.update((str(path.relative_to(ROOT)), ref) for ref in refs)
        self.assertEqual(sorted(found - allowed), [], "say what and why, not which ticket: ticket history lives in Jira and git")
        self.assertEqual(sorted(allowed - found), [], "an allowlist entry that is no longer needed: delete it")

    def test_the_allowlist_is_short_and_every_entry_says_why(self):
        entries = json.loads(ALLOW.read_text())["entries"]
        self.assertLessEqual(len(entries), 25)
        self.assertTrue(all(e["reason"].strip() and TICKET.fullmatch(e["ref"]) for e in entries))

    def test_the_checker_reads_comments_and_docstrings_only(self):
        text = '"""LIFE-1 module."""\nx = "LIFE-2 is data"  # LIFE-3\ndef f():\n    """LIFE-4"""\n    return "LIFE-5"\n'
        self.assertEqual(python_refs(text), {"LIFE-1", "LIFE-3", "LIFE-4"})


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
