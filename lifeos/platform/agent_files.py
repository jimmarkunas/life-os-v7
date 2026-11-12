"""LIFE-593: AGENTS.md is the one source of the Concord block that the per-tool instruction files repeat.

    python -m lifeos.platform.agent_files            rewrite the block in every generated file from AGENTS.md
    python -m lifeos.platform.agent_files --check    exit 1 and name the files whose block differs from AGENTS.md (the contract test runs this)

The block is the text between `<!-- concord:start -->` and `<!-- concord:end -->`. Only that block is replaced; the rest of each file (a Cursor front-matter header, for example) is kept.
Generated files are exact copies of the block. A per-tool file that carries a different, shorter block (a variant Concord's own setup writes for that tool) is listed in VARIANTS with the
reason and is NOT generated or compared. Names only; no data, no secrets."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLOCK = re.compile(r"(<!-- concord:start -->\n)(.*?)(<!-- concord:end -->)", re.S)
GENERATED = (".github/copilot-instructions.md", ".cursor/rules/concord.mdc", ".codex/concord.md")
VARIANTS = {".agents/rules/concord.md": "a shorter block written by Concord's own setup for that tool; not an exact copy of the AGENTS.md block"}


def block_of(text):
    """The text between the markers, or None when the markers are missing."""
    found = BLOCK.search(text)
    return found.group(2) if found else None


def source(root=ROOT):
    block = block_of((root / "AGENTS.md").read_text())
    if block is None:
        raise ValueError("AGENTS.md has no concord:start / concord:end block")
    return block


def drifted(root=ROOT):
    """-> the generated files (repo paths) whose block is missing or differs from AGENTS.md's."""
    want = source(root)
    out = []
    for name in GENERATED:
        path = root / name
        if not path.exists() or block_of(path.read_text()) != want:
            out.append(name)
    return out


def sync(root=ROOT):
    """Write AGENTS.md's block into every generated file -> the files that changed."""
    want = source(root)
    changed = []
    for name in GENERATED:
        path = root / name
        text = path.read_text()
        if BLOCK.search(text) is None:
            raise ValueError(f"{name} has no concord block to replace")
        new = BLOCK.sub(lambda m: m.group(1) + want + m.group(3), text, count=1)
        if new != text:
            path.write_text(new)
            changed.append(name)
    return changed


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--check"]:
        bad = drifted()
        for name in bad:
            print(f"differs from AGENTS.md: {name} (run python -m lifeos.platform.agent_files)")
        return 1 if bad else 0
    if argv == []:
        changed = sync()
        print(f"agent files: {len(changed)} changed" + (": " + ", ".join(changed) if changed else ""))
        return 0
    print("usage: python -m lifeos.platform.agent_files [--check]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
