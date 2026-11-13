"""LIFE-643 row 8: build the kill-switch script-pack (docs/SCRIPT_PACK_RUNNER.md). Run from the repo root:

    python3 lifeos/edge/whatsapp/pack_row8/build_pack.py OUTDIR

The pack declares no restore list on purpose: a restore would start the services again, which is the opposite of a kill switch."""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build(out):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / "pack" / "steps", out / "steps")
    shutil.copytree(HERE / "pack" / "files", out / "files")
    manifest = {"schema": 1, "ticket": "LIFE-643", "steps": ["steps/10-kill.sh"], "restore": {"files": [], "services": []}, "step_timeout_seconds": 120}
    (out / "pack.json").write_text(json.dumps(manifest, indent=1) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    build(sys.argv[1])
    print("pack built")
