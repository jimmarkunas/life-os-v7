"""LIFE-1112: the API call receipt. How many calls each job of a run made to each outside service, from the `stage_timing` rows the stages print (each row's `api_calls`, counted by lifeos.platform.api_counts).

    python tools/api_receipt.py RUN_ID [RUN_ID ...]    reads each run's log with `gh` (read only) and prints one table: a row per job, a column per service
    python tools/api_receipt.py --log FILE             reads a log already saved (`gh run view RUN --log`)

A job that printed no stage row has no row (UNVERIFIED, never zero). A service is a plain lowercase name (gmail, notion, jira, graph, todoist, gcal); the gate's own tallies (units, seconds waited, slowdowns, lease) have an underscore and are listed apart under the job. Names and numbers only. Stdlib only."""

import argparse
import collections
import json
import re
import subprocess
import sys

REPO = "jimmarkunas/life-os-v7"
SERVICE = re.compile(r"^[a-z]+$")


def job_of(name):
    """`finish / finish` -> `finish`; `run / mail / mail` -> `mail` (a job that calls a reusable workflow repeats its name)."""
    parts = [p.strip() for p in str(name).split(" / ") if p.strip()]
    return parts[-1] if parts else "?"


def parse_row(raw):
    """-> {stage, begin, run, api_calls} from one log line, or None. GitHub's log masks every brace of a row as `***`
    (the value is a secret elsewhere), so the row is read by its keys, not as JSON."""
    text = raw.split("stage_timing:", 1)[1]
    stage, begin, run = (re.search(rf'"{k}": "([^"]*)"', text) for k in ("stage", "begin", "run"))
    counts = re.search(r'"api_calls": (?:\{|\*\*\*)(.*?)(?:\}|\*\*\*)', text)
    if not stage or not counts:
        return None
    api = {k: int(float(v)) for k, v in re.findall(r'"([a-z_]+)": (\d+(?:\.\d+)?)', counts.group(1))}
    return {
        "stage": stage.group(1),
        "begin": begin.group(1) if begin else None,
        "run": run.group(1) if run else None,
        "api_calls": api,
    }


def tally(text):
    """-> ({job: {service: calls}}, {job: {gate tally: number}}) from the stage_timing lines of a run log."""
    calls = collections.defaultdict(collections.Counter)
    extras = collections.defaultdict(collections.Counter)
    seen = set()
    for raw in text.splitlines():
        if "stage_timing:" not in raw:
            continue
        job = job_of(raw.split("\t", 1)[0]) if "\t" in raw else "?"
        row = parse_row(raw)
        if row is None:
            continue
        api, key = row["api_calls"], (job, row["stage"], row["begin"], row["run"])
        if not api or key in seen:
            continue
        seen.add(key)
        for name, amount in api.items():
            (calls if SERVICE.match(name) else extras)[job][name] += int(amount)
    return {j: dict(c) for j, c in calls.items()}, {j: dict(c) for j, c in extras.items()}


def render(calls, extras):
    services = sorted({s for c in calls.values() for s in c})
    if not services:
        return "No stage printed any API call count in this log (UNVERIFIED, not zero)."
    width = max(len(j) for j in calls)
    lines = [
        "| job | " + " | ".join(services) + " | total |",
        "|---|" + "---:|" * (len(services) + 1),
    ]
    totals = collections.Counter()
    for job in sorted(calls):
        row = calls[job]
        totals.update(row)
        lines.append(
            f"| {job.ljust(width)} | " + " | ".join(str(row.get(s, 0)) for s in services) + f" | {sum(row.values())} |"
        )
    lines.append("| all | " + " | ".join(str(totals[s]) for s in services) + f" | {sum(totals.values())} |")
    gate = [f"{job}: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())) for job, c in sorted(extras.items())]
    if gate:
        lines += ["", "Gate tallies (Gmail budget; units, seconds waited, slowdowns, lease):"] + [
            f"- {g}" for g in gate
        ]
    return "\n".join(lines)


def fetch(run, repo=REPO):
    return subprocess.run(
        ["gh", "run", "view", str(run), "-R", repo, "--log"], capture_output=True, text=True, check=False
    ).stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="*")
    parser.add_argument("--log")
    args = parser.parse_args(argv)
    if args.log:
        with open(args.log, encoding="utf-8") as handle:
            text = handle.read()
    elif args.runs:
        text = "\n".join(fetch(run) for run in args.runs)
    else:
        parser.error("give a run id, or --log FILE")
    print(render(*tally(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
