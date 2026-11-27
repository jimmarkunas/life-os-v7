"""code and workflow source say what and why now"""
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
ALLOWLIST = ROOT / "tests" / "contracts" / "ticket_refs_allowlist.json"
MAX_ALLOWED = 25


def prose_refs(path):
    """> sorted [(kind, ticket)] for every ticket id in a Python file's comments and docstrings (runtime strings are not
    prose).
    """
    text = path.read_text(encoding="utf-8")
    found = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            found += [("comment", ref) for ref in TICKET.findall(tok.string)]
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                found += [("docstring", ref) for ref in TICKET.findall(first.value.value)]
    return sorted(found)


def comment_refs(text):
    """> [(line number, comment text)] for every YAML comment that names a ticket"""
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


class LifeosCode(unittest.TestCase):
    """A LIFE-nnn in a lifeos/ comment or docstring fails unless ticket_refs_allowlist.json lists it with a reason (at
    most 25 entries).
    """

    def setUp(self):
        self.allowed = json.loads(ALLOWLIST.read_text(encoding="utf-8"))

    def test_the_allowlist_is_small_and_every_entry_says_why(self):
        self.assertLessEqual(len(self.allowed), MAX_ALLOWED)
        for entry in self.allowed:
            self.assertEqual(sorted(entry), ["path", "reason", "ref"])
            self.assertRegex(entry["ref"], r"^LIFE-\d+$")
            self.assertTrue(entry["reason"].strip(), entry)

    def test_no_comment_or_docstring_in_lifeos_names_a_ticket(self):
        allowed = {(e["path"], e["ref"]) for e in self.allowed}
        seen, found = set(), []
        for path in sorted((ROOT / "lifeos").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            for kind, ref in prose_refs(path):
                seen.add((rel, ref))
                if (rel, ref) not in allowed:
                    found.append(f"{rel}: {ref} in a {kind}")
        self.assertEqual(found, [], "ticket history belongs in Jira and git: say what and why now, or list the id in the allowlist with a reason")
        self.assertEqual(sorted(allowed - seen), [], "allowlist entries that match no comment or docstring any more: remove them")


if __name__ == "__main__":
    unittest.main()
