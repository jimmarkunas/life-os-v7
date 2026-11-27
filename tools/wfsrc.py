"""LIFE-1010: how the big workflows are built from their source parts, and the table of why each step exists.

The parts under `.github/workflows-src/<workflow>/` are the files people edit; `.github/workflows/<workflow>.yml` is generated from them by tools/regen.py and carries no comments. A step's history (tickets, decisions, why it is written this way) lives in a `# why:` comment line directly above the step in its part; `docs/workflows.md` is generated from those lines, so the step name stays short and the history is still one click away.

  strip_comments(text)   the YAML text without comments, proved to parse to the same document (any doubt keeps the text as it was)
  compress(text)         the same document with repeated values and repeated `env:` / `with:` blocks written once as YAML anchors and aliases (LIFE-1013), kept only when it parses to the same document and `expand_aliases` gives the text back exactly
  expand_aliases(text)   the inverse: the plain text, for code that reads a generated workflow as text
  why_rows(parts)        [(workflow, job, step, tickets, why)] from the `# why:` lines of (workflow, file name, text) parts
  docs_text(rows)        the generated docs/workflows.md"""
import collections
import re

import yaml

TICKET = re.compile(r"\b(?:LIFE-\d+|NET-\d+(?:\.\d+)?|EDGE-\d+(?:\.\d+)?|D\d+)\b")
JOB = re.compile(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$")
BLOCK = re.compile(r"^(\s*)(- )?[\w.-]+: [|>][-+0-9]*\s*(?:#.*)?$")
STEP = re.compile(r"^      - ")


def _strip(text, inline):
    out, block = [], None
    for line in text.split("\n"):
        body = line.lstrip()
        indent = len(line) - len(body)
        if block is not None:
            if not body or indent > block:
                out.append(line)
                continue
            block = None
        if body.startswith("#"):
            continue
        head = BLOCK.match(line)
        if head:
            block = len(head.group(1)) + (2 if head.group(2) else 0)
        elif inline:
            line = re.sub(r"\s+#\s.*$", "", line)
        out.append(line)
    return "\n".join(out)


def strip_comments(text):
    """-> `text` without comments when that parses to the very same document (inline ones first, then whole-line ones only), else `text` itself."""
    want = yaml.safe_load(text)
    for inline in (True, False):
        stripped = _strip(text, inline)
        if yaml.safe_load(stripped) == want:
            return stripped
    return text


SCALAR = re.compile(r"^(\s*(?:- )?[\w.\-]+): (?!\||>|&|\*|\{|\[|#)(\S.*)$")
SCALAR_ANCHOR = re.compile(r"^(\s*(?:- )?[\w.\-]+): &(a\d+) (.*)$")
SCALAR_ALIAS = re.compile(r"^(\s*(?:- )?[\w.\-]+): \*(a\d+)$")
BLOCK_HEAD = re.compile(r"^(\s*)(env|with):\s*$")
BLOCK_ANCHOR = re.compile(r"^(\s*)(env|with): &(m\d+)$")
BLOCK_ALIAS = re.compile(r"^(\s*)(env|with): \*(m\d+)$")
MIN_VALUE, MIN_BLOCK = 14, 40


def _literal(lines):
    """-> a flag per line: True when the line is inside a block scalar (a `run: |` script), which compression never touches."""
    flags, block = [], None
    for line in lines:
        body = line.lstrip()
        indent = len(line) - len(body)
        if block is not None and (not body or indent > block):
            flags.append(True)
            continue
        block = None
        flags.append(False)
        head = BLOCK.match(line)
        if head:
            block = len(head.group(1)) + (2 if head.group(2) else 0)
    return flags


def _blocks(text):
    lines = text.split("\n")
    inside = _literal(lines)
    found, i = [], 0
    while i < len(lines):
        head = BLOCK_HEAD.match(lines[i])
        if head and not inside[i]:
            width, j = len(head.group(1)), i + 1
            while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > width):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            found.append((i, j, head.group(1), head.group(2), "\n".join(lines[i + 1:j])))
            i = j
        else:
            i += 1
    count = collections.Counter((key, body) for _, _, _, key, body in found)
    out, last, names = [], 0, {}
    for i, j, indent, key, body in found:
        out += lines[last:i]
        last = j
        if count[(key, body)] > 1 and len(body) > MIN_BLOCK:
            if (key, body) not in names:
                names[(key, body)] = f"m{len(names) + 1}"
                out += [f"{indent}{key}: &{names[(key, body)]}"] + body.split("\n")
            else:
                out.append(f"{indent}{key}: *{names[(key, body)]}")
        else:
            out += lines[i:j]
    return "\n".join(out + lines[last:])


def _scalars(text):
    lines = text.split("\n")
    inside = _literal(lines)

    def plain(at):
        found = SCALAR.match(lines[at])
        nxt = lines[at + 1] if at + 1 < len(lines) else ""
        deeper = nxt.strip() and len(nxt) - len(nxt.lstrip()) > len(lines[at]) - len(lines[at].lstrip())
        return found if found and not inside[at] and not deeper and len(found.group(2)) >= MIN_VALUE else None

    count = collections.Counter(found.group(2) for at in range(len(lines)) if (found := plain(at)))
    names, out = {}, []
    for at, line in enumerate(lines):
        found = plain(at)
        if found and count[found.group(2)] > 1:
            value = found.group(2)
            if value not in names:
                names[value] = f"a{len(names) + 1}"
                line = f"{found.group(1)}: &{names[value]} {value}"
            else:
                line = f"{found.group(1)}: *{names[value]}"
        out.append(line)
    return "\n".join(out)


def compress(text):
    """-> `text` with its repeats written as anchors and aliases when that is provably the same document, else `text`."""
    packed = _scalars(_blocks(text))
    if yaml.safe_load(packed) == yaml.safe_load(text) and expand_aliases(packed) == text:
        return packed
    return text


def expand_aliases(text):
    """-> the plain text `compress` started from."""
    values, lines = {}, []
    for line in text.split("\n"):
        anchor, alias = SCALAR_ANCHOR.match(line), SCALAR_ALIAS.match(line)
        if anchor:
            values[anchor.group(2)] = anchor.group(3)
            line = f"{anchor.group(1)}: {anchor.group(3)}"
        elif alias:
            line = f"{alias.group(1)}: {values[alias.group(2)]}"
        lines.append(line)
    blocks, out, at = {}, [], 0
    while at < len(lines):
        line = lines[at]
        anchor, alias = BLOCK_ANCHOR.match(line), BLOCK_ALIAS.match(line)
        if anchor:
            width, end = len(anchor.group(1)), at + 1
            while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > width):
                end += 1
            while end > at + 1 and not lines[end - 1].strip():
                end -= 1
            blocks[anchor.group(3)] = lines[at + 1:end]
            out += [f"{anchor.group(1)}{anchor.group(2)}:"] + lines[at + 1:end]
            at = end
            continue
        out += [f"{alias.group(1)}{alias.group(2)}:"] + blocks[alias.group(3)] if alias else [line]
        at += 1
    return "\n".join(out)


def why_rows(parts):
    """-> [(workflow, job, step, tickets, why)] in file order; `step` is the step's id, else its name."""
    rows = []
    for workflow, name, text in parts:
        job = re.sub(r"^\d+[a-z]?-", "", name.rsplit(".", 1)[0])
        lines = text.split("\n")
        for at, line in enumerate(lines):
            found = JOB.match(line)
            if found and workflow == "v3-pages":
                job = found.group(1)
            why = re.match(r"^\s+# why: (.*)$", line)
            if not why:
                continue
            end = next((i for i in range(at + 2, len(lines)) if STEP.match(lines[i])), len(lines))
            body = "\n".join(lines[at + 1:end])
            ident = re.search(r"^\s+(?:- )?id: (\S+)", body, re.M) or re.search(r"^\s+(?:- )?name: (.+)$", body, re.M)
            text_why = why.group(1).strip()
            rows.append((workflow, job, ident.group(1).strip("\"'") if ident else "?", ", ".join(dict.fromkeys(TICKET.findall(text_why))), text_why))
    return rows


def docs_text(rows):
    """-> docs/workflows.md: one table per workflow, a row per step that has a `# why:` line."""
    out = ["# Workflow steps: why each is written the way it is", "",
           "Generated by `python tools/regen.py` from the `# why:` lines in `.github/workflows-src/` (LIFE-1010). Edit the line above the step in its part, never this file. Step names in the workflows stay at 80 characters or fewer; the history that used to sit in the names is here.", ""]
    for workflow in dict.fromkeys(r[0] for r in rows):
        out += [f"## {workflow}", "", "| Job | Step | Tickets | Why |", "| --- | --- | --- | --- |"]
        out += [f"| {j} | {s} | {t} | {w.replace('|', '/')} |" for wf, j, s, t, w in rows if wf == workflow]
        out.append("")
    return "\n".join(out)
