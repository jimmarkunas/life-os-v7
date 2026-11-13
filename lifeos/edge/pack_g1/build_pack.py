"""LIFE-647 group 1: build the script-pack (docs/SCRIPT_PACK_RUNNER.md) that installs four trimmed programs on Oracle. Run from the repo root:

    python3 lifeos/edge/pack_g1/build_pack.py OUTDIR [--commit SHA]

The new files come out of `git show <commit>:<path>` (default HEAD). OLD is the sha256 of the main file the host holds today (main 53a231a1); the pack stops if the host holds neither it nor the new file."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
# name: (repo path, host path, sha256 of the old main file)
FILES = {
    "offsite_backup.py": ("lifeos/edge/backup/offsite_backup.py", "/opt/lifeos-offsite/offsite_backup.py", "817022e4c71bf49f8804e8b21017fc066c69521e78b20ecfefa56012081c4a2b"),
    "ticker.py": ("lifeos/edge/ticker/ticker.py", "/opt/lifeos-ticker/ticker.py", "5486c3d413ff8b9f74b5d2ef874e854ee888f4f20787f473c0e1f5550debc932"),
    "close_release.py": ("lifeos/edge/oracle/close_release.py", "/opt/lifeos-close/bin/close_release.py", "429a753308af03b1510f267b39fdb28f04974a893a26b17020e60f39763f0b0f"),
    "gran_helper.py": ("lifeos/edge/granola/gran_helper.py", "/opt/lifeos-gran/gran_helper.py", "28695afc343d7b399ed28275fa8d194a5293186dae0076ee93b6f7c6158bd77d"),
}


def build(out, commit="HEAD"):
    sha = subprocess.run(["git", "rev-parse", commit], cwd=REPO, check=True, capture_output=True).stdout.decode().strip()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / "pack", out)
    rows = []
    for name, (repo_path, host, old) in FILES.items():
        data = subprocess.run(["git", "show", f"{commit}:{repo_path}"], cwd=REPO, check=True, capture_output=True).stdout
        (out / "files" / name).write_bytes(data)
        rows.append("|".join((name, host, old, hashlib.sha256(data).hexdigest())))
    (out / "files" / "table").write_text("# name|host path|old sha256|new sha256\n" + "\n".join(rows) + "\n")
    manifest = {"schema": 1, "ticket": "LIFE-647", "steps": ["steps/10-install.sh"], "restore": {"files": [v[1] for v in FILES.values()], "services": []}, "step_timeout_seconds": 300}
    (out / "pack.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return sha


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    print("pack built for", build(args[0], args[args.index("--commit") + 1] if "--commit" in args else "HEAD")[:12])
