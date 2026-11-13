"""LIFE-609: which domain jobs are due on this tick, from ONE table (lifeos/platform/due_table.json) and each job's last successful run.

    python3 -m lifeos.platform.due        run by the gate job of domains.yml; writes due_<job>=true|false to $GITHUB_OUTPUT and a table to $GITHUB_STEP_SUMMARY

For a tick (event workflow_run) a job is due when its row says so; for anything else (`all`) every job is due. "Last successful run" comes from the Actions API: the recent domains runs started by a tick, newest first, and each job's conclusion; a skipped job is not a success and a manual run never counts. A success is timed at the START of its run (the gate's time), not the job's end.
every_hours: due when now minus that time >= hours minus SLACK; 1 means every tick, with no history read. slots_ct: due when a slot (CT, HH:MM) has passed since that time. No success in the history: due; several missed slots: due once. History unreadable: every job is due and "history unreadable" is printed (fail open). Counts and job names only."""
# A manual run never counts because a dry run must not make a card look fresh; the time is the START of the run so an hourly job that ends late is not mistaken for a recent one.
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from lifeos.platform import gate

TABLE = Path(__file__).with_name("due_table.json")
CENTRAL = ZoneInfo("America/Chicago")
SLACK = dt.timedelta(minutes=15)
HISTORY_RUNS = 30                                   # a daily row needs at most about 24 hourly runs back


def load_table(path=TABLE):
    return json.loads(Path(path).read_text())["jobs"]


def _t(value):
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def last_success(runs, jobs_of, wanted):
    """-> {job: start of the newest tick run in which it succeeded}. runs: newest first; jobs_of(run) -> that run's job records. Stops reading once every wanted job is found."""
    found = {}
    for run in runs:
        if len(found) == len(wanted):
            break
        started = _t(run.get("run_started_at") or run["created_at"])
        for job in jobs_of(run):
            if job.get("conclusion") == "success" and job.get("name") in wanted and job["name"] not in found:
                found[job["name"]] = started
    return found


def slot_passed(slots, last, now):
    """True when a Central-Time slot lies after `last` and not after `now` (today's and yesterday's slots are enough for any row that runs at least daily)."""
    local = now.astimezone(CENTRAL)
    for day in (0, 1):
        date = (local - dt.timedelta(days=day)).date()
        for slot in slots:
            hour, minute = (int(x) for x in slot.split(":"))
            moment = dt.datetime(date.year, date.month, date.day, hour, minute, tzinfo=CENTRAL).astimezone(dt.timezone.utc)
            if last < moment <= now:
                return True
    return False


def is_due(row, last, now):
    """-> (due, why) for one row, the job's last success time (None: none known) and now (both aware)."""
    if "slots_ct" in row:
        if last is None:
            return True, "no earlier success"
        return slot_passed(row["slots_ct"], last, now), "slots " + ", ".join(row["slots_ct"]) + " CT"
    hours = row["every_hours"]
    if hours <= 1:
        return True, "every tick"
    if last is None:
        return True, f"every {hours} h, no earlier success"
    return now - last >= dt.timedelta(hours=hours) - SLACK, f"every {hours} h"


def decide(table, last, now, tick=True):
    """-> {job: (due, why)}. Not a tick: everything is due."""
    if not tick:
        return {job: (True, "not a tick") for job in table}
    return {job: is_due(row, last.get(job), now) for job, row in table.items()}


def needs_history(table):
    return any("slots_ct" in row or row["every_hours"] > 1 for row in table.values())


def fetch_last_success(wanted, api=gate._api):
    runs = api(f"actions/workflows/domains.yml/runs?event=workflow_run&status=completed&per_page={HISTORY_RUNS}").get("workflow_runs", [])
    return last_success(runs, lambda run: api(f"actions/runs/{run['id']}/jobs?per_page=100").get("jobs", []), set(wanted))


def local_text(moment):
    if moment is None:
        return "none found"
    moment = moment.astimezone(CENTRAL)
    return f"{moment.strftime('%a %b')} {moment.day}, {moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'} CT"


def summary(result, last, failed_open):
    lines = ["### Domain jobs: due or skipped (lifeos/platform/due_table.json)", ""]
    if failed_open:
        lines += ["**History unreadable: every job is due (fail open).**", ""]
    lines += ["| Job | Rule | Last success (start of that run) | This run |", "|---|---|---|---|"]
    lines += [f"| {job} | {why} | {local_text(last.get(job))} | {'due' if due else 'skipped'} |" for job, (due, why) in sorted(result.items())]
    return "\n".join(lines) + "\n"


def main(environ=os.environ, api=gate._api, now=None, table=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    table = table if table is not None else load_table()
    tick = environ.get("GITHUB_EVENT_NAME") == "workflow_run"
    last, failed_open = {}, False
    if tick and needs_history(table):
        try:
            last = fetch_last_success(table, api)
        except Exception as error:                                          # noqa: BLE001 - any failure to read history means "run everything" and says so
            print(f"due: history unreadable ({type(error).__name__}); every job is due")
            failed_open = True
    result = {job: (True, "history unreadable") for job in table} if failed_open else decide(table, last, now, tick)
    text = summary(result, last, failed_open)
    print(text)
    outputs = "".join(f"due_{job}={'true' if due else 'false'}\n" for job, (due, _) in result.items())
    for path, content in ((environ.get("GITHUB_OUTPUT"), outputs), (environ.get("GITHUB_STEP_SUMMARY"), text)):
        if path:
            with open(path, "a") as handle:
                handle.write(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
