"""LIFE-643 rows 3 and 4: build the script-pack (docs/SCRIPT_PACK_RUNNER.md) that tightens the two WhatsApp linked-device session folders on Oracle to owner-only and installs the hardened attention user unit. Run from the repo root:

    python3 lifeos/edge/whatsapp/build_pack.py OUTDIR [--commit SHA] [--root-prefix DIR]

The unit comes from `git show <commit>:lifeos/whatsapp/oracle/lifeos-whatsapp-attention.service` (default HEAD); the steps and helper files are the ones in lifeos/edge/whatsapp/pack/.
`--root-prefix` is for tests only: every host path in the pack moves under DIR."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
UNIT_REPO_PATH = "lifeos/whatsapp/oracle/lifeos-whatsapp-attention.service"
UNIT_HOST_PATH = "/home/ubuntu/.config/systemd/user/lifeos-whatsapp-attention.service"


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True).stdout


def build(out, commit="HEAD", prefix=""):
    sha = git("rev-parse", commit).decode().strip()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / "pack" / "steps", out / "steps")
    shutil.copytree(HERE / "pack" / "files", out / "files")
    (out / "files" / "lifeos-whatsapp-attention.service").write_bytes(git("show", f"{commit}:{UNIT_REPO_PATH}"))
    if prefix:
        (out / "files" / "ROOT").write_text(prefix + "\n")
    manifest = {"schema": 1, "ticket": "LIFE-643",
                "steps": ["steps/10-preflight.sh", "steps/20-apply.sh", "steps/30-readback.sh"],
                "restore": {"files": [prefix + UNIT_HOST_PATH], "services": ["lifeos-whatsapp-attention.service"]},
                "step_timeout_seconds": 300}
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
