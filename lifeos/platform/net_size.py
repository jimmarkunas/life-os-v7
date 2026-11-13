"""LIFE-625: net size of a branch by area, so every pull request can say how many lines it adds and removes.

    python -m lifeos.platform.net_size [base]        (default base: origin/main; compares base...HEAD)

Prints one block to paste into the pull request's "Net size" section: lines added, removed and net for lifeos/, tests/, docs/, workflows and other. When lifeos/ net is above zero the block
ends with a reminder to say what the change removes, or why nothing can be removed. Counts only (no file names); stdlib only; a binary file counts as zero lines."""
import subprocess
import sys
from pathlib import Path

AREAS = (("lifeos/", ("lifeos/",)), ("tests/", ("tests/",)), ("docs/", ("docs/",)), ("workflows", (".github/workflows/", ".github/actions/")), ("other", ()))
REMINDER = "lifeos/ net is above zero: say what this change removes, or why nothing can be removed."
CEILINGS = {"sources": 9400, "jobs": 8250, "platform": 7050, "interview": 4900, "edge": 3200, "jira": 1550}   # LIFE-631 (Jim, Nov 12 2026): Python lines per lifeos/ package; ADVISORY, a warning and never a failure


def area_of(path):
    for name, prefixes in AREAS[:-1]:
        if path.startswith(prefixes):
            return name
    return AREAS[-1][0]


def parse_numstat(text):
    """`git diff --numstat` output -> [(added, removed, path)]. A binary file ("-") counts as 0 and 0; a line that is not three tab-separated parts is ignored."""
    rows = []
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        rows.append((int(added) if added.isdigit() else 0, int(removed) if removed.isdigit() else 0, path))
    return rows


def totals(rows):
    """-> {area: [added, removed]} for every area, zero when nothing changed there."""
    out = {name: [0, 0] for name, _ in AREAS}
    for added, removed, path in rows:
        area = out[area_of(path)]
        area[0] += added
        area[1] += removed
    return out


def render(rows):
    found = totals(rows)
    lines = [f"- {name}: added {added}, removed {removed}, net {added - removed:+d}" for name, (added, removed) in found.items()]
    all_added = sum(added for added, _ in found.values())
    all_removed = sum(removed for _, removed in found.values())
    lines.append(f"- all: added {all_added}, removed {all_removed}, net {all_added - all_removed:+d}")
    if found["lifeos/"][0] - found["lifeos/"][1] > 0:
        lines.append(REMINDER)
    return "\n".join(lines)


def package_lines(root, package):
    """Python lines under lifeos/<package>/ in the working tree."""
    return sum(len(p.read_text().splitlines()) for p in (Path(root) / "lifeos" / package).rglob("*.py"))


def ceiling_warnings(rows, root=".", ceilings=CEILINGS):
    """LIFE-631, advisory: a line for each package that this change makes bigger while it is over its ceiling. Never an error."""
    grown = {}
    for added, removed, path in rows:
        parts = path.split("/")
        if len(parts) > 2 and parts[0] == "lifeos" and parts[1] in ceilings and path.endswith(".py"):
            grown[parts[1]] = grown.get(parts[1], 0) + added - removed
    return [f"warning (advisory): lifeos/{name} is {package_lines(root, name):,} lines against its ceiling of {ceilings[name]:,} and this change adds {net:+d}"
            for name, net in sorted(grown.items()) if net > 0 and package_lines(root, name) > ceilings[name]]


def diff(base, cwd=None, run=subprocess.run):
    """The branch's numstat against its merge base with `base` (renames are counted as a delete plus an add, so no path format has to be parsed)."""
    done = run(["git", "diff", "--numstat", "--no-renames", f"{base}...HEAD"], capture_output=True, text=True, check=True, cwd=cwd)
    return done.stdout


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    base = args[0] if args else "origin/main"
    try:
        text = diff(base)
    except (subprocess.CalledProcessError, OSError):
        print("net_size: git diff failed; is the base branch fetched (git fetch origin main)?")
        return 2
    rows = parse_numstat(text)
    print(render(rows))
    for line in ceiling_warnings(rows):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
