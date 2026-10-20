"""LIFE-89 (PLAT-GOV-2, Jim approved Oct 20 2026): claim a decision number before writing a decision, so parallel sessions never take the same one.

`python -m lifeos.decisions claim` finds the highest D-number in use (the `## D###` headings of docs/DECISIONS.md on the remote's main, plus every
`decision-claim/D###` tag on the remote), then tries to create the tag for the next number on the remote. Creating a ref on the remote is atomic: when
another session took that number first, the push is rejected and the next number is tried. Each claim is its own annotated tag object (a random claim id in
its message), so a second claim of the same number can never look like an up-to-date push of the same tag. Prints the claimed number, e.g. `D242`, and
nothing else. An unused claim leaves a gap, which is harmless. Fixed error codes only.

`python -m lifeos.decisions duplicates` lists any decision number used twice in docs/DECISIONS.md or docs/DECISIONS_INDEX.md (the contract test's check)."""
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

PREFIX = "decision-claim/D"
HEADING = re.compile(r"^## D(\d+)\b(.*)$", re.M)
INDEX_ENTRY = re.compile(r"^- D(\d+)\b(.*)$", re.M)
FOLLOW_UP = re.compile(r"^\s*(amendment|advice)\b", re.I)         # "## D12 amendment …", "## D6 advice …": the same decision, continued
TRIES = 10


class ClaimError(RuntimeError):
    """Fixed codes only."""


def _git(*args, cwd=None, data=None):
    return subprocess.run(["git", *args], cwd=cwd, input=data, capture_output=True, text=True, timeout=60)


def numbers(text, pattern=HEADING):
    """Every decision number a log (or, with INDEX_ENTRY, an index) uses."""
    return {int(n) for n, _ in pattern.findall(text)}


def duplicates(text, pattern=HEADING):
    """Decision numbers with more than one primary entry -> {number: [titles]}. A follow-up entry (amendment, advice) of an existing number is allowed."""
    seen = {}
    for n, rest in pattern.findall(text):
        if FOLLOW_UP.match(rest):
            continue
        seen.setdefault(int(n), []).append(rest.strip(" -—:")[:80])
    return {n: titles for n, titles in sorted(seen.items()) if len(titles) > 1}


def claimed(remote="origin", cwd=None):
    """Every number already claimed on the remote."""
    out = _git("ls-remote", "--tags", remote, f"refs/tags/{PREFIX}*", cwd=cwd)
    if out.returncode != 0:
        raise ClaimError("DECISION_CLAIM_REMOTE_UNREADABLE")
    return {int(m) for m in re.findall(rf"refs/tags/{re.escape(PREFIX)}(\d+)$", out.stdout, re.M)}


def highest(remote="origin", cwd=None, branch="main"):
    """The highest number in use: the remote main's decision log and every claim. -> (number, the remote main's commit)."""
    if _git("fetch", "--quiet", remote, branch, cwd=cwd).returncode != 0:
        raise ClaimError("DECISION_CLAIM_FETCH_FAILED")
    sha = _git("rev-parse", "FETCH_HEAD", cwd=cwd).stdout.strip()
    log = _git("show", f"{sha}:docs/DECISIONS.md", cwd=cwd)
    if log.returncode != 0 or not sha:
        raise ClaimError("DECISION_CLAIM_LOG_UNREADABLE")
    return max(numbers(log.stdout) | claimed(remote, cwd) | {0}), sha


def _claim_object(n, sha, cwd=None):
    """An annotated tag object for this one claim (its random id makes it unlike any other claim of the same number)."""
    body = (f"object {sha}\ntype commit\ntag {PREFIX}{n}\ntagger LIFE OS decisions <decisions@lifeos.invalid> {int(time.time())} +0000\n\n"
            f"LIFE-89 decision number claim {uuid.uuid4().hex}\n")
    made = _git("hash-object", "-t", "tag", "-w", "--stdin", cwd=cwd, data=body)
    if made.returncode != 0:
        raise ClaimError("DECISION_CLAIM_TAG_NOT_MADE")
    return made.stdout.strip()


def claim(remote="origin", cwd=None, tries=TRIES, start=None):
    """-> the claimed number. `start` (tests only) overrides the computed next number, to force a race."""
    top, sha = highest(remote, cwd)
    n = start if start is not None else top + 1
    for _ in range(tries):
        pushed = _git("push", "--quiet", remote, f"{_claim_object(n, sha, cwd)}:refs/tags/{PREFIX}{n}", cwd=cwd)
        if pushed.returncode == 0:
            return n
        n = max(n + 1, max(claimed(remote, cwd) | {0}) + 1)              # taken: the next number above every claim
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
        docs = Path(__file__).resolve().parents[1] / "docs"
        found = {**{f"DECISIONS.md D{n}": t for n, t in duplicates((docs / "DECISIONS.md").read_text()).items()},
                 **{f"DECISIONS_INDEX.md D{n}": t for n, t in duplicates((docs / "DECISIONS_INDEX.md").read_text(), INDEX_ENTRY).items()}}
        for where, titles in found.items():
            print(f"{where}: {' | '.join(titles)}")
        return 1 if found else 0
    print("usage: python -m lifeos.decisions claim | duplicates", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
