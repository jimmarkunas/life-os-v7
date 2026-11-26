"""The stage manifest: lifeos/platform/stages.json, one entry per stage, is the only hand-written stage metadata.
tools/regen.py renders the rest from it (stage_budgets.json and the stage rows of docs/platform/timeouts.md and
stage-isolation.md); `sh tools/preflight.sh` adds a default entry for a stage that has none.
"""
import json
from pathlib import Path

TABLE = Path(__file__).with_name("stages.json")
ENTRY_KEYS = ("basis", "exits_1", "module", "required", "seconds", "status", "why")
# the key in a stage's own counts that is its item count (a whole number); absent means unknown, never zero
OPTIONAL_KEYS = ("items_key",)


def load(path=TABLE):
    return json.loads(Path(path).read_text())


def dump(data):
    data["stages"] = dict(sorted(data["stages"].items()))
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def budgets_text(data):
    rows = {name: {key: entry[key] for key in ("basis", "required", "seconds")} for name, entry in sorted(data["stages"].items())}
    return json.dumps({"default_seconds": data["default_seconds"], "rule": data["rule"], "stages": rows}, indent=1, ensure_ascii=False) + "\n"


def timeouts_rows(data):
    return {name: f"| `{name}` | {e['seconds']} | {'yes' if e['required'] else 'no'} | {e['basis']} |" for name, e in data["stages"].items()}


def isolation_rows(data):
    return {name: f"| `{name}` | `{e['module']}` | `{e['status']}` | {e['why']} | {'yes' if e['exits_1'] else 'no'} |" for name, e in data["stages"].items()}
