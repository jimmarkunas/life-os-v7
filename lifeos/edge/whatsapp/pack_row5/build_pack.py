"""LIFE-643 row 5: build the script-pack (docs/SCRIPT_PACK_RUNNER.md) that replaces the npm tree of the new WhatsApp app (/opt/lifeos-wa/app/node_modules, made by the row 1 pack) with one built from the committed lockfile
without the optional and peer packages. Run from the repo root:

    python3 lifeos/edge/whatsapp/pack_row5/build_pack.py OUTDIR [--commit SHA] [--root-prefix DIR]

package.json and package-lock.json come out of `git show <commit>:lifeos/whatsapp/oracle/...` (default HEAD). The shared shell helpers and the file helper are the row 1 ones (pack_row1/pack/files), copied in, not duplicated.
`--root-prefix` is for tests only: every host path in the pack moves under DIR."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
ROW1_FILES = HERE.parent / "pack_row1" / "pack" / "files"
FROM_GIT = {"package.json": "lifeos/whatsapp/oracle/package.json", "package-lock.json": "lifeos/whatsapp/oracle/package-lock.json"}


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True).stdout


def build(out, commit="HEAD", prefix=""):
    sha = git("rev-parse", commit).decode().strip()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / "pack" / "steps", out / "steps")
    shutil.copytree(HERE / "pack" / "files", out / "files")
    for name in ("common.sh", "wa_row1.py"):
        shutil.copy(ROW1_FILES / name, out / "files" / name)
    for name, path in FROM_GIT.items():
        (out / "files" / name).write_bytes(git("show", f"{commit}:{path}"))
    if prefix:
        (out / "files" / "ROOT").write_text(prefix + "\n")
    manifest = {"schema": 1, "ticket": "LIFE-643",
                "steps": ["steps/10-preflight.sh", "steps/20-build.sh", "steps/30-swap.sh", "steps/40-readback.sh"],
                "restore": {"files": [], "services": ["lifeos-wa-attention.socket", "lifeos-wa-attention.service"]},
                "step_timeout_seconds": 600}
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
