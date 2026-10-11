"""The Daily Command Center write, proven from page markdown alone (D169). The ChatGPT-native router runs outside V7 and its Notion connector gives it the page as markdown
and one write, a search-and-replace on that markdown (no block ids, no block-local call), so safety is built from what that can show: the write is confined by construction
(the text it replaces must sit wholly inside the one DCC callout and be unique on the page), and every other region is compared before and after. V7 writes its own regions on its
own schedule, so a changed region is attributed to its owner only when the owner's own `Updated H:MM CT` stamp moved; anything else that changed is a failure. Pure functions:
`fetch()` and `update(old, new)` are injected, nothing here calls Notion, retries, schedules or stores. Fixed codes and region headings only: no page content."""
import re

from lifeos.platform import router
from lifeos.platform.router import DEGRADED, NO_ACTION, PASS, RouterError

DCC_MODULE = "daily-command-center"
STAMP = re.compile(r"Updated \d{1,2}:\d{2} [AP]M CT")
HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
SUFFIX = re.compile(r"\s*\(Updated [^)]*\)\s*$")                                     # "JIRA Execution (Updated 4:14 PM CT)" is the region "JIRA Execution"
INVISIBLE = {code: None for code in range(0x2066, 0x206A)}                          # bidi isolates Notion keeps in Amazon text
OPENERS = ("callout", "column", "columns", "details", "table")


def normalize(text):
    """Harmless serialization differences are not content: line endings, trailing space, bidi isolates, no-break spaces, escaped dollar signs."""
    text = text.replace("\r\n", "\n").translate(INVISIBLE).replace(" ", " ").replace("\\$", "$")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")


def _content(md):
    """The page body lines between <content> and </content>. A read that is cut short (no closing tag, unbalanced markup) is refused: it proves nothing."""
    lines = md.replace("\r\n", "\n").split("\n")
    marks = [i for i, line in enumerate(lines) if line.strip() in ("<content>", "</content>")]
    if marks:
        if [lines[i].strip() for i in marks] != ["<content>", "</content>"]:
            raise RouterError("READBACK_INCOMPLETE")
        lines = lines[marks[0] + 1:marks[1]]
    for tag in OPENERS:
        opened = sum(1 for line in lines if re.match(rf"\s*<{tag}[ >]", line) and not line.rstrip().endswith("/>"))
        if opened != sum(1 for line in lines if line.strip() == f"</{tag}>"):
            raise RouterError("READBACK_INCOMPLETE")
    return lines


def _regions(lines):
    """-> [(start, end, key, owner)] for the outermost callouts and the floating region(s), by tags (a table's rows are not indented, so indentation proves nothing)."""
    out, stack, headless = [], [], 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("<callout") and not s.endswith("/>"):
            stack.append(i)
        elif s == "</callout>":
            start = stack.pop()
            if stack:
                continue
            first = next((x.strip() for x in lines[start + 1:i] if x.strip()), "")
            heading = HEADING.match(first)
            if heading:
                name = SUFFIX.sub("", heading.group(1)).strip()
                out.append((start, i + 1, name, router.OWNERS.get(name)))
            else:
                headless += 1                                                         # D129: V7's stamped text above Jim's Attention view is the Attention region
                out.append((start, i + 1, f"(unmarked callout {headless})", router.OWNERS[router.ATTENTION_REGION] if STAMP.match(first) else None))
    covered = [(a, b) for a, b, _, _ in out]
    for i, line in enumerate(lines):
        heading = HEADING.match(line.strip())
        name = SUFFIX.sub("", heading.group(1)).strip() if heading else None
        if name in router.FLOATING and not any(a <= i < b for a, b in covered):
            end = next((j + 1 for j in range(i + 1, len(lines)) if lines[j].strip() == "</table>"), None)
            if end is None:
                raise RouterError("READBACK_INCOMPLETE")
            out.append((i, end, name, router.OWNERS[name]))
    return sorted(out)


def locate(lines):
    """-> (start, end) of the one DCC callout: its opening tag line and the line after its closing tag. Missing and ambiguous are refused, never created or guessed."""
    found = [(name, str(start)) for start, _, name, _ in _regions(lines)]
    start = int(router.resolve_target([(n, b) for n, b in found if not n.startswith("(unmarked")], DCC_MODULE))
    return next((a, b) for a, b, _, _ in _regions(lines) if a == start)


def snapshot(md):
    """Every region outside the DCC callout as normalized text with its owner, plus the rest of the page (layout, dividers, columns, unowned text) with each region reduced to a marker."""
    lines = _content(md)
    regions, skeleton, cursor, seen = {}, [], 0, {}
    for start, end, name, owner in _regions(lines):
        seen[name] = seen.get(name, 0) + 1
        key = name if seen[name] == 1 else f"{name}#{seen[name]}"
        skeleton += lines[cursor:start]
        skeleton.append(f"<<{key}>>")
        cursor = end
        if owner != DCC_MODULE:
            regions[key] = (owner, normalize("\n".join(lines[start:end])))
    skeleton += lines[cursor:]
    return {"regions": regions, "skeleton": normalize("\n".join(skeleton))}


def _body(lines, span):
    start, end = span
    return lines[start + 2:end - 1]


def plan(md, intended):
    """-> {"action": "NO_ACTION"} when the DCC body already says this, else {"action": "WRITE", "old": str, "new": str}: the smallest run of lines that differs, widened with its
    neighbours inside the DCC callout until it is unique on the page. If it cannot be proven unique and inside the callout the write is refused (DCC_WRITE_BLOCKED)."""
    lines = _content(md)
    span = locate(lines)
    start, end = span
    indent = re.match(r"\s*", lines[start + 1]).group()
    old = _body(lines, span)
    new = [indent + line if line.strip() else line for line in intended.strip("\n").split("\n")]
    if normalize("\n".join(old)) == normalize("\n".join(new)):
        return {"action": NO_ACTION}
    head = 0
    while head < min(len(old), len(new)) and normalize(old[head]) == normalize(new[head]):
        head += 1
    tail = 0
    while tail < min(len(old), len(new)) - head and normalize(old[-1 - tail]) == normalize(new[-1 - tail]):
        tail += 1
    lo, hi = start + 2 + head, end - 1 - tail                                          # the lines of `lines` to replace: [lo, hi)
    replacement = new[head:len(new) - tail]
    if lo == hi:                                                                       # pure insertion: anchor on the line before it (the heading when the body is empty)
        lo -= 1
        replacement = [lines[lo]] + replacement
    page = "\n".join(lines)
    low, high = start + 1, end - 1                                                      # the callout's own lines: heading through last body line
    prefix, suffix = [], []
    while page.count("\n".join(lines[lo:hi])) != 1:
        if lo > low:
            lo -= 1
            prefix.insert(0, lines[lo])
        elif hi < high:
            suffix.append(lines[hi])
            hi += 1
        else:
            raise RouterError("DCC_WRITE_BLOCKED")
    return {"action": "WRITE", "old": "\n".join(lines[lo:hi]), "new": "\n".join(prefix + replacement + suffix)}


def verify(before, after_md, intended):
    """before: snapshot() taken immediately before the write. -> {"outcome", "code", "external"}; the DCC text must be the intended text and nothing outside it may have changed
    except a region whose owner refreshed it (its own stamp moved), which is reported and never blamed on this module."""
    lines = _content(after_md)
    span = locate(lines)
    indent = re.match(r"\s*", lines[span[0] + 1]).group()
    got = normalize("\n".join(line[len(indent):] if line.startswith(indent) else line for line in _body(lines, span)))
    if got != normalize(intended):
        return _result(DEGRADED, "DCC_CONTENT_NOT_PRESENT")
    now = snapshot(after_md)
    external = []
    if now["skeleton"] != before["skeleton"]:
        return _result(DEGRADED, "PROTECTED_REGION_VERIFICATION_FAILED")
    for key in sorted(set(before["regions"]) | set(now["regions"])):
        was, is_ = before["regions"].get(key), now["regions"].get(key)
        if was == is_:
            continue
        if was and is_ and was[0] and is_[0] == was[0] and _stamp(was[1]) and _stamp(is_[1]) and _stamp(was[1]) != _stamp(is_[1]):
            external.append(key)                                                        # its owner wrote it while we worked: noted, not ours
            continue
        return _result(DEGRADED, "PROTECTED_REGION_VERIFICATION_FAILED")
    return _result(PASS, "DCC_UPDATED", external)


def _stamp(text):
    found = STAMP.search(text)
    return found.group() if found else None


def _result(outcome, code, external=(), written=None):
    return {"outcome": outcome, "code": code, "external": list(external), "written": written}      # written: True | False | None (unknown: the write failed in a way that leaves it uncertain)


def run(fetch, update, intended):
    """One attempt, no retry: fetch, plan, (write), fetch, verify. fetch() -> page markdown; update(old, new) writes. -> {"outcome", "code", "external", "written"}."""
    try:
        before = fetch()
        step = plan(before, intended)
        if step["action"] == NO_ACTION:
            return _result(NO_ACTION, "DCC_UNCHANGED", written=False)
        baseline = snapshot(before)
    except RouterError as error:
        return _result(DEGRADED, str(error), written=False)
    except Exception:
        return _result(DEGRADED, "DCC_READ_FAILED", written=False)
    try:
        update(step["old"], step["new"])
    except Exception:
        return _result(DEGRADED, "DCC_WRITE_BLOCKED", written=None)                    # an uncertain write is never repeated
    try:
        return {**verify(baseline, fetch(), intended), "written": True}
    except RouterError as error:
        return _result(DEGRADED, str(error), written=True)
    except Exception:
        return _result(DEGRADED, "READBACK_FAILED", written=True)
