"""Claim a decision number before writing a decision, so parallel sessions never take the same one.

`claim` finds the highest D-number in use (the `## D###` headings of docs/DECISIONS.md and docs/archive/DECISIONS-*.md
on the remote's main plus every `decision-claim/D###` tag) and creates the tag for the next number on the remote. Ref creation is atomic: if
another session took the number first, the push is rejected and the next is tried. Prints only the claimed number;
an unused claim leaves a harmless gap. `duplicates` lists numbers used twice; `show D139` prints one decision's
section. Older decisions live in docs/archive/DECISIONS-*.md; every command reads them too. Fixed error
codes only.
"""

# Each claim is its own annotated tag object (random claim id in its message), so a repeat claim never looks like an
# up-to-date push of the same tag.
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

PREFIX = "decision-claim/D"
HEADING = re.compile(r"^## D(\d+)\b(.*)$", re.M)
INDEX_ENTRY = re.compile(r"^- D(\d+)\b(.*)$", re.M)
FOLLOW_UP = re.compile(
    r"^\s*(amendment|advice)\b", re.I
)  # "## D12 amendment ...", "## D6 advice ...": the same decision, continued
TRIES = 10


class ClaimError(RuntimeError):
    """Fixed codes only."""


def _git(*args, cwd=None, data=None):
    return subprocess.run(["git", *args], cwd=cwd, input=data, capture_output=True, text=True, timeout=60)


def log_text(docs):
    """docs/DECISIONS.md (current) followed by every docs/archive/DECISIONS-*.md (older entries), as one text."""
    parts = [Path(docs, "DECISIONS.md")] + sorted(Path(docs, "archive").glob("DECISIONS-*.md"))
    return "\n".join(part.read_text() for part in parts)


def numbers(text, pattern=HEADING):
    return {int(n) for n, _ in pattern.findall(text)}


def duplicates(text, pattern=HEADING):
    seen = {}
    for n, rest in pattern.findall(text):
        if FOLLOW_UP.match(rest):
            continue
        seen.setdefault(int(n), []).append(rest.strip(" -—:")[:80])
    return {n: titles for n, titles in sorted(seen.items()) if len(titles) > 1}


def section(text, number):
    heads = list(HEADING.finditer(text))
    out = []
    for i, head in enumerate(heads):
        if int(head.group(1)) == number:
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            out.append(text[head.start() : end].rstrip("\n"))
    return "\n\n".join(out)


def claimed(remote="origin", cwd=None):
    out = _git("ls-remote", "--tags", remote, f"refs/tags/{PREFIX}*", cwd=cwd)
    if out.returncode != 0:
        raise ClaimError("DECISION_CLAIM_REMOTE_UNREADABLE")
    return {int(m) for m in re.findall(rf"refs/tags/{re.escape(PREFIX)}(\d+)$", out.stdout, re.M)}


def highest(remote="origin", cwd=None, branch="main"):
    if _git("fetch", "--quiet", remote, branch, cwd=cwd).returncode != 0:
        raise ClaimError("DECISION_CLAIM_FETCH_FAILED")
    sha = _git("rev-parse", "FETCH_HEAD", cwd=cwd).stdout.strip()
    log = _git("show", f"{sha}:docs/DECISIONS.md", cwd=cwd)
    if log.returncode != 0 or not sha:
        raise ClaimError("DECISION_CLAIM_LOG_UNREADABLE")
    found = numbers(log.stdout)
    listing = _git("ls-tree", "--name-only", sha, "docs/archive/", cwd=cwd).stdout.split()
    for name in listing:
        if re.fullmatch(r"docs/archive/DECISIONS-[^/]+\.md", name):
            older = _git("show", f"{sha}:{name}", cwd=cwd)
            if older.returncode != 0:
                raise ClaimError("DECISION_CLAIM_LOG_UNREADABLE")
            found |= numbers(older.stdout)
    return max(found | claimed(remote, cwd) | {0}), sha


def _claim_object(n, sha, cwd=None):
    body = (
        f"object {sha}\ntype commit\ntag {PREFIX}{n}\ntagger LIFE OS decisions <decisions@example.com> {int(time.time())} +0000\n\n"
        f"LIFE-89 decision number claim {uuid.uuid4().hex}\n"
    )
    made = _git("hash-object", "-t", "tag", "-w", "--stdin", cwd=cwd, data=body)
    if made.returncode != 0:
        raise ClaimError("DECISION_CLAIM_TAG_NOT_MADE")
    return made.stdout.strip()


def claim(remote="origin", cwd=None, tries=TRIES, start=None):
    """-> the claimed number. `start` (tests only) forces a race."""
    top, sha = highest(remote, cwd)
    n = start if start is not None else top + 1
    for _ in range(tries):
        pushed = _git("push", "--quiet", remote, f"{_claim_object(n, sha, cwd)}:refs/tags/{PREFIX}{n}", cwd=cwd)
        if pushed.returncode == 0:
            return n
        n = max(n + 1, max(claimed(remote, cwd) | {0}) + 1)
    raise ClaimError("DECISION_CLAIM_FAILED")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["claim"]:
        try:
            print(f"D{claim()}")
            return 0
        except ClaimError as error:
            print(str(error), file=sys.stderr)
            return 1
    if argv == ["duplicates"]:
        docs = Path(__file__).resolve().parents[3] / "docs"
        found = {
            **{f"DECISIONS.md D{n}": t for n, t in duplicates(log_text(docs)).items()},
            **{
                f"DECISIONS_INDEX.md D{n}": t
                for n, t in duplicates((docs / "DECISIONS_INDEX.md").read_text(), INDEX_ENTRY).items()
            },
        }
        for where, titles in found.items():
            print(f"{where}: {' | '.join(titles)}")
        return 1 if found else 0
    if len(argv) == 2 and argv[0] == "show" and re.fullmatch(r"[Dd]\d+", argv[1]):
        docs = Path(__file__).resolve().parents[3] / "docs"
        found = section(log_text(docs), int(argv[1][1:]))
        print(found if found else f"{argv[1].upper()} is not in docs/DECISIONS.md or docs/archive/")
        return 0 if found else 1
    print("usage: python -m lifeos.sources.devtools.decisions claim | duplicates | show D<n>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
