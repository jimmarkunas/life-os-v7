"""Measure prose in lifeos/: docstring and comment characters, LIFE-nnn mentions in them, long-line share.

Usage: python tools/prose_stats.py [path ...] [--top N]   (default path: lifeos; files or directories)
Docstring = first statement of a module, class or function when it is a string expression.
"""

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

TICKET = re.compile(r"LIFE-\d+")


def docstring_nodes(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                yield first.value


def file_stats(path):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    doc = 0
    refs = 0
    for node in docstring_nodes(tree):
        seg = "".join(lines[node.lineno - 1 : node.end_lineno])
        doc += len(seg)
        refs += len(TICKET.findall(seg))
    com = 0
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            com += len(tok.string)
            refs += len(TICKET.findall(tok.string))
    long_chars = sum(len(line) for line in lines if len(line.rstrip("\n")) > 160)
    return {"chars": len(text), "doc": doc, "com": com, "refs": refs, "long": long_chars}


def main(argv):
    roots = [Path(a) for a in argv[1:] if not a.startswith("--") and not a.isdigit()] or [Path("lifeos")]
    top = int(argv[argv.index("--top") + 1]) if "--top" in argv else 10
    rows = {p: file_stats(p) for r in roots for p in (sorted(r.rglob("*.py")) if r.is_dir() else [r])}
    total = {k: sum(r[k] for r in rows.values()) for k in ("chars", "doc", "com", "refs", "long")}
    prose = total["doc"] + total["com"]
    print(
        f"files={len(rows)} chars={total['chars']} docstrings={total['doc']} ({100 * total['doc'] / total['chars']:.1f}%) "
        f"comments={total['com']} ({100 * total['com'] / total['chars']:.1f}%) prose={prose} ({100 * prose / total['chars']:.1f}%)"
    )
    print(
        f"ticket_refs_in_prose={total['refs']} chars_on_lines_over_160={total['long']} ({100 * total['long'] / total['chars']:.1f}%)"
    )
    if "--files" in argv or top:
        for p, r in sorted(rows.items(), key=lambda kv: -(kv[1]["doc"] + kv[1]["com"]))[:top]:
            print(f"  {r['doc'] + r['com']:>7} prose  {r['refs']:>4} refs  {p}")


if __name__ == "__main__":
    main(sys.argv)
