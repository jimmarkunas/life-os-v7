"""D16.1 (Jim verified Oct 21; widened Nov 23, 2026, LIFE-933): every page V7 creates under a page must come from Jim's Full Width template, because Notion's public API cannot read or set a page's width.
The check reads the source (ast) of every module under lifeos/. A page creation is (a) `write_tree(...)` with `title=`, which must also pass `template=`, or (b) a POST to `/pages` whose parent is a `page_id`,
whose body must carry a `template` key (or go through `full_width.create`). A row created in a database (parent `data_source_id`) is not a page body and is out of scope.
KNOWN_NARROW is the shrink-only list of modules that still create narrow pages: the test fails when a module is added to it by accident or stays on it after it was fixed."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
KNOWN_NARROW = {
    "lifeos/interview/ui_probe.py": "QA control page: the narrow comparison for the render probe, on purpose",
    "lifeos/sources/proofs/m1m2_proof.py": "synthetic proof pages in a QA container",
    "lifeos/sources/proofs/interview_contacts_proof.py": "synthetic QA container page",
    "lifeos/sources/proofs/interview_journey_proof.py": "synthetic QA container page",
    "lifeos/interview/advisor_queue.py": "Advisor queue request page (once-only create with read-back); to move to the template",
    "lifeos/interview/advisor_store.py": "Advisor preview page (once-only create with read-back); to move to the template",
    "lifeos/interview/granola_write.py": "Granola transcript page under a round; to move to the template",
    "lifeos/jobs/private_report.py": "private report page of tables; to move to the template",
}


def _calls(source):
    tree = ast.parse(source)
    named = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    named.setdefault(target.id, []).append(node.value)
    return tree, named


def _keys(d):
    return {k.value for k in d.keys if isinstance(k, ast.Constant)}


def _parent_is_page(d):
    for key, value in zip(d.keys, d.values):
        if isinstance(key, ast.Constant) and key.value == "parent" and isinstance(value, ast.Dict):
            return "page_id" in _keys(value) or any(isinstance(v, ast.Constant) and v.value == "page_id" for v in value.values)
    return False


def pages_without_template(source):
    """-> line numbers of page creations that do not use the Full Width template."""
    tree, named = _calls(source)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", getattr(node.func, "id", "")) == "write_tree":
            names = {k.arg for k in node.keywords}
            if "title" in names and "template" not in names:
                bad.append(node.lineno)
            continue
        constants = {a.value for a in node.args if isinstance(a, ast.Constant)}
        if "/pages" not in constants or "POST" not in constants:
            continue
        bodies = [a for a in node.args if isinstance(a, ast.Dict)] + [d for a in node.args if isinstance(a, ast.Name) for d in named.get(a.id, [])]
        if any(_parent_is_page(d) and "template" not in _keys(d) for d in bodies):
            bad.append(node.lineno)
    return bad


class FullWidth(unittest.TestCase):
    def test_every_page_v7_creates_under_a_page_passes_the_full_width_template(self):
        offenders = {}
        for path in sorted((ROOT / "lifeos").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            lines = pages_without_template(path.read_text())
            if lines:
                offenders[rel] = lines
        self.assertEqual(sorted(offenders), sorted(KNOWN_NARROW), f"a page is created without the Full Width template, or a fixed module is still listed: {offenders}")

    def test_the_writers_that_must_be_full_width_are_not_on_the_narrow_list(self):
        for module in ("lifeos/dcc/weekly_archive.py", "lifeos/platform/full_width.py", "lifeos/interview/create.py"):
            self.assertNotIn(module, KNOWN_NARROW)
        self.assertIn("full_width.create", (ROOT / "lifeos/dcc/weekly_archive.py").read_text())

    def test_the_check_fails_on_a_creation_without_the_template(self):                                  # fail-then-pass: the checker itself
        self.assertEqual(pages_without_template("notion.write_tree(c, p, b, title=t)\n"), [1])
        self.assertEqual(pages_without_template("notion.write_tree(c, p, b, title=t, template=notion.FULL_WIDTH)\n"), [])
        self.assertEqual(pages_without_template("notion.write_tree(c, p, blocks)\n"), [])             # no title: it appends blocks, it creates nothing
        self.assertEqual(pages_without_template('c.call("POST", "/pages", {"parent": {"type": "page_id", "page_id": p}, "children": b})\n'), [1])
        self.assertEqual(pages_without_template('c.call_once("POST", "/pages", {"parent": {"page_id": p}, "template": {"type": "template_id"}})\n'), [])
        self.assertEqual(pages_without_template('body = {"parent": {"page_id": p}, "children": []}\nc.call_once("POST", "/pages", body)\n'), [2])
        self.assertEqual(pages_without_template('c.call("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": s}, "properties": {}})\n'), [])   # a database row


if __name__ == "__main__":
    unittest.main()
