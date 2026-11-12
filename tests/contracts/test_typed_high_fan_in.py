"""LIFE-594: the three platform modules that the most other code imports (notion_client, db, gmail) are fully annotated, with the standard library only, and the advisory type checker (LIFE-559) stays
advisory. The helpers are the ones LIFE-559's contract test uses; that test is not edited."""
import ast
import sys
import unittest

from tests.contracts.test_mypy_advisory import ROOT, unannotated

MODULES = ("notion_client", "gmail", "db")

def functions(module):
    tree = ast.parse((ROOT / "lifeos" / "platform" / f"{module}.py").read_text())
    return tree, [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


class ThreeModulesAreTyped(unittest.TestCase):
    def test_every_function_and_method_in_the_three_modules_is_fully_annotated(self):
        gaps = {}
        for module in MODULES:
            for node in functions(module)[1]:
                missing = unannotated(node)
                if missing:
                    gaps[f"{module}.{node.name}"] = missing
        self.assertEqual(gaps, {})

    def test_notion_client_which_ships_in_the_oracle_close_tree_imports_only_the_standard_library_and_limits(self):
        tree, _ = functions("notion_client")
        for node in ast.walk(tree):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names] if isinstance(node, ast.Import) else []
            for name in names:
                self.assertTrue(name.split(".")[0] in sys.stdlib_module_names or name == "lifeos.platform" or name == "lifeos.platform.limits", name)

    def test_notion_client_defers_annotation_evaluation_so_the_oracle_close_tree_runs_on_an_older_python(self):
        tree, _ = functions("notion_client")
        self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module == "__future__" and any(a.name == "annotations" for a in n.names) for n in tree.body))

    def test_negative_control_an_unannotated_function_is_reported(self):
        node = next(n for n in ast.walk(ast.parse("def f(a, b: int):\n    return a\n")) if isinstance(n, ast.FunctionDef))
        self.assertEqual(unannotated(node), ["a", "return"])


if __name__ == "__main__":
    unittest.main()
