"""LIFE-544 (Decision D18): tests/kit holds shared fakes that know nothing about any domain. A kit file may import the standard library and lifeos.platform (the platform boundary the
fakes stand in for); it may not import a domain package (lifeos.jobs, lifeos.amazon, lifeos.recruiters, ...). A fake that needs domain code lives beside that domain's tests instead."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {"lifeos.platform"}


def domain_imports(source):
    """-> sorted lifeos modules imported (anywhere in the file, including inside functions) that are not lifeos.platform[.x]."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        names = [node.module] if isinstance(node, ast.ImportFrom) and node.module else [a.name for a in node.names] if isinstance(node, ast.Import) else []
        for name in names:
            if name == "lifeos" or name.startswith("lifeos."):
                if name != "lifeos.platform" and not name.startswith("lifeos.platform."):
                    found.add(name)
    return sorted(found)


class KitIsDomainFree(unittest.TestCase):
    def test_no_kit_file_imports_a_domain_package(self):
        bad = {str(p.relative_to(ROOT)): domain_imports(p.read_text()) for p in sorted((ROOT / "tests" / "kit").glob("*.py"))}
        self.assertEqual({k: v for k, v in bad.items() if v}, {})

    def test_negative_control_a_domain_import_is_caught_and_platform_is_not(self):
        self.assertEqual(domain_imports("from lifeos.jobs import store"), ["lifeos.jobs"])
        self.assertEqual(domain_imports("def f():\n    from lifeos.amazon.stage import _row\n"), ["lifeos.amazon.stage"])
        self.assertEqual(domain_imports("import lifeos.recruiters.notion as n"), ["lifeos.recruiters.notion"])
        self.assertEqual(domain_imports("from lifeos.platform.notion_client import NotionError\nfrom lifeos.platform import ledger_body\nimport json"), [])

    def test_the_kit_folder_exists_and_has_files(self):
        self.assertTrue(list((ROOT / "tests" / "kit").glob("*.py")))


if __name__ == "__main__":
    unittest.main()
