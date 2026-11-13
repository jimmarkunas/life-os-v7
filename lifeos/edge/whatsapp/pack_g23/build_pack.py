"""LIFE-647 groups 2+3: build the script-pack (docs/SCRIPT_PACK_RUNNER.md) that puts four smaller Python files on Oracle. Run from the repo root:

    python3 lifeos/edge/whatsapp/pack_g23/build_pack.py OUTDIR [--commit SHA] [--old SHA]

The new files come out of `git show <commit>:<path>` (default HEAD); the sha256 each host file must still have (the file this change replaces) is that of `git show <old>:<path>` (default <commit>^).
The manifest lists every host file in the restore list, so the runner puts them back on any failure."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
UBUNTU = "/home/ubuntu/.local/"
FILES = {"alerts_endpoint.py": ("lifeos/imessage/alerts_endpoint.py", UBUNTU + "lib/lifeos-imessage/alerts_endpoint.py"),
         "openclaw_imessage.py": ("lifeos/imessage/openclaw_imessage.py", UBUNTU + "bin/openclaw-imessage"),
         "attention.py": ("lifeos/telegram/attention.py", UBUNTU + "lib/lifeos-telegram/attention.py"),
         "forward_alerts.py": ("lifeos/whatsapp/oracle/forward_alerts.py", UBUNTU + "lib/lifeos-alerts/forward_alerts.py")}


def show(commit, path):
    return subprocess.run(["git", "show", f"{commit}:{path}"], cwd=REPO, check=True, capture_output=True).stdout


def build(out, commit="HEAD", old=None):
    sha = subprocess.run(["git", "rev-parse", commit], cwd=REPO, check=True, capture_output=True).stdout.decode().strip()
    out, old, table = Path(out), old or commit + "^", []
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / "pack" / "steps", out / "steps")
    (out / "files").mkdir()
    for name, (path, host) in FILES.items():
        new = show(commit, path)
        (out / "files" / name).write_bytes(new)
        table.append(f"{name} {host} {hashlib.sha256(show(old, path)).hexdigest()} {hashlib.sha256(new).hexdigest()}")
    (out / "files" / "table").write_text("\n".join(table) + "\n")
    manifest = {"schema": 1, "ticket": "LIFE-647", "steps": ["steps/10-install.sh"], "restore": {"files": [row.split()[1] for row in table], "services": []}, "step_timeout_seconds": 300}
    (out / "pack.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return sha


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    pick = lambda flag: args[args.index(flag) + 1] if flag in args else None
    print("pack built for", build(args[0], pick("--commit") or "HEAD", pick("--old"))[:12])
