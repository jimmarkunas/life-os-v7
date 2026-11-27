"""Finds what a public repository must never hold: personal email addresses and real Notion ids.

One pure scan used by the unit test (every tracked file, plus the pull request's title, body and commit messages in
CI) and by the git hooks. A finding names the file, the line and the kind and shows at most six characters of the
value; the full value is never printed. Real Notion ids are kept out of this repository by stable names such as
`notion:daily_report` (and `lifeos.platform.notion_ids`, which gives an unconfigured name a hash that matches
nothing), so a 32-digit hex string or a UUID is a finding unless it is made up (all zeros, one repeated digit per
group, a counting run) or listed by exact value, with a reason, in `tools/public_repo_guard_allow.txt`. An email
address is a finding unless it is @example.com, .org or .net, a noreply address of Anthropic or GitHub, or listed
the same way."""

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ALLOW_FILE = os.path.join(ROOT, "tools", "public_repo_guard_allow.txt")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
ID = re.compile(
    r"(?<![0-9A-Za-z])([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})(?![0-9A-Za-z])"
)
FREE_DOMAINS = ("example.com", "example.org", "example.net")
FREE_ADDRESSES = ("noreply@anthropic.com", "noreply@github.com")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
MAX_SHOWN = 6


def made_up(value):
    """True for an invented id: two or fewer distinct digits, mostly zeros, one repeated digit per group (the UUID
    version and variant digits aside), or a counting run."""
    digits = value.replace("-", "").lower()
    core = digits[:12] + digits[13:16] + digits[17:]
    groups = [digits[:8], digits[8:12], digits[12:16][1:], digits[16:20][1:], digits[20:]]
    return (
        len(set(core)) <= 2
        or digits.count("0") >= 18
        or all(len(set(g)) == 1 for g in groups)
        or digits.startswith(("1234", "abcd", "fedc"))
    )


def shown(value):
    """At most six characters: the first three, an ellipsis and the last two."""
    return value if len(value) <= MAX_SHOWN else value[:3] + "…" + value[-2:]


def load_allowlist(path=ALLOW_FILE):
    """-> {lowercase value: reason} from lines `value | reason`; a line without a reason is an error."""
    allowed = {}
    if not os.path.exists(path):
        return allowed
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            value, bar, reason = (part.strip() for part in text.partition("|"))
            if not value or not bar or not reason:
                raise ValueError(f"{os.path.basename(path)} line {number}: every entry needs `value | reason`")
            allowed[value.lower()] = reason
    return allowed


def _email_ok(address, allowed):
    low = address.lower()
    domain = low.rsplit("@", 1)[1]
    return low in FREE_ADDRESSES or low in allowed or any(domain == d or domain.endswith("." + d) for d in FREE_DOMAINS)


def scan_line(name, number, line, allowed):
    found = []
    for match in EMAIL.finditer(line):
        if not _email_ok(match.group(0), allowed):
            found.append((name, number, "personal email", shown(match.group(0))))
    for match in ID.finditer(line):
        value = match.group(1)
        if not made_up(value) and value.lower() not in allowed:
            found.append((name, number, "notion id", shown(value)))
    return found


def scan_text(name, text, allowed=None):
    """-> [(file, line, kind, short snippet)] for every email address and id in `text` that is not allowed."""
    allowed = load_allowlist() if allowed is None else allowed
    found = []
    for number, line in enumerate(text.split("\n"), 1):
        found += scan_line(name, number, line, allowed)
    return found


def scan_diff(diff, allowed=None):
    """-> findings for the added lines of a unified diff (`git diff --unified=0`), numbered in the new file."""
    allowed = load_allowlist() if allowed is None else allowed
    found, name, number = [], "", 0
    for line in diff.split("\n"):
        if line.startswith("+++ "):
            name = line[6:] if line.startswith("+++ b/") else line[4:]
        elif HUNK.match(line):
            number = int(HUNK.match(line).group(1)) - 1
        elif line.startswith("+") and not line.startswith("+++"):
            number += 1
            found += scan_line(name, number, line[1:], allowed)
    return found


def format_finding(finding):
    name, number, kind, snippet = finding
    return f"{name}:{number}: {kind} ({snippet})"


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=ROOT).stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--staged", action="store_true", help="scan the staged changes")
    parser.add_argument("--message", metavar="FILE", help="scan a commit message file")
    args = parser.parse_args(argv)
    found = []
    if args.staged:
        found += scan_diff(_git("diff", "--cached", "--unified=0", "--no-color", "--diff-filter=ACMR"))
    if args.message:
        with open(args.message, encoding="utf-8", errors="replace") as handle:
            found += scan_text("commit message", handle.read())
    for finding in found:
        print(format_finding(finding), file=sys.stderr)
    if found:
        print(
            "public repo guard: remove it (use a stable name such as notion:daily_report, or an @example.com address);"
            " a value that is not personal goes in tools/public_repo_guard_allow.txt with a reason.",
            file=sys.stderr,
        )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
