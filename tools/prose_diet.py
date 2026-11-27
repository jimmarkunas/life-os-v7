"""LIFE-1019: shorten the comments and docstrings of Python files so they say what and why now (tests/ and anywhere else).

Usage: python tools/prose_diet.py [--write] path ...      (files or directories; prints characters before and after)
Ticket numbers, dates, who decided, and history sentences are dropped; a comment or docstring keeps its first sentence. Pragmas (`# noqa`, `# type:`, `# fmt:`, `# pragma`, `# ruff`, shebangs) stay. Code, string data and test names are never touched; prove it with tools/ast_equiv.py."""

import ast
import io
import re
import sys
import textwrap
import tokenize
from pathlib import Path

REF = r"(?:LIFE|NET|EDGE|TL|COMM|RECR|NOTION)-\d+(?:\.\d+)*[a-z]?|\bD\d+(?:\.\d+)?\b|\bG\d+\b|\bTL \d+\b"
PEOPLE = r"Jim(?:'s)?|Claude|Codex|ChatGPT|OC|MC|MC0\d"
HIST = re.compile(
    r"\b(previously|used to|no longer|formerly|moved (?:from|to|out)|replaced|reverted|rollback|since then|before this"
    r"|(?:Oct|Nov|Dec|Jan) \d{1,2}(?:, \d{4}| \d{4})?)\b|\b(?:%s)\b[ ,]*(?:said|asked|decided|approved|wants|chose)"
    % PEOPLE,
    re.I,
)
PRAGMA = re.compile(r"^#\s*(?:noqa|type:|fmt:|pragma|ruff:|pylint|nosec|!)", re.I)


def clean(text, limit, sentences_kept=2):
    t = text
    nested = r"(?:[^()]|\((?:[^()]|\([^()]*\))*\))*"
    t = re.sub(r"\(%s(?:%s|\b(?:%s)\b)%s\)" % (nested, REF, PEOPLE, nested), "", t)
    t = re.sub(r"^(?:%s)(?:[ ,/]+(?:%s))*\s*[:\-–—]\s*" % (REF, REF), "", t)
    t = re.sub(r"(?:%s)(?:\s+(?:comment|PR|row)s?\s+\d+[a-z]?)?" % REF, "", t)
    t = re.sub(r"\s+([,.;:)])", r"\1", t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;:-–—")
    sentences = [s for s in re.split(r"(?<=[.;?!])\s+", t) if s and not HIST.search(s)]
    t = " ".join(sentences[:sentences_kept]).strip(" ;")
    if t.count("(") > t.count(")"):
        t += ")"
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-(") + "." if limit >= 100 else ""
    return t


def comment_edits(source):
    """-> [(first line, last line, new lines)] over 1-based inclusive line ranges, for comment-only blocks and trailing comments."""
    lines = source.split("\n")
    comments = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            comments.append(token)
    edits, used = [], set()
    by_line = {t.start[0]: t for t in comments}
    for number in sorted(by_line):
        if number in used:
            continue
        token = by_line[number]
        line = lines[number - 1]
        if line.lstrip().startswith("#"):
            indent = len(line) - len(line.lstrip())
            end = number
            block = [token.string]
            while (
                end + 1 in by_line
                and lines[end].lstrip().startswith("#")
                and len(lines[end]) - len(lines[end].lstrip()) == indent
            ):
                end += 1
                block.append(by_line[end].string)
            used.update(range(number, end + 1))
            if any(PRAGMA.match(b) for b in block) or (number == 1 and block[0].startswith("#!")):
                continue
            text = clean(" ".join(b.lstrip("#").strip() for b in block), 110, 1)
            new = [" " * indent + "# " + w for w in textwrap.wrap(text, 118 - indent)] if text else []
            edits.append((number, end, new))
        else:
            if PRAGMA.match(token.string):
                bare = re.sub(r"\s*\([^()]*(?:%s)[^()]*\)" % REF, "", token.string)
                if bare != token.string:
                    edits.append((number, number, [line[: token.start[1]] + bare]))
                continue
            code = line[: token.start[1]].rstrip()
            text = clean(token.string.lstrip("#").strip(), 50, 1)
            edits.append((number, number, [code + (f"  # {text}" if text else "")]))
    return edits


def docstring_edits(source):
    edits = []
    tree = ast.parse(source)
    lines = source.split("\n")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                raw = "".join(lines[first.lineno - 1 : first.end_lineno])
                if re.match(r"\s*[rRbBuUfF]", raw) or '"""' not in raw:
                    continue
                text = clean(" ".join(first.value.value.split()), 150, 1)
                text = text.replace('"', "'")
                if "\\" in text:
                    continue
                indent = " " * first.col_offset
                if not text:
                    new = []
                elif len(text) + first.col_offset + 6 <= 118:
                    new = [f'{indent}"""{text}"""']
                else:
                    wrapped = textwrap.wrap(text, 118 - first.col_offset)
                    new = [f'{indent}"""{wrapped[0]}'] + [indent + w for w in wrapped[1:]] + [indent + '"""']
                trailing = lines[first.end_lineno - 1][first.end_col_offset :]
                if trailing.strip():
                    continue
                if not new and len(node.body) == 1:
                    new = [f"{indent}pass"]
                edits.append((first.lineno, first.end_lineno, new))
    return edits


def diet(source):
    edits = docstring_edits(source) + comment_edits(source)
    lines = source.split("\n")
    for start, end, new in sorted(edits, key=lambda e: -e[0]):
        lines[start - 1 : end] = new
    return "\n".join(lines)


def main(argv):
    write = "--write" in argv
    paths = [Path(a) for a in argv if not a.startswith("--")]
    files = [f for p in paths for f in ([p] if p.is_file() else sorted(p.rglob("*.py")))]
    before = after = 0
    for f in files:
        text = f.read_text()
        try:
            new = diet(text)
            ast.parse(new)
        except (SyntaxError, tokenize.TokenError):
            new = text
        before += len(text)
        after += len(new)
        if write and new != text:
            f.write_text(new)
    print(f"{len(files)} files, {before} -> {after} characters ({before - after} fewer)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
