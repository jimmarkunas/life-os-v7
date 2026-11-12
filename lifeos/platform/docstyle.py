"""LIFE-595: the docstring standard for every lifeos module, and the check that keeps it. Counts and file names only.

THE STANDARD (this docstring is the one place it is written):
1. The first paragraph is ONE line of at most FIRST_LINE_MAX (170) characters that says what the module is for. The code map (`python -m lifeos.codemap`) shows exactly this line.
2. After a blank line, only what a reader needs to use the module without reading its code: purpose, inputs, outputs, failure codes, and a pointer (a D-number, a LIFE key or a docs file). No history: history lives in docs/DECISIONS.md.
3. The whole docstring is at most TOTAL_MAX characters (set from the pilot, `lifeos/buzz`: the longest module there needs about 1,000 and the median is about 330).
4. A package `__init__.py` has a docstring of one line (the package summary the code map lists) and an `__all__` naming what the package exports.

THE CHECK: `problems(source, is_init)` returns fixed codes; `python -m lifeos.platform.docstyle [--strict] [path ...]` prints a count per package. It is ADVISORY until the last package follows the standard:
tests/platform/test_docstyle.py fails only for the packages listed in ENFORCED, and the last rollout PR turns that list into "every package". Changing a rule here changes it everywhere.
Imports nothing from lifeos."""
import ast
import sys
from pathlib import Path

FIRST_LINE_MAX = 170
TOTAL_MAX = 1200
ENFORCED = ("lifeos/buzz",)                         # packages that already follow the standard; one package per PR is added here


def problems(source, is_init=False):
    """-> sorted fixed codes for one file's source."""
    tree = ast.parse(source)
    doc = ast.get_docstring(tree)
    if not doc:
        return ["NO_DOCSTRING"]
    out = []
    first = doc.split("\n\n")[0]
    if "\n" in first.strip():
        out.append("FIRST_PARAGRAPH_NOT_ONE_LINE")
    if len(" ".join(first.split())) > FIRST_LINE_MAX:
        out.append("FIRST_LINE_TOO_LONG")
    if len(doc) > TOTAL_MAX:
        out.append("TOTAL_TOO_LONG")
    if is_init:
        if "\n" in doc.strip():
            out.append("INIT_NOT_ONE_LINE")
        exported = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets)]
        if not exported:
            out.append("INIT_NO_ALL")
    return sorted(out)


def check(path):
    """-> {file: [codes]} for the files under `path` that break the standard (a package directory or one file)."""
    path = Path(path)
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
    found = {}
    for f in files:
        codes = problems(f.read_text(), f.name == "__init__.py")
        if codes:
            found[str(f)] = codes
    return found


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    strict = "--strict" in args
    paths = [a for a in args if a != "--strict"] or sorted(str(p) for p in Path("lifeos").iterdir() if p.is_dir() and (p / "__init__.py").exists())
    total = 0
    for path in paths:
        found = check(path)
        total += len(found)
        print(f"{path}: {len(found)} file(s) off the standard")
    print(f"docstyle: {total} file(s) off the standard" + ("" if strict else " (advisory)"))
    return 1 if strict and total else 0


if __name__ == "__main__":
    sys.exit(main())
