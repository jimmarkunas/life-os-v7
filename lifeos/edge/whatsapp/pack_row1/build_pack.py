"""LIFE-643 row 1: build the script-pack (docs/SCRIPT_PACK_RUNNER.md) that moves the WhatsApp Attention service on Oracle to its own unprivileged user `lifeos-wa`. Run from the repo root:

    python3 lifeos/edge/whatsapp/pack_row1/build_pack.py OUTDIR [--commit SHA] [--root-prefix DIR]

Everything that is copied to the host comes out of `git show <commit>:<path>` (default HEAD): the two new unit files, attention.mjs, state.mjs, package.json and package-lock.json (the pinned Baileys
tree itself is NOT in the pack: the steps copy the installed, lock-verified tree on the host), the repointed endpoint and the Oracle card script. The steps and helpers are the ones in pack/.
`--root-prefix` is for tests only: every host path in the pack moves under DIR."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
FROM_GIT = {                                   # name in the pack's files/  ->  path in the repo
    "lifeos-wa-attention.service": "lifeos/whatsapp/oracle/lifeos-wa-attention.service",
    "lifeos-wa-attention.socket": "lifeos/whatsapp/oracle/lifeos-wa-attention.socket",
    "attention.mjs": "lifeos/whatsapp/oracle/attention.mjs",
    "state.mjs": "lifeos/whatsapp/oracle/state.mjs",
    "package.json": "lifeos/whatsapp/oracle/package.json",
    "package-lock.json": "lifeos/whatsapp/oracle/package-lock.json",
    "alerts_endpoint.py": "lifeos/imessage/alerts_endpoint.py",
    "stats.mjs": "lifeos/oraclecloud/oracle/stats.mjs",
}
ENDPOINT_HOST_PATH = "/home/ubuntu/.local/lib/lifeos-imessage/alerts_endpoint.py"
STATS_HOST_PATH = "/home/ubuntu/.local/lib/lifeos-oracle/stats.mjs"


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True).stdout


def build(out, commit="HEAD", prefix=""):
    sha = git("rev-parse", commit).decode().strip()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / "pack" / "steps", out / "steps")
    shutil.copytree(HERE / "pack" / "files", out / "files")
    for name, path in FROM_GIT.items():
        (out / "files" / name).write_bytes(git("show", f"{commit}:{path}"))
    if prefix:
        (out / "files" / "ROOT").write_text(prefix + "\n")
    manifest = {"schema": 1, "ticket": "LIFE-643",
                "steps": ["steps/10-preflight.sh", "steps/20-prepare.sh", "steps/30-switch.sh", "steps/40-readback.sh"],
                "restore": {"files": [prefix + ENDPOINT_HOST_PATH, prefix + STATS_HOST_PATH],
                            "services": ["lifeos-wa-attention.socket", "lifeos-wa-attention.service"]},
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
