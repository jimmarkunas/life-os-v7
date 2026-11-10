"""LIFE-555: where the hourly and domains workflows spend their time. Read-only: it reads GitHub's own run records and prints seconds per job and per step.

  python -m lifeos.profile_runs --workflow hourly|domains --last 10 [--failed 5]

Only job and step NAMES (they are written in the workflow files) and start/end times are used; no log text, no data from any run, no secret. A step that was skipped is not counted.
Runs are grouped by outcome (success, failure) and by kind (hourly: the Oracle ticker's "tick" versus a manual dispatch; domains: "after tick" versus manual); cancelled and skipped runs are left out.
Percentiles are nearest-rank over the runs that executed the step. The critical path is the longest chain of jobs through the workflow's `needs:` lines at the p50 job times."""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {"hourly": "hourly.yml", "domains": "domains.yml"}
STARTUP = re.compile(r"set up job|checkout|bitwarden|tailscale|tailnet|set up python|setup-python|install|restore|cache|complete job|post run", re.I)
UNIT_TESTS = re.compile(r"unit tests", re.I)
NAME_MAX = 70


def parse(stamp):
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")


def span(start, end):
    """Whole seconds between two GitHub timestamps, or None when either is missing or the order is wrong."""
    if not start or not end:
        return None
    seconds = int((parse(end) - parse(start)).total_seconds())
    return seconds if seconds >= 0 else None


def kind(workflow, run):
    if workflow == "hourly":
        return "tick" if run.get("displayTitle") == "tick" else "manual"
    return "after tick" if run.get("event") == "workflow_run" else "manual"


def run_record(detail):
    """One run's `gh run view --json jobs,startedAt,updatedAt` -> {"wall": s, "jobs": {job: {"seconds": s, "steps": {step: s}}}}. Skipped steps and jobs are left out."""
    jobs = {}
    first = last = None
    for job in detail.get("jobs", []):
        if job.get("conclusion") in ("skipped", "cancelled", None):
            continue
        seconds = span(job.get("startedAt"), job.get("completedAt"))
        if seconds is None:
            continue
        steps = {}
        for step in job.get("steps", []):
            if step.get("conclusion") in ("skipped", "cancelled", None):
                continue
            took = span(step.get("startedAt"), step.get("completedAt"))
            if took is not None:
                steps[step["name"][:NAME_MAX]] = steps.get(step["name"][:NAME_MAX], 0) + took
        jobs[job["name"]] = {"seconds": seconds, "steps": steps}
        first = min(first, job["startedAt"]) if first else job["startedAt"]
        last = max(last, job["completedAt"]) if last else job["completedAt"]
    return {"wall": span(first, last) or 0, "jobs": jobs}


def percentile(values, p):
    """Nearest-rank percentile (p in 0..100) of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * p // 100))                                   # ceil(n * p / 100)
    return ordered[int(rank) - 1]


def stats(values):
    return {"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95)} if values else {"n": 0, "p50": 0, "p95": 0}


def summarize(records):
    """-> {"runs", "wall", "jobs": {job: {**stats, "steps": {step: stats}}}, "startup_share", "unit_test_share"} over run_record() results."""
    jobs = {}
    startup = tests = total = 0
    for record in records:
        for name, job in record["jobs"].items():
            slot = jobs.setdefault(name, {"seconds": [], "steps": {}})
            slot["seconds"].append(job["seconds"])
            total += job["seconds"]
            for step, took in job["steps"].items():
                slot["steps"].setdefault(step, []).append(took)
                if STARTUP.search(step):
                    startup += took
                if UNIT_TESTS.search(step):
                    tests += took
    return {"runs": len(records), "wall": stats([r["wall"] for r in records]),
            "jobs": {name: {**stats(slot["seconds"]), "steps": {step: stats(vals) for step, vals in slot["steps"].items()}} for name, slot in jobs.items()},
            "startup_share": round(startup / total, 3) if total else 0, "unit_test_share": round(tests / total, 3) if total else 0, "job_seconds": total}


def needs_from_workflow(path):
    """{job: [jobs it waits for]} from the workflow file's `needs:` lines (PyYAML)."""
    import yaml                                                                  # noqa: PLC0415 - a runtime dependency of the pipeline, imported only here
    jobs = (yaml.safe_load(Path(path).read_text()) or {}).get("jobs", {})
    out = {}
    for name, body in jobs.items():
        wait = body.get("needs") or []
        out[name] = [wait] if isinstance(wait, str) else list(wait)
    return out


def critical_path(needs, seconds):
    """-> (total seconds, [jobs in order]): the longest chain through the `needs` graph using each job's duration (jobs with no record count 0)."""
    best = {}

    def reach(job):
        if job not in best:
            before = max((reach(dep) for dep in needs.get(job, [])), default=(0, []), key=lambda pair: (pair[0], len(pair[1])))
            best[job] = (before[0] + seconds.get(job, 0), before[1] + [job])
        return best[job]
    return max((reach(job) for job in needs), default=(0, []), key=lambda pair: (pair[0], len(pair[1])))      # a tie goes to the longer chain


def gh(args):
    done = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=ROOT, timeout=120)
    if done.returncode != 0:
        raise RuntimeError("gh " + args[0] + " failed")
    return json.loads(done.stdout)


def pick(runs, workflow, last, failed):
    """-> {(outcome, kind): [run ids]}: the newest `last` successes and `failed` failures per kind; cancelled and skipped runs are left out."""
    chosen = {}
    for run in runs:                                                              # newest first
        outcome = run.get("conclusion")
        if outcome not in ("success", "failure"):
            continue
        key = (outcome, kind(workflow, run))
        limit = last if outcome == "success" else failed
        if len(chosen.setdefault(key, [])) < limit:
            chosen[key].append(run["databaseId"])
    return chosen


def collect(workflow, last, failed, fetch=gh):
    runs = fetch(["run", "list", "-w", WORKFLOWS[workflow], "-L", "300", "--json", "databaseId,conclusion,event,displayTitle,createdAt"])
    out = {}
    for key, ids in pick(runs, workflow, last, failed).items():
        out[key] = [run_record(fetch(["run", "view", str(i), "--json", "jobs"])) for i in ids]
    return out


def render(workflow, groups, needs, top=8):
    lines = []
    for (outcome, how), records in sorted(groups.items()):
        s = summarize(records)
        durations = {name: job["p50"] for name, job in s["jobs"].items()}
        seconds, chain = critical_path(needs, durations)
        lines.append(f"## {workflow} / {outcome} / {how}: {s['runs']} runs; wall p50 {s['wall']['p50']} s, p95 {s['wall']['p95']} s; startup share {s['startup_share']:.0%}, unit-test share {s['unit_test_share']:.0%}")
        lines.append(f"critical path (p50 job times): {' > '.join(chain) or 'none'} = {seconds} s")
        for name, job in sorted(s["jobs"].items(), key=lambda kv: -kv[1]["p95"]):
            lines.append(f"- job {name}: n={job['n']} p50 {job['p50']} s, p95 {job['p95']} s")
            for step, st in sorted(job["steps"].items(), key=lambda kv: -kv[1]["p95"])[:top]:
                lines.append(f"    - {step}: n={st['n']} p50 {st['p50']} s, p95 {st['p95']} s")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m lifeos.profile_runs")
    parser.add_argument("--workflow", choices=sorted(WORKFLOWS), required=True)
    parser.add_argument("--last", type=int, default=10, help="newest successful runs per kind")
    parser.add_argument("--failed", type=int, default=5, help="newest failed runs per kind")
    parser.add_argument("--top", type=int, default=8, help="steps shown per job, slowest p95 first (0 = all)")
    parser.add_argument("--json", action="store_true", help="print the summaries as JSON")
    args = parser.parse_args(argv)
    try:
        groups = collect(args.workflow, args.last, args.failed)
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        print("profile_runs: could not read the run records")
        return 1
    needs = needs_from_workflow(ROOT / ".github" / "workflows" / WORKFLOWS[args.workflow])
    if args.json:
        print(json.dumps({f"{o}/{k}": summarize(r) for (o, k), r in groups.items()}, sort_keys=True))
    else:
        print(render(args.workflow, groups, needs, top=args.top or 10 ** 6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
