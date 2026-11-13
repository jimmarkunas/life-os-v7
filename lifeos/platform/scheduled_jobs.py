"""LIFE-627: one read-only list of every job that starts on a clock, with its schedule and last run in Central Time and a state: on time, late, missing, not due or unknown.

    GITHUB_REPOSITORY=owner/name python -m lifeos.platform.scheduled_jobs

The jobs and their tolerances are data (scheduled_jobs.json; a contract test fails when a workflow schedule or a systemd timer has no row). A last run comes from the repository's public run
history, so no secret is held; a job whose last run leaves no trace there (Oracle timers, launchd) is `unknown`, never guessed. The view starts nothing. Late or missing jobs are named in the daily check."""
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
ON_TIME, LATE, MISSING, NOT_DUE, UNKNOWN = "on time", "late", "missing", "not due", "unknown"


def load():
    return json.loads(Path(__file__).with_name("scheduled_jobs.json").read_text())


def _t(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def slot(now, weekday, hhmm):
    """The newest weekly slot (UTC, Monday = 0) that is not after now."""
    hour, minute = map(int, hhmm.split(":"))
    found = now.replace(hour=hour, minute=minute, second=0, microsecond=0) - dt.timedelta(days=(now.weekday() - weekday) % 7)
    return found if found <= now else found - dt.timedelta(days=7)


def state(job, runs, now):
    """-> (state, last run or None). runs maps a workflow file to its run list, or to None when it could not be read."""
    rule, source = job["rule"], job.get("source")
    if rule == "unknown" or runs.get(source) is None or runs.get(job.get("after", source)) is None:
        return UNKNOWN, None
    found = [r for r in runs[source] if all(r.get(k) == v for k, v in job.get("match", {}).items())]
    last = max((_t(r["created_at"]) for r in found), default=None)
    minutes = lambda since: (now - since).total_seconds() / 60            # noqa: E731
    if rule == "gap":
        age = minutes(last) if last else None
        return (MISSING if age is None or age > job["missing"] else LATE if age > job["late"] else ON_TIME), last
    if rule == "weekly":
        due = slot(now, job["weekday"], job["utc"])
        age = minutes(due)
        return (ON_TIME if last and last >= due else NOT_DUE if age <= job["late"] else LATE if age <= job["missing"] else MISSING), last
    ticks = [r for r in runs[job["after"]] if r.get("display_title") == "tick" and r.get("status") == "completed" and r.get("conclusion") != "cancelled"]
    if not ticks:
        return UNKNOWN, last
    tick = max(ticks, key=lambda r: r["created_at"])
    if last and last >= _t(tick["created_at"]):
        return ON_TIME, last
    return (NOT_DUE if minutes(_t(tick["updated_at"])) <= job["late"] else LATE), last


def evaluate(now, runs, jobs=None):
    """-> one dict per job: job, host, schedule (CT), last (aware datetime or None), state."""
    rows = []
    for job in jobs or load()["jobs"]:
        found, last = state(job, runs, now)
        schedule = slot(now, job["weekday"], job["utc"]).astimezone(CT).strftime("%A %-I:%M %p %Z") if job["rule"] == "weekly" else job["schedule"]
        rows.append({"job": job["job"], "host": job["host"], "schedule": schedule, "last": last, "state": found})
    return rows


def late_names(rows):
    return [r["job"] for r in rows if r["state"] in (LATE, MISSING)]


def render(rows):
    lines = ["job | host | schedule (CT) | last run (CT) | state"]
    lines += [" | ".join((r["job"], r["host"], r["schedule"], r["last"].astimezone(CT).strftime("%b %-d, %-I:%M %p CT") if r["last"] else "none seen", r["state"])) for r in rows]
    return "\n".join(lines)


def fetch(api, jobs=None):
    """-> {workflow file: run list, or None when unreadable}. `api(path)` returns the parsed JSON for one repository API path."""
    runs = {}
    for name in {j[k] for j in jobs or load()["jobs"] for k in ("source", "after") if j.get(k)}:
        try:
            runs[name] = api(f"actions/workflows/{name}/runs?per_page=60")["workflow_runs"]
        except Exception:                                                  # noqa: BLE001 - an unreadable history is `unknown`, never a crash or a guess
            runs[name] = None
    return runs


def main():
    repo = os.environ.get("GITHUB_REPOSITORY") or subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip().removesuffix(".git").split("github.com")[-1].strip(":/")
    token = os.environ.get("GITHUB_TOKEN")

    def api(path):
        request = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}", headers={"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    rows = evaluate(dt.datetime.now(dt.timezone.utc), fetch(api))
    print(render(rows))
    print(f"{len(rows)} jobs; late or missing: {len(late_names(rows))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
