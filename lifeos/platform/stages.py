"""LIFE-768: the stage manifest. One entry per stage in lifeos/platform/stages.json is the only place a stage's metadata is written by hand.

Each entry holds seconds, required, basis (the time budget), module, status, why and exits_1 (the isolation row). Everything the other registries say about a stage is rendered from it by tools/regen.py:
lifeos/platform/stage_budgets.json (read by lifeos/platform/budget.py), the "Stage budgets" rows of docs/platform/timeouts.md and the "Every stage" rows of docs/platform/stage-isolation.md.
Adding a stage is its code and its workflow step; `sh tools/preflight.sh` runs tools/regen.py, which adds a default entry here for a stage that has none and renders the rest."""
import json
from pathlib import Path

TABLE = Path(__file__).with_name("stages.json")
ENTRY_KEYS = ("basis", "exits_1", "module", "required", "seconds", "status", "why")


def load(path=TABLE):
    return json.loads(Path(path).read_text())


def dump(data):
    data["stages"] = dict(sorted(data["stages"].items()))
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def budgets_text(data):
    """The stage_budgets.json text: default seconds, the rule doc and one {basis, required, seconds} row per stage."""
    rows = {name: {key: entry[key] for key in ("basis", "required", "seconds")} for name, entry in sorted(data["stages"].items())}
    return json.dumps({"default_seconds": data["default_seconds"], "rule": data["rule"], "stages": rows}, indent=1, ensure_ascii=False) + "\n"


def timeouts_rows(data):
    return {name: f"| `{name}` | {e['seconds']} | {'yes' if e['required'] else 'no'} | {e['basis']} |" for name, e in data["stages"].items()}


def isolation_rows(data):
    return {name: f"| `{name}` | `{e['module']}` | `{e['status']}` | {e['why']} | {'yes' if e['exits_1'] else 'no'} |" for name, e in data["stages"].items()}
