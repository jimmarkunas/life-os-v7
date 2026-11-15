"""LIFE-726: add the registry rows a new stage or package needs, so a new stage is one line in lifeos/run.py plus `python tools/regen.py`.

Rows the contract tests mirror from code (they keep checking them; this writes the missing ones and drops the stale ones, and never rewrites a row that already exists):
  lifeos/platform/stage_budgets.json     one budget row per stage (default 600 s, capped by the job limit that runs it; required follows the workflow step)
  docs/platform/timeouts.md              the "Stage budgets" row per stage, from that file
  docs/platform/stage-isolation.md       the "Every stage" row per stage (single when the module has no loop over items, guarded when every loop has a try)
  lifeos/platform/docstyle.py            ENFORCED lists every package with an __init__.py
  lifeos/platform/runtime.json           one row per top-level unit under lifeos/ (github host unless said otherwise by hand)
  docs/platform/module-inventory.md      regenerated from runtime.json

    python tools/regen.py            write the changes and print what changed
    python tools/regen.py --check    change nothing; exit 1 when something is missing (for CI or a quick look)

A stage whose module loops over external items with no try inside is not classified here: that needs a human verdict (docs/platform/stage-isolation.md); the script says which."""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lifeos import run                                                    # noqa: E402
from lifeos.jobs import tick                                               # noqa: E402
from tests.contracts import test_runtime_manifest as manifest, test_stage_isolation as isolation   # noqa: E402

BUDGETS = ROOT / "lifeos/platform/stage_budgets.json"
TIMEOUTS_DOC = ROOT / "docs/platform/timeouts.md"
ISOLATION_DOC = ROOT / "docs/platform/stage-isolation.md"
DOCSTYLE = ROOT / "lifeos/platform/docstyle.py"
RUNTIME = ROOT / "lifeos/platform/runtime.json"
DEFAULT_BASIS = "no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values"
SINGLE_WHY = "No loop over external items: one read or write unit, so its failure is the stage's own."
GUARDED_WHY = "Loops over its items with error handling inside; one failed item does not stop the others."


def workflow_steps():
    """{stage: [(job limit in seconds, step has continue-on-error)]} from every `lifeos.run <stage>` step in the workflows."""
    import yaml
    found = {}
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        for job in (yaml.safe_load(path.read_text()).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                for token in (step.get("run") or "").split("lifeos.run ")[1:]:
                    if token.split():
                        found.setdefault(token.split()[0], []).append((int(job.get("timeout-minutes", 0)) * 60, bool(step.get("continue-on-error"))))
    return found


def budgets(changes):
    data = json.loads(BUDGETS.read_text())
    rows, steps, plan = data["stages"], workflow_steps(), {s.name: s for s in tick.PLAN}
    for name in sorted(set(run.STAGES) - set(rows)):
        if name in plan:
            row = {"basis": "the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage", "required": not plan[name].bulk, "seconds": plan[name].budget}
        else:
            limits = [limit for limit, _ in steps.get(name, [])]
            row = {"basis": DEFAULT_BASIS, "required": bool(steps.get(name)) and not all(flag for _, flag in steps[name]), "seconds": min(600, max(limits)) if limits else 600}
        rows[name] = row
        changes.append(f"stage_budgets.json: added {name}")
    for name in sorted(set(rows) - set(run.STAGES)):
        del rows[name]
        changes.append(f"stage_budgets.json: removed {name}")
    data["stages"] = dict(sorted(rows.items()))
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n", data["stages"]


def table_rewrite(path, heading, wanted, changes, label):
    """The table under `## heading`: add a row for each name in `wanted` {name: new row} that it lacks, drop rows for names not in `wanted`, keep every existing row as it is."""
    lines = path.read_text().split("\n")
    start = next(i for i, line in enumerate(lines) if line.strip() == f"## {heading}")
    first = next(i for i in range(start, len(lines)) if lines[i].startswith("| ---")) + 1
    last = first
    while last < len(lines) and lines[last].startswith("| "):
        last += 1
    have = {stage_key(line): line for line in lines[first:last]}
    for name in sorted(set(wanted) - set(have)):
        have[name] = wanted[name]
        changes.append(f"{label}: added {name}")
    for name in sorted(set(have) - set(wanted)):
        del have[name]
        changes.append(f"{label}: removed {name}")
    lines[first:last] = [have[name] for name in sorted(have)]
    return "\n".join(lines)


def stage_key(line):
    return line.split("|")[1].strip().strip("`")


def isolation_row(name, problems):
    stage = run.STAGES[name]
    if not getattr(stage, "target", None):
        problems.append(f"{name}: not a lazy stage, classify its row in stage-isolation.md by hand")
        return None
    module = stage.target[0]
    found = isolation.loops(module)
    if any(not guarded for guarded in found.values()):
        problems.append(f"{name}: {module} loops over items with no try inside; fix it or review the loop in stage-isolation.md ('Reviewed loops')")
        return None
    status, why = ("guarded", GUARDED_WHY) if found else ("single", SINGLE_WHY)
    return f"| `{name}` | `{module.replace('lifeos.', '', 1)}` | `{status}` | {why} | {'yes' if name in run.PARTIAL_FAILURE else 'no'} |"


def docstyle_text():
    packages = sorted(f"lifeos/{p.name}" for p in (ROOT / "lifeos").iterdir() if p.is_dir() and (p / "__init__.py").exists())
    quoted = [f'"{p}"' for p in packages]
    chunks, line = [], ""
    for item in quoted:
        if len(line) + len(item) > 190:
            chunks.append(line.rstrip())
            line = ""
        line += item + ", "
    chunks.append(line.rstrip().rstrip(","))
    text = DOCSTYLE.read_text()
    new = re.sub(r"ENFORCED = \(.*?\)(   # every package with an __init__\.py)", lambda m: "ENFORCED = (" + ("\n            ".join(chunks)) + ")" + m.group(1), text, count=1, flags=re.S)
    return new, packages


def runtime_text(changes):
    text = RUNTIME.read_text()
    have = {m["module"] for m in json.loads(text)["modules"]}
    lines = text.split("\n")
    row = re.compile(r'^  \{"module": "(lifeos/[^"/]+(?:\.py)?)"')
    for unit in sorted(manifest.units() - have):
        index = next((i for i, line in enumerate(lines) if (m := row.match(line)) and m.group(1) > unit), None)
        if index is None:
            index = next(i for i, line in enumerate(lines) if line == "]}")
            lines[index - 1] = lines[index - 1].rstrip(",") + ","
            lines.insert(index, f'  {{"module": "{unit}", "host": ["github"]}}')
        else:
            lines.insert(index, f'  {{"module": "{unit}", "host": ["github"]}},')
        changes.append(f"runtime.json: added {unit} (host github; edit by hand if it runs elsewhere)")
    return "\n".join(lines)


def main(argv):
    check = "--check" in argv
    changes, problems = [], []
    budget_text, stage_rows = budgets(changes)
    timeouts_rows = {name: f"| `{name}` | {row['seconds']} | {'yes' if row['required'] else 'no'} | {row['basis']} |" for name, row in stage_rows.items()}
    doc_timeouts = table_rewrite(TIMEOUTS_DOC, "Stage budgets", timeouts_rows, changes, "timeouts.md")
    have_iso = {stage_key(line) for line in ISOLATION_DOC.read_text().split("## Every stage", 1)[1].split("\n## ", 1)[0].split("\n") if line.startswith("| `")}
    iso_rows = {name: isolation_row(name, problems) for name in run.STAGES if name not in have_iso}
    wanted_iso = {name: row for name, row in iso_rows.items() if row} | {name: "" for name in have_iso if name in run.STAGES}
    doc_isolation = table_rewrite(ISOLATION_DOC, "Every stage", wanted_iso, changes, "stage-isolation.md")
    style, packages = docstyle_text()
    if set(re.findall(r'"(lifeos/[^"]+)"', re.search(r"ENFORCED = \(.*?\)", DOCSTYLE.read_text(), re.S).group(0))) == set(packages):
        style = DOCSTYLE.read_text()                                          # same packages: keep the file's own wrapping
    else:
        changes.append("docstyle.py: ENFORCED updated")
    runtime = runtime_text(changes)
    inventory = manifest.INVENTORY.read_text()
    block = manifest.block(inventory)
    rendered = manifest.render(json.loads(runtime))
    if block and block.group(0) != rendered:
        inventory = inventory.replace(block.group(0), rendered)
        changes.append("module-inventory.md: regenerated")
    targets = ((BUDGETS, budget_text), (TIMEOUTS_DOC, doc_timeouts), (ISOLATION_DOC, doc_isolation), (DOCSTYLE, style), (RUNTIME, runtime), (manifest.INVENTORY, inventory))
    for path, text in targets:
        if path.read_text() != text and not check:
            path.write_text(text)
    for line in changes + [f"NEEDS A HUMAN: {p}" for p in problems]:
        print(line)
    if not changes and not problems:
        print("registries are current")
    return 1 if (check and (changes or problems)) or problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
