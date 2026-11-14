"""LIFE-705: the numbers behind the `suite-timing` workflow (manual, no secrets): N full-suite runs on N fresh runners at once, then one report job that prints min, median, p95 and max
of the suite's seconds and fails when any run reaches the CI limit (120 s). Runners started in the same minute measure runner-to-runner spread, not hour-to-hour drift; the report says so.

`python -m tests.suite_timing` runs in the report job (GITHUB_RUN_ID, GITHUB_REPOSITORY and GITHUB_TOKEN are set by the runner; the token only reads this run's jobs and logs)."""
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys

LIMIT = 120
STEP = "Full suite"                                              # the timing job's step whose seconds are the suite's seconds
SHARD_LINE = re.compile(r"^(?:\S+\s+)?shard (\d+)/(\d+) took ([0-9.]+) s", re.M)


def percentile(values, p):
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    return ordered[max(math.ceil(p / 100 * len(ordered)), 1) - 1]


def stats(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {"runs": len(ordered), "min": ordered[0], "median": median, "p95": percentile(ordered, 95), "max": ordered[-1]}


def _moment(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def step_seconds(job, prefix=STEP):
    """Whole seconds of the job's first step whose name starts with `prefix`, or None when it has not finished."""
    for step in job.get("steps", []):
        if step.get("name", "").startswith(prefix) and step.get("started_at") and step.get("completed_at"):
            return round((_moment(step["completed_at"]) - _moment(step["started_at"])).total_seconds())
    return None


def shard_times(log):
    """{shard number: seconds} from the `shard K/N took S s` lines each shard prints."""
    return {int(k): float(seconds) for k, _n, seconds in SHARD_LINE.findall(log)}


def report(rows, limit=LIMIT):
    """-> (text, ok). rows: [{'run': 1, 'seconds': 104, 'shards': {1: 101.2, 2: 98.4}}]."""
    seconds = [r["seconds"] for r in rows if r.get("seconds") is not None]
    lines = ["| run | suite s | shard seconds |", "|---|---|---|"]
    for row in sorted(rows, key=lambda r: r["run"]):
        lines.append(f"| {row['run']} | {row['seconds']} | {', '.join(f'{k}: {v:.0f}' for k, v in sorted(row.get('shards', {}).items()))} |")
    if not seconds:
        return "\n".join(lines + ["", "no run finished the suite"]), False
    summary = stats(seconds)
    spread = [max(r["shards"].values()) - min(r["shards"].values()) for r in rows if len(r.get("shards", {})) > 1]
    lines += ["", f"suite seconds over {summary['runs']} runs: min {summary['min']}, median {summary['median']:g}, p95 {summary['p95']}, max {summary['max']} (limit {limit}).",
              f"shard spread (slowest minus fastest) per run: {', '.join(f'{s:.0f}' for s in spread) or 'one shard'}.",
              "These runs started together on separate runners: they measure runner-to-runner spread in one minute, not ten different hours."]
    return "\n".join(lines), summary["max"] < limit


def _gh(path):
    return subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout


def main():
    run, repo = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_REPOSITORY"]
    jobs = json.loads(_gh(f"repos/{repo}/actions/runs/{run}/jobs?per_page=100"))["jobs"]
    rows = []
    for job in jobs:
        found = re.fullmatch(r"timing \((\d+)\)", job["name"])
        if found:
            rows.append({"run": int(found.group(1)), "seconds": step_seconds(job), "shards": shard_times(_gh(f"repos/{repo}/actions/jobs/{job['id']}/logs"))})
    text, ok = report(rows)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as handle:
            handle.write(text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
