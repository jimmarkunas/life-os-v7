"""LIFE-595: the docstring standard for every lifeos module, and the check that keeps it. Counts and file names only.

THE STANDARD (this docstring is the one place it is written):
1. The first paragraph is ONE line of at most FIRST_LINE_MAX (170) characters saying what the module is for; the code map shows exactly this line.
2. After a blank line, only what a reader needs to use the module without reading its code: purpose, inputs, outputs, failure codes, and a pointer (a D-number, a LIFE key or a docs file). No history: history lives in docs/DECISIONS.md.
3. The whole docstring is at most TOTAL_MAX characters.
4. A package `__init__.py` has a one-line docstring (the package summary the code map lists) and an `__all__` naming what the package exports.

THE CHECK: `problems(source, is_init)` returns fixed codes; `python -m lifeos.platform.docstyle [--strict] [path ...]` prints a count per package. BLOCKING (LIFE-595): tests/platform/test_docstyle.py fails for any package in ENFORCED (every package that has an `__init__.py`) except the files listed with a reason in EXEMPT. Imports nothing from lifeos."""
# TOTAL_MAX was set from the pilot, `lifeos/buzz`: the longest module there needs about 1,000 characters and the median is about 330. Changing a rule here changes it everywhere.
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIRST_LINE_MAX = 170
TOTAL_MAX = 1200
ENFORCED = ("lifeos/agenda", "lifeos/amazon", "lifeos/bills", "lifeos/buzz", "lifeos/calendar_bridge", "lifeos/dcc", "lifeos/delivery", "lifeos/hiring", "lifeos/imessage", "lifeos/interview", "lifeos/jira",
            "lifeos/jobs", "lifeos/megibow", "lifeos/network", "lifeos/oraclecloud", "lifeos/outlook", "lifeos/physmail", "lifeos/platform", "lifeos/recruiters", "lifeos/sources", "lifeos/telegram")   # every package with an __init__.py
# Files that are installed on the Oracle VM (or protected): a docstring edit changes the file's hash, so the deploy-drift check would show DRIFT until a script pack is run with Jim's typed `run` line. They are exempt until
# such a pack touches them anyway (then conform them and delete the entry; the test fails if an entry becomes unnecessary). A path ending in "/" exempts the whole folder.
EXEMPT = {"lifeos/edge/": "installed on the Oracle VM and the Mac helper (runner, secrets, buzzguard, close gate, backup, ticker, whatsapp packs)",
          "lifeos/whatsapp/oracle/": "installed on the Oracle VM (LIFE-643)",
          "lifeos/telegram/attention.py": "installed on the Oracle VM",
          "lifeos/imessage/alerts_endpoint.py": "installed on the Oracle VM",
          "lifeos/imessage/openclaw_imessage.py": "installed on the Oracle VM",
          "lifeos/imessage/attention_mcp.py": "hash-pinned for the Mac helper that holds Full Disk Access",
          "lifeos/platform/gate.py": "installed on the Oracle VM for the ticker (drift check)",
          "lifeos/platform/alert_registry.py": "installed on the Oracle VM for the ticker (drift check)",
          "lifeos/platform/alert_settings.py": "installed on the Oracle VM for the ticker (drift check)"}


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


def exempt(path):
    """-> True when this file is listed in EXEMPT (an exact file, or inside a folder entry ending in "/")."""
    path = Path(path)
    path = path.relative_to(ROOT) if path.is_absolute() else path
    name = path.as_posix()
    return any(name == key or (key.endswith("/") and name.startswith(key)) for key in EXEMPT)


def enforced_problems():
    """-> {file: [codes]} for the files in ENFORCED packages that break the standard and are not exempt: this is what CI blocks on."""
    found = {}
    for package in ENFORCED:
        found.update({f: c for f, c in check(ROOT / package).items() if not exempt(f)})
    return found


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    strict = "--strict" in args
    paths = [a for a in args if a != "--strict"] or sorted(str(p) for p in Path("lifeos").iterdir() if p.is_dir() and (p / "__init__.py").exists())
    total = 0
    for path in paths:
        found = {f: c for f, c in check(path).items() if not exempt(f)}
        total += len(found)
        print(f"{path}: {len(found)} file(s) off the standard (exempt files not counted)")
    print(f"docstyle: {total} file(s) off the standard")
    return 1 if strict and total else 0


if __name__ == "__main__":
    sys.exit(main())
