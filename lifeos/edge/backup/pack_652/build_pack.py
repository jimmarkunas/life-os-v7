"""LIFE-652: build the script-pack (docs/SCRIPT_PACK_RUNNER.md) that installs the fixed off-site backup program on Oracle. Run from the repo root:

    python3 lifeos/edge/backup/pack_652/build_pack.py OUTDIR [--commit SHA]

offsite_backup.py comes out of `git show <commit>:lifeos/edge/backup/offsite_backup.py` (default HEAD). old.sha256 is the sha256 of the version that is installed today: the file as of the commit before the LIFE-652 fix (OLD_COMMIT).
The manifest declares the installed file in the restore list, so the runner puts it back on any failure."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PATH = "lifeos/edge/backup/offsite_backup.py"
HOST = "/opt/lifeos-offsite/offsite_backup.py"
OLD_SHA256 = "817022e4c71bf49f8804e8b21017fc066c69521e78b20ecfefa56012081c4a2b"      # the host's file today (main before LIFE-652), checked read-only on Nov 13 2026


def build(out, commit="HEAD"):
    sha = subprocess.run(["git", "rev-parse", commit], cwd=REPO, check=True, capture_output=True).stdout.decode().strip()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / "pack" / "steps", out / "steps")
    shutil.copytree(HERE / "pack" / "files", out / "files")
    data = subprocess.run(["git", "show", f"{commit}:{PATH}"], cwd=REPO, check=True, capture_output=True).stdout
    (out / "files" / "offsite_backup.py").write_bytes(data)
    (out / "files" / "old.sha256").write_text(OLD_SHA256 + "\n")
    manifest = {"schema": 1, "ticket": "LIFE-652", "steps": ["steps/10-install.sh"], "restore": {"files": [HOST], "services": []}, "step_timeout_seconds": 600}
    (out / "pack.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return sha, hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    commit = args[args.index("--commit") + 1] if "--commit" in args else "HEAD"
    print("pack built for", build(args[0], commit)[0][:12])
