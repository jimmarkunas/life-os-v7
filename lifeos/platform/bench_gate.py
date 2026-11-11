"""LIFE-577: benchmark mode must run alone, and say so when it did not.

    python -m lifeos.platform.bench_gate before    exit 1 (BENCHMARK_REFUSED) while any other hourly or domains run is queued or running
    python -m lifeos.platform.bench_gate after     write whether another run overlapped this one to the run summary (a benchmark that was overlapped is NOT COMPARABLE)
    python -m lifeos.platform.bench_gate jobs      the domains benchmark's summary: seconds per job of this run, its wall time, and the overlap check

Counts and run kinds only (the run's display title and workflow name), no ids. Pure logic is separate from the GitHub reads so tests inject both."""
import datetime as dt
import os
import sys

from lifeos.platform import gate

WORKFLOWS = ("hourly.yml", "domains.yml")
ACTIVE = ("queued", "in_progress", "pending", "waiting")


def _t(value):
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if value else None


def active_others(runs, current_id):
    """Other runs (either workflow) that are queued or running now: [(workflow, display title, status)]."""
    return [(r["workflow"], r.get("display_title"), r["status"]) for r in runs if r["id"] != current_id and r.get("status") in ACTIVE]


def overlapping(runs, current_id, started, ended):
    """Other runs whose running time intersects [started, ended]; a run still active counts as running until `ended`."""
    found = []
    for r in runs:
        if r["id"] == current_id:
            continue
        begin = _t(r.get("run_started_at") or r.get("created_at"))
        finish = ended if r.get("status") in ACTIVE else _t(r.get("updated_at"))
        if begin and finish and begin < ended and finish > started:
            found.append((r["workflow"], r.get("display_title")))
    return found


def fetch_runs():
    """The recent runs of both workflows with their workflow name attached."""
    runs = []
    for workflow in WORKFLOWS:
        for r in gate._api(f"actions/workflows/{workflow}/runs?per_page=20").get("workflow_runs", []):
            runs.append({**r, "workflow": workflow})
    return runs


def before(runs, current_id):
    """-> (exit code, message)."""
    others = active_others(runs, current_id)
    if others:
        kinds = ", ".join(f"{w} {t} ({s})" for w, t, s in others)
        return 1, f"BENCHMARK_REFUSED: {len(others)} other run(s) in progress or waiting: {kinds}. Start the benchmark when none is running."
    return 0, "benchmark: no other hourly or domains run is in progress; starting"


def after(runs, current_id, started, ended):
    """-> markdown for the run summary."""
    over = overlapping(runs, current_id, started, ended)
    if over:
        kinds = ", ".join(f"{w} {t}" for w, t in over)
        return f"### Benchmark overlap check\n\n**NOT COMPARABLE:** {len(over)} other run(s) overlapped this one ({kinds}). Run the benchmark again.\n"
    return "### Benchmark overlap check\n\nNo other hourly or domains run overlapped this one: comparable.\n"


def job_seconds(jobs):
    """[(job name, seconds, conclusion)] for the finished jobs of a run, longest first; a job that did not run is left out."""
    rows = []
    for j in jobs:
        begun, ended = _t(j.get("started_at")), _t(j.get("completed_at"))
        if begun and ended and j.get("conclusion") != "skipped":
            rows.append((j["name"], int((ended - begun).total_seconds()), j.get("conclusion")))
    return sorted(rows, key=lambda row: (-row[1], row[0]))


def jobs_summary(jobs):
    """Markdown: seconds per job, the run's wall time (first job start to last job end) and the sum of job seconds."""
    rows = job_seconds(jobs)
    if not rows:
        return "### Benchmark: seconds per job\n\nNo job finished.\n"
    starts = [_t(j["started_at"]) for j in jobs if j.get("started_at") and j.get("conclusion") != "skipped"]
    ends = [_t(j["completed_at"]) for j in jobs if j.get("completed_at") and j.get("conclusion") != "skipped"]
    wall = int((max(ends) - min(starts)).total_seconds())
    lines = ["### Benchmark: seconds per job", "", f"**Domains wall seconds (first job start to last job end): {wall}**; sum of job seconds: {sum(r[1] for r in rows)}", "",
             "| Job | Seconds | Result |", "|---|---:|---|"] + [f"| {name} | {seconds} | {result} |" for name, seconds, result in rows]
    return "\n".join(lines) + "\n"


def own_start(run_id):
    """When this run started, from its own run record (None if it cannot be read)."""
    try:
        return gate._api(f"actions/runs/{run_id}").get("run_started_at")
    except Exception as error:                                                     # noqa: BLE001 - an unreadable record only means "overlap unknown", and it says so
        print(f"bench_gate: run record unreadable ({type(error).__name__}); overlap unknown", file=sys.stderr)
        return None


def main(argv=None, environ=os.environ, fetch=fetch_runs, now=None, own_start=own_start):
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else ""
    current = int(environ.get("GITHUB_RUN_ID") or 0)
    runs = fetch()
    if mode == "before":
        code, message = before(runs, current)
        print(message, file=sys.stderr if code else sys.stdout)
        return code
    if mode == "after":
        started = _t(own_start(current))                                          # LIFE-604: the run record is the only source of the start time
        ended = now or dt.datetime.now(dt.timezone.utc)
        text = after(runs, current, started, ended) if started else "### Benchmark overlap check\n\nNo start time was recorded; overlap unknown.\n"
        print(text)
        path = environ.get("GITHUB_STEP_SUMMARY")
        if path:
            with open(path, "a") as handle:
                handle.write(text)
        return 0
    if mode == "jobs":
        jobs = gate._api(f"actions/runs/{current}/jobs?per_page=100").get("jobs", [])
        started = _t(own_start(current))
        text = jobs_summary([j for j in jobs if j.get("name") != "bench_report"])
        text += after(runs, current, started, now or dt.datetime.now(dt.timezone.utc)) if started else "\nNo start time was recorded; overlap unknown.\n"
        print(text)
        path = environ.get("GITHUB_STEP_SUMMARY")
        if path:
            with open(path, "a") as handle:
                handle.write(text)
        return 0
    print("usage: bench_gate before|after|jobs", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
