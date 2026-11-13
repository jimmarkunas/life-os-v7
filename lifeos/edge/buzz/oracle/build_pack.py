"""LIFE-590: build the script-pack (docs/SCRIPT_PACK_RUNNER.md) that installs the renamed Buzz modules on Oracle. Run from the repo root:

    python3 lifeos/edge/buzz/oracle/build_pack.py OUTDIR [--commit SHA] [--root-prefix DIR]

The files under files/tree come from `git show <commit>:path` (default HEAD): lifeos/__init__.py, lifeos/buzz/*.py, nothing else (LIFE-631: the old-name shims are gone; the unit the pack installs says `-m lifeos.buzz.mcp`).
The release folder is named after the first 12 characters of the commit. `--root-prefix` is for tests only: every path in the pack moves under DIR."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PREVIOUS = "c423a4434c4c"


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True).stdout


def build(out, commit="HEAD", prefix=""):
    sha = git("rev-parse", commit).decode().strip()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "files" / "tree" / "lifeos" / "buzz").mkdir(parents=True)
    wanted = ["lifeos/__init__.py"]
    wanted += sorted(p for p in git("ls-tree", "-r", "--name-only", commit, "lifeos/buzz/").decode().split() if p.endswith(".py"))
    for path in wanted:
        target = out / "files" / "tree" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git("show", f"{commit}:{path}"))
    unit = git("show", f"{commit}:lifeos/edge/buzz/oracle/lifeos-buzz-mcp.service")
    (out / "files" / "lifeos-buzz-mcp.service").write_bytes(unit)
    (out / "files" / "RELEASE").write_text(sha[:12] + "\n")
    if prefix:
        (out / "files" / "ROOT").write_text(prefix + "\n")
    shutil.copytree(HERE / "pack" / "steps", out / "steps")
    base = f"{prefix}/opt/lifeos-buzz-mcp/releases/{sha[:12]}"
    manifest = {"schema": 1, "ticket": "LIFE-590", "steps": ["steps/10-release.sh", "steps/20-switch.sh", "steps/30-readback.sh"],
                "restore": {"files": [f"{prefix}/etc/systemd/system/lifeos-buzz-mcp.service"] + [f"{base}/{p}" for p in wanted], "services": ["lifeos-buzz-mcp.service"]},
                "step_timeout_seconds": 120}
    (out / "pack.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return sha


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    out, commit, prefix = argv[0], "HEAD", ""
    if "--commit" in argv:
        commit = argv[argv.index("--commit") + 1]
    if "--root-prefix" in argv:
        prefix = argv[argv.index("--root-prefix") + 1].rstrip("/")
    print("pack built for", build(out, commit, prefix)[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
