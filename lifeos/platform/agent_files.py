"""LIFE-593: AGENTS.md is the one source of the Concord block that the per-tool instruction files repeat.

    python -m lifeos.platform.agent_files            rewrite the block in every generated file from AGENTS.md
    python -m lifeos.platform.agent_files --check    exit 1 and name the files whose block differs from AGENTS.md (the contract test runs this)

The block is the text between `<!-- concord:start -->` and `<!-- concord:end -->`. Only that block is replaced; the rest of each file (a Cursor front-matter header, for example) is kept.
AGENTS.md holds the common text only (what Claude Code uses). A generated file's block is that common text plus, for a tool that needs it, that tool's own paragraph from PARAGRAPHS (the
Cursor file gets Cursor's). A per-tool file that carries a different, shorter block (a variant Concord's own setup writes for that tool) is listed in VARIANTS with the reason and is NOT generated
or compared. Names only; no data, no secrets."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLOCK = re.compile(r"(<!-- concord:start -->\n)(.*?)(<!-- concord:end -->)", re.S)
# generated file -> the tool whose own paragraph it carries after the common text (None: the common text only)
GENERATED = {".github/copilot-instructions.md": None, ".cursor/rules/concord.mdc": "cursor", ".codex/concord.md": None}
VARIANTS = {".agents/rules/concord.md": "a shorter block written by Concord's own setup for that tool; not built from AGENTS.md"}
# LIFE-593 (option b, Jim's decision of Nov 12 2026): AGENTS.md keeps only the Concord text Claude Code uses. What the other tools need to keep themselves reachable lives here, byte for byte as it
# was in AGENTS.md, and a file generated for a tool gets the common text plus that tool's paragraph. A tool with no generated file yet keeps its paragraph here for when one exists.
PREAMBLE = "Replace <agent-id> with the id the SessionStart hook printed for this session.\nA monitor may not inherit the session's environment, so it cannot work the id out itself."
PARAGRAPHS = {
    "grok": 'In Grok Build, keep this session reachable while idle by starting one persistent\nmonitor for `concord inbox watch --agent <agent-id> --provider grok`. Do not start a duplicate when that monitor is active.',
    "cursor": 'In Cursor, start exactly one Cursor background Shell task for `concord inbox watch --agent <agent-id> --provider cursor --once`.\nLeave it active when the turn ends. When Cursor resumes you with its completion,\nanswer the emitted peer message and immediately start a fresh background monitor.',
    "gemini": 'In Gemini CLI, start the exact monitor command supplied by the SessionStart hook\n(`concord inbox watch --agent <agent-id> --provider gemini --once`) with `run_shell_command` and `is_background: true`. Project\nsettings inject background completion into the agent. Answer the peer message,\nthen immediately start a fresh background monitor.',
    "antigravity": 'In Antigravity, start exactly one background command for `concord inbox watch --agent <agent-id> --provider antigravity --once`\nand end the turn without waiting on it. Its completion wakes the idle session:\nanswer the peer message, then immediately start a fresh background monitor.',
}


def block_of(text):
    """The text between the markers, or None when the markers are missing."""
    found = BLOCK.search(text)
    return found.group(2) if found else None


def source(root=ROOT):
    block = block_of((root / "AGENTS.md").read_text())
    if block is None:
        raise ValueError("AGENTS.md has no concord:start / concord:end block")
    return block


def expected(name, common):
    """The block a generated file must carry: the common text, plus its tool's paragraph (with the monitor preamble) when it has one."""
    tool = GENERATED[name]
    return common if tool is None else common + PREAMBLE + "\n\n" + PARAGRAPHS[tool] + "\n"


def drifted(root=ROOT):
    """-> the generated files (repo paths) whose block is missing or differs from what AGENTS.md and the table say it should be."""
    common = source(root)
    out = []
    for name in GENERATED:
        path = root / name
        if not path.exists() or block_of(path.read_text()) != expected(name, common):
            out.append(name)
    return out


def sync(root=ROOT):
    """Write each generated file's block from AGENTS.md and the table -> the files that changed."""
    common = source(root)
    changed = []
    for name in GENERATED:
        path = root / name
        text = path.read_text()
        if BLOCK.search(text) is None:
            raise ValueError(f"{name} has no concord block to replace")
        new = BLOCK.sub(lambda m: m.group(1) + expected(name, common) + m.group(3), text, count=1)
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
