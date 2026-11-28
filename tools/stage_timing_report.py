"""LIFE-855: the stage timing report. Per stage: runs, p50, p95, max, share of the run's wall time and its part of the critical path, from the `stage-timing-<run_id>.jsonl` artifacts of the hourly runs.

    python tools/stage_timing_report.py --dir DIR                       DIR holds stage-timing-<run_id>.jsonl files (already downloaded)
    python tools/stage_timing_report.py --from 2026-11-23 --to 2026-11-24   downloads the artifacts of the hourly ticks created in that Central-time date range with `gh` (read only)
    add --steps to join GitHub's own job-step times for the jobs and steps that are not tick stages (prep, finish, report): they appear as `job <name>` rows

Missing evidence is UNVERIFIED, never zero: a run with no artifact is counted as such, and a stage a run lacks is left out of that run's numbers and counted. The critical path of a run is walked back from its
latest end: the stage that ended last, then the latest-ending stage that had ended by the time the previous one began, and so on; a stage's critical seconds are its own elapsed seconds on that path. Names and numbers only.
Stdlib only (gh for the fetch)."""

import argparse
import collections
import datetime as dt
import json
import math
import pathlib
import re
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
REPO = "jimmarkunas/life-os-v7"
WORKFLOW = "hourly.yml"
GAP = 1.0  # seconds: a stage that ended this close before another began is that one's predecessor
JOB_ROWS = ("prep", "finish", "report")


def _t(value):
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def parse_lines(text):
    """-> the stage_timing rows in a jsonl file or a log (`stage_timing: {...}` lines); a line that is not one is ignored."""
    rows = []
    for raw in text.splitlines():
        match = re.search(r"(\{.*\})\s*$", raw)
        if not match or ("stage_timing" not in raw and not raw.lstrip().startswith("{")):
            continue
        try:
            row = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(row, dict) and "stage" in row and "begin" in row and "end" in row:
            rows.append(row)
    return rows


def percentile(values, fraction):
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def critical_path(rows):
    """-> the stage rows on the critical path of one run, last to first."""
    spans = [
        (r, _t(r["begin"]), _t(r["end"])) for r in rows if not str(r["stage"]).startswith("job ")
    ]  # the job rows contain the tick's stages, so they are not on the path
    if not spans:
        return []
    path, (row, begin, _) = [], max(spans, key=lambda s: s[2])
    path.append(row)
    while True:
        earlier = [s for s in spans if s[0] is not row and s[2] <= begin + dt.timedelta(seconds=GAP) and s[1] < begin]
        if not earlier:
            return path
        row, begin, _ = max(earlier, key=lambda s: s[2])
        path.append(row)


def analyse(runs, expected=None):
    """`runs`: {run id: rows or None (no artifact)}. -> {"runs", "unverified_runs", "stages": {name: {...}}, "wall": [...]}."""
    have = {run: rows for run, rows in runs.items() if rows}
    names = set(expected or ()) | {r["stage"] for rows in have.values() for r in rows}
    stats = {
        name: {
            "elapsed": [],
            "wait": [],
            "critical": [],
            "on_path": 0,
            "missing_runs": [],
            "outcomes": collections.Counter(),
        }
        for name in names
    }
    walls = {}
    for run, rows in sorted(have.items()):
        begins, ends = [_t(r["begin"]) for r in rows], [_t(r["end"]) for r in rows]
        walls[run] = (max(ends) - min(begins)).total_seconds()
        seen = {r["stage"]: r for r in rows}
        on_path = {r["stage"]: r for r in critical_path(rows)}
        for name in names:
            if name not in seen:
                stats[name]["missing_runs"].append(run)
                continue
            row = seen[name]
            stats[name]["elapsed"].append(float(row["elapsed_s"]))
            stats[name]["wait"].append(float(row.get("wait_s") or 0))
            stats[name]["outcomes"][row.get("outcome", "UNVERIFIED")] += 1
            if name in on_path:
                stats[name]["on_path"] += 1
                stats[name]["critical"].append(float(row["elapsed_s"]))
    total_wall = sum(walls.values())
    out = {}
    for name, s in sorted(stats.items()):
        e = s["elapsed"]
        out[name] = {
            "runs": len(e),
            "p50": percentile(e, 0.5) if e else None,
            "p95": percentile(e, 0.95) if e else None,
            "max": max(e) if e else None,
            "share_of_wall": round(100 * sum(e) / total_wall, 1) if e and total_wall else None,
            "wait_p50": percentile(s["wait"], 0.5) if s["wait"] else None,
            "critical_runs": s["on_path"],
            "critical_seconds": round(sum(s["critical"]), 1),
            "outcomes": dict(s["outcomes"]),
            "missing_in_runs": s["missing_runs"],
        }
    return {
        "runs": len(have),
        "unverified_runs": sorted(run for run, rows in runs.items() if not rows),
        "stages": out,
        "wall": sorted(walls.values()),
    }


def _ct(moment):
    local = moment.astimezone(CENTRAL)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} CT"


def render(result, title="Stage timing report"):
    lines = [
        f"### {title}",
        "",
        f"Runs with a receipt: {result['runs']}. Runs with no artifact (UNVERIFIED, not counted as zero): {len(result['unverified_runs'])}"
        + (f" ({', '.join(result['unverified_runs'])})" if result["unverified_runs"] else "")
        + ".",
    ]
    if result["wall"]:
        lines.append(
            f"Run wall time (first begin to last end of the stage rows): p50 {percentile(result['wall'], 0.5):.0f} s, p95 {percentile(result['wall'], 0.95):.0f} s, max {max(result['wall']):.0f} s."
        )
    lines += [
        "",
        "| Stage | Runs | p50 s | p95 s | Max s | Share of wall % | Wait p50 s | On critical path (runs) | Critical s total | Not complete | Missing in runs |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, s in result["stages"].items():
        fmt = lambda v: "UNVERIFIED" if v is None else f"{v:.1f}"  # noqa: E731 - one line of formatting
        bad = sum(n for o, n in s["outcomes"].items() if o != "complete")
        lines.append(
            f"| {name} | {s['runs']} | {fmt(s['p50'])} | {fmt(s['p95'])} | {fmt(s['max'])} | {fmt(s['share_of_wall'])} | {fmt(s['wait_p50'])} | {s['critical_runs']} | {s['critical_seconds']:.1f} | {bad} | {len(s['missing_in_runs'])} |"
        )
    lines += [
        "",
        "Share of wall is a stage's summed seconds over the summed run wall time; concurrent stages overlap, so the column can add to more than 100. Critical path: see the tool's docstring.",
    ]
    return "\n".join(lines)


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False).stdout


def load_dir(path):
    runs = {}
    for file in sorted(pathlib.Path(path).glob("stage-timing-*.jsonl")):
        runs[file.stem.split("stage-timing-", 1)[1]] = parse_lines(file.read_text())
    return runs


def fetch(first, last, repo=REPO, runner=gh):
    """-> {run id: rows or None} for the hourly runs created from `first` through `last` (Central dates, inclusive); an artifact that cannot be downloaded is None."""
    start = dt.datetime.combine(first, dt.time.min, CENTRAL).astimezone(dt.timezone.utc)
    stop = dt.datetime.combine(last + dt.timedelta(days=1), dt.time.min, CENTRAL).astimezone(dt.timezone.utc)
    listing = json.loads(
        runner("api", f"repos/{repo}/actions/workflows/{WORKFLOW}/runs?per_page=100&status=completed") or "{}"
    ).get("workflow_runs", [])
    runs = {}
    for item in listing:
        if item.get("display_title") != "tick" or not (start <= _t(item["created_at"]) < stop):
            continue
        run = str(item["id"])
        with tempfile.TemporaryDirectory() as tmp:
            runner("run", "download", run, "-R", repo, "-n", f"stage-timing-{run}", "-D", tmp)
            files = list(pathlib.Path(tmp).glob("*.jsonl"))
            runs[run] = parse_lines(files[0].read_text()) if files else None
    return runs


def job_rows(run, repo=REPO, runner=gh):
    """-> the prep, finish and report jobs of a run as rows (`job <name>`), from GitHub's own timestamps; the tick's stages are in the artifact."""
    jobs = json.loads(runner("api", f"repos/{repo}/actions/runs/{run}/jobs?per_page=100") or "{}").get("jobs", [])
    rows = []
    for job in jobs:
        label = str(job.get("name") or "").split(" / ")[0]  # a job that calls a reusable workflow is "finish / finish"
        if label in JOB_ROWS and job.get("started_at") and job.get("completed_at"):
            seconds = (_t(job["completed_at"]) - _t(job["started_at"])).total_seconds()
            rows.append(
                {
                    "stage": f"job {label}",
                    "outcome": "complete" if job.get("conclusion") == "success" else "incomplete",
                    "begin": job["started_at"],
                    "end": job["completed_at"],
                    "elapsed_s": seconds,
                    "wait_s": 0,
                }
            )
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir")
    parser.add_argument("--from", dest="first")
    parser.add_argument("--to", dest="last")
    parser.add_argument("--steps", action="store_true")
    args = parser.parse_args(argv)
    if args.dir:
        runs = load_dir(args.dir)
    elif args.first and args.last:
        runs = fetch(dt.date.fromisoformat(args.first), dt.date.fromisoformat(args.last))
    else:
        parser.error("give --dir, or --from and --to")
    if args.steps:
        for run, rows in runs.items():
            if rows:
                rows.extend(job_rows(run))
    print(render(analyse(runs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
