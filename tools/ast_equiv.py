"""Prove a change did not alter behaviour: compare each changed .py file's AST against a base ref.

Usage: python tools/ast_equiv.py [base=origin/main] [path ...]
Docstrings (the first statement of a module, class or function when it is a string) are removed from both sides first,
so comment, docstring and whitespace edits compare equal while any code change does not. Exit 1 on the first difference.
With no paths, every .py file that differs from the base under lifeos/ and tools/ is compared.
"""

import ast
import subprocess
import sys


def strip_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return tree


def dump(source):
    return ast.dump(strip_docstrings(ast.parse(source)), include_attributes=False)


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def main(argv):
    base = argv[1] if len(argv) > 1 else "origin/main"
    paths = argv[2:] or [
        p for p in git("diff", "--name-only", base, "--", "lifeos", "tools").split() if p.endswith(".py")
    ]
    bad = []
    for path in paths:
        try:
            before = git("show", f"{base}:{path}")
        except subprocess.CalledProcessError:
            print(f"new file (not compared): {path}")
            continue
        try:
            after = open(path, encoding="utf-8").read()
        except FileNotFoundError:
            print(f"deleted: {path}")
            bad.append(path)
            continue
        if dump(before) != dump(after):
            bad.append(path)
            print(f"AST DIFFERS: {path}")
    print(f"compared {len(paths)} file(s): {'empty AST diff' if not bad else str(len(bad)) + ' differ'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv)
