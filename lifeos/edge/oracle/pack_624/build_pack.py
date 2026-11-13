"""LIFE-624 step 2: build the script-pack (docs/SCRIPT_PACK_RUNNER.md) that points the installed Close unit, installer and release tool at lifeos.sources.apps.close_app. Run from the repo root:

    python3 lifeos/edge/oracle/pack_624/build_pack.py OUTDIR [--commit SHA]

The two root-owned tools come out of `git show <commit>:<path>` (default HEAD) and the old ones out of `git show <commit>^:<path>`: the host must hold exactly the old ones, or step 10 refuses before anything changes.
The restore list names both tools and the unit, so a failed step puts them back (the runner reloads systemd after a restore)."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
TOOLS = {"install-close.sh": "lifeos/edge/oracle/install-close.sh", "close_release.py": "lifeos/edge/oracle/close_release.py"}
HOST = ["/opt/lifeos-close/bin/install-close.sh", "/opt/lifeos-close/bin/close_release.py", "/etc/systemd/system/lifeos-close@.service", "/etc/systemd/system/lifeos-close.socket"]


def git_show(ref, path):
    return subprocess.run(["git", "show", f"{ref}:{path}"], cwd=REPO, check=True, capture_output=True).stdout


def build(out, commit="HEAD"):
    sha = subprocess.run(["git", "rev-parse", commit], cwd=REPO, check=True, capture_output=True).stdout.decode().strip()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / "pack" / "steps", out / "steps")
    (out / "files").mkdir()
    expected = []
    for name, path in TOOLS.items():
        new = git_show(commit, path)
        (out / "files" / name).write_bytes(new)
        expected.append(f"new {name} {hashlib.sha256(new).hexdigest()}")
        expected.append(f"old {name} {hashlib.sha256(git_show(commit + '^', path)).hexdigest()}")
    (out / "files" / "expected.txt").write_text("\n".join(expected) + "\n")
    manifest = {"schema": 1, "ticket": "LIFE-624", "steps": ["steps/10-preflight.sh", "steps/20-apply.sh"], "restore": {"files": HOST, "services": []}, "step_timeout_seconds": 300}
    (out / "pack.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return sha


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    commit = args[args.index("--commit") + 1] if "--commit" in args else "HEAD"
    print("pack built for", build(args[0], commit)[:12])
