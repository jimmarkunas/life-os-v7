"""Every page the Interview OS writer creates must come from Jim's Full Width template (D16.1): Notion's public API cannot read or set a page's width, only a template at creation gives it.
The check reads the source (ast): in lifeos/interview, a `write_tree(...)` call that passes `title=` creates a page, so it must also pass `template=`. ui_probe.py is exempt on purpose
(its control page is the narrow comparison for the QA render)."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXEMPT = {"ui_probe.py"}


def titled_without_template(source):
    """-> line numbers of write_tree calls that create a page (title=) with no template=."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "write_tree":
            names = {k.arg for k in node.keywords}
            if "title" in names and "template" not in names:
                bad.append(node.lineno)
    return bad


class FullWidth(unittest.TestCase):
    def test_every_page_the_writer_creates_passes_the_full_width_template(self):
        found = False
        for path in sorted((ROOT / "lifeos" / "interview").glob("*.py")):
            if path.name in EXEMPT:
                continue
            text = path.read_text()
            found = found or "template=notion.FULL_WIDTH" in text
            self.assertEqual(titled_without_template(text), [], f"{path.name}: a page is created without the Full Width template")
        self.assertTrue(found, "no creation call passes notion.FULL_WIDTH any more")

    def test_the_check_fails_on_a_creation_without_the_template(self):                                  # fail-then-pass: the checker itself
        self.assertEqual(titled_without_template("notion.write_tree(c, p, b, title=t)\n"), [1])
        self.assertEqual(titled_without_template("notion.write_tree(c, p, b, title=t, template=notion.FULL_WIDTH)\n"), [])
        self.assertEqual(titled_without_template("notion.write_tree(c, p, blocks)\n"), [])             # no title: it appends blocks, it creates nothing


if __name__ == "__main__":
    unittest.main()
