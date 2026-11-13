"""LIFE-643 cleanup: build the script-pack (docs/SCRIPT_PACK_RUNNER.md) that removes what the WhatsApp move left behind on Oracle (old session copy, old program folder, old user unit, pre-slim tree) and drops the
old-socket fallback from the two consumer files. Run from the repo root:

    python3 lifeos/edge/whatsapp/pack_cleanup/build_pack.py OUTDIR [--commit SHA]

The two consumer files come out of `git show <commit>:<path>` (default HEAD). The manifest declares both consumer files in the restore list (the runner puts them back on any failure); the deletions cannot be restored, by design."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
FROM_GIT = {"alerts_endpoint.py": "lifeos/imessage/alerts_endpoint.py", "stats.mjs": "lifeos/oraclecloud/oracle/stats.mjs"}
HOST = ["/home/ubuntu/.local/lib/lifeos-imessage/alerts_endpoint.py", "/home/ubuntu/.local/lib/lifeos-oracle/stats.mjs"]


def build(out, commit="HEAD"):
    sha = subprocess.run(["git", "rev-parse", commit], cwd=REPO, check=True, capture_output=True).stdout.decode().strip()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / "pack" / "steps", out / "steps")
    shutil.copytree(HERE / "pack" / "files", out / "files")
    for name, path in FROM_GIT.items():
        (out / "files" / name).write_bytes(subprocess.run(["git", "show", f"{commit}:{path}"], cwd=REPO, check=True, capture_output=True).stdout)
    manifest = {"schema": 1, "ticket": "LIFE-643", "steps": ["steps/10-cleanup.sh"], "restore": {"files": HOST, "services": []}, "step_timeout_seconds": 300}
    (out / "pack.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return sha


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    commit = args[args.index("--commit") + 1] if "--commit" in args else "HEAD"
    print("pack built for", build(args[0], commit)[:12])
