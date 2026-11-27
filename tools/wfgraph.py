"""LIFE-1010: the proof that a workflow refactor changes no behaviour.

    python tools/wfgraph.py [--base origin/main]

Normalises every `.github/workflows/*.yml` at the base commit and in the working tree and diffs the two job graphs: step names and comments are dropped, and the two composite actions this lean pass introduced are inlined back to the steps they replace (`set:` on a `bws-load` step is the inline `secrets:` list it names in `.github/actions/bws-load/sets.yml`; `job-start` is the Python step plus the Bitwarden helper step; `doppler_compare` on a `bws-load` step is the Doppler parity step that used to follow it, its names sorted). Job ids, job names, `run-name`, triggers, `if:`, `needs`, env, `with`, secrets and the commands in their order all stay in the graph, so any difference shows. Exit 0 and one `empty diff` line per workflow when nothing differs; exit 1 with the unified diff otherwise. Paste the output in the PR body."""

import argparse
import difflib
import json
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLOWS = ".github/workflows"
LOCAL = "./.github/actions/"
SETS = ".github/actions/bws-load/sets.yml"


def parity_names(secrets):
    """The names a Bitwarden load lists, sorted: the names its parity step compares."""
    return sorted(line.split(">")[1].strip() for line in (secrets or "").splitlines() if ">" in line)


def expand(step, sets=None):
    """-> the steps one workflow step stands for before the lean pass."""
    uses = step.get("uses") or ""
    with_ = step.get("with") or {}
    if uses == LOCAL + "bws-load" and with_.get("set"):
        step = {
            **step,
            "with": {**{k: v for k, v in with_.items() if k != "set"}, "secrets": (sets or {})[with_["set"]]},
        }
        with_ = step["with"]
    if uses == LOCAL + "job-start":
        out = [{"uses": LOCAL + "python"}] if str(with_.get("python", "true")) != "false" else []
        return out + [{"uses": LOCAL + "bws-helper", "with": {"wait_seconds": with_.get("wait_seconds", "300")}}]
    if uses == LOCAL + "bws-load" and with_.get("doppler_compare"):
        load = {**step, "with": {k: v for k, v in with_.items() if k != "doppler_compare"}}
        parity = {
            "if": "${{ steps.%s.outcome == 'success' }}" % step["id"],
            "continue-on-error": True,
            "uses": LOCAL + "doppler-load",
            "with": {
                "token": with_["doppler_compare"],
                "names": ",".join(parity_names(with_.get("secrets"))),
                "bitwarden": "${{ toJSON(steps.%s.outputs) }}" % step["id"],
            },
        }
        return [load, parity]
    return [step]


def normal_step(step):
    out = {k: v for k, v in step.items() if k != "name"}
    if out.get("uses") == LOCAL + "doppler-load":
        out.pop("id", None)
        out["with"] = {**out["with"], "names": ",".join(sorted(out["with"]["names"].split(",")))}
    return out


def keyed(node):
    """YAML reads the `on:` key as True; every key becomes text so the graph sorts."""
    if isinstance(node, dict):
        return {str(k): keyed(v) for k, v in node.items()}
    return [keyed(v) for v in node] if isinstance(node, list) else node


def graph(text, sets=None):
    flow = keyed(yaml.safe_load(text))
    for job in (flow.get("jobs") or {}).values():
        job["steps"] = [normal_step(s) for step in job.get("steps") or [] for s in expand(step, sets)]
    return json.dumps(flow, indent=1, sort_keys=True, default=str).splitlines()


def at(base, path):
    done = subprocess.run(["git", "show", f"{base}:{path}"], cwd=ROOT, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    base = parser.parse_args(argv).base
    old_sets = yaml.safe_load(at(base, SETS) or "{}")
    new_sets = yaml.safe_load((ROOT / SETS).read_text()) if (ROOT / SETS).exists() else {}
    listed = subprocess.run(
        ["git", "ls-tree", "--name-only", base, FLOWS + "/"], cwd=ROOT, capture_output=True, text=True
    ).stdout.split()
    paths = sorted(
        {p for p in listed if p.endswith(".yml")} | {str(p.relative_to(ROOT)) for p in (ROOT / FLOWS).glob("*.yml")}
    )
    status = 0
    for path in paths:
        old, new = at(base, path), (ROOT / path).read_text() if (ROOT / path).exists() else None
        if old is None or new is None:
            print(f"{path}: {'added' if old is None else 'removed'} (no graph to compare)")
            status = 1
            continue
        diff = list(
            difflib.unified_diff(
                graph(old, old_sets), graph(new, new_sets), "base/" + path, "tree/" + path, lineterm="", n=2
            )
        )
        print(f"{path}: empty diff" if not diff else "\n".join(diff))
        status |= bool(diff)
    return status


if __name__ == "__main__":
    sys.exit(main())
