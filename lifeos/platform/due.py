"""Which domain jobs are due on this tick, from ONE table (lifeos/platform/due_table.json) and each job's last successful run.

    python3 -m lifeos.platform.due    run by the gate job of domains.yml; writes due_<job>=true|false to $GITHUB_OUTPUT
                                      and a table to $GITHUB_STEP_SUMMARY

On a tick a job is due when its row says so; otherwise every job is due. The last success comes from the Actions API
(recent tick-started runs); skipped jobs and manual runs never count, and a success is timed at the START of its run.
every_hours: due when now minus that time >= hours minus SLACK. slots_ct: due when a CT slot (HH:MM) has passed since
it. No success in the history: due; several missed slots: due once. Unreadable history: every job is due (fail open).
"""

import datetime as dt
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

from lifeos.platform import gate, jobnames

TABLE = Path(__file__).with_name("due_table.json")
CENTRAL = ZoneInfo("America/Chicago")
SLACK = dt.timedelta(minutes=15)
HISTORY_RUNS = 30


def load_table(path=TABLE):
    return json.loads(Path(path).read_text())["jobs"]


def load_stages(path=TABLE):
    return json.loads(Path(path).read_text()).get("stages", {})


def _t(value):
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def last_success(runs, jobs_of, wanted):
    found = {}
    for run in runs:
        if len(found) == len(wanted):
            break
        started = _t(run.get("run_started_at") or run["created_at"])
        for job in jobs_of(run):
            name = jobnames.label(job.get("name"))
            if job.get("conclusion") == "success" and name in wanted and name not in found:
                found[name] = started
    return found


def slot_passed(slots, last, now):
    """True when a Central-Time slot lies after `last` and not after `now` (today's and yesterday's slots suffice for a
    row that runs at least daily).
    """
    local = now.astimezone(CENTRAL)
    for day in (0, 1):
        date = (local - dt.timedelta(days=day)).date()
        for slot in slots:
            hour, minute = (int(x) for x in slot.split(":"))
            moment = dt.datetime(date.year, date.month, date.day, hour, minute, tzinfo=CENTRAL).astimezone(
                dt.timezone.utc
            )
            if last < moment <= now:
                return True
    return False


def is_due(row, last, now):
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
    if not tick:
        return {job: (True, "not a tick") for job in table}
    return {job: is_due(row, last.get(job), now) for job, row in table.items()}


def needs_history(table):
    return any("slots_ct" in row or row["every_hours"] > 1 for row in table.values())


def fetch_last_success(wanted, api=gate._api):
    runs = api(f"actions/workflows/domains.yml/runs?status=completed&per_page={HISTORY_RUNS}").get(
        "workflow_runs", []
    )  # the title tells after-tick runs from a manual run
    runs = [run for run in runs if run.get("display_title") == gate.DOMAINS_TITLE]
    return last_success(
        runs, lambda run: api(f"actions/runs/{run['id']}/jobs?per_page=100").get("jobs", []), set(wanted)
    )


STAGE_OK = re.compile(r"\bstage (\S+): ok ")
TICK_RUNS = 30


class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.headers.pop(
                "Authorization", None
            )  # the log archive lives on another host: the token is never sent there
        return new


def _log_text(run_id, environ=os.environ):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}/logs",
        headers={"Authorization": f"Bearer {environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.build_opener(_NoAuthRedirect).open(request, timeout=60) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    return "\n".join(archive.read(name).decode("utf-8", "replace") for name in archive.namelist())


def stages_ok(text):
    return set(STAGE_OK.findall(text))


def fetch_stage_success(wanted, api=gate._api, log_text=_log_text):
    runs = api(f"actions/workflows/hourly.yml/runs?status=completed&per_page={TICK_RUNS}").get("workflow_runs", [])
    found = {}
    for run in runs:
        if len(found) == len(wanted):
            break
        if run.get("display_title") != gate.TICK_TITLE:
            continue
        for name in stages_ok(log_text(run["id"])) & set(wanted):
            found.setdefault(name, _t(run.get("run_started_at") or run["created_at"]))
    return found


def stage_decisions(table, environ=os.environ, now=None, api=gate._api, log_text=_log_text):
    """-> ({stage: (due, why)}, history_ok). All-hourly rows read no history; when history cannot be read every stage is
    due and history_ok is False (fail open).
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    last, ok = {}, True
    if needs_history(table):
        try:
            last = fetch_stage_success(table, api, log_text)
        except Exception as error:  # noqa: BLE001 - any failure to read history means "run everything" and says so
            print(f"due: stage history unreadable ({type(error).__name__}); every stage is due")
            ok = False
    result = {name: (True, "history unreadable") for name in table} if not ok else decide(table, last, now)
    return result, ok


def split_plan(plan, environ=None, decisions=None):
    """-> (plan without the stages the table's `stages` says are not due this tick, [(step, why)] for those left out).
    Stages with no row always run.
    """
    table = load_stages()
    result = (
        decisions if decisions is not None else stage_decisions(table, os.environ if environ is None else environ)[0]
    )
    left = [(step, result[step.name][1]) for step in plan if step.name in table and not result[step.name][0]]
    skipped = {step.name for step, _ in left}
    return tuple(step for step in plan if step.name not in skipped), left


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
    lines += [
        f"| {job} | {why} | {local_text(last.get(job))} | {'due' if due else 'skipped'} |"
        for job, (due, why) in sorted(result.items())
    ]
    return "\n".join(lines) + "\n"


def main(environ=os.environ, api=gate._api, now=None, table=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    table = table if table is not None else load_table()
    tick = gate.is_tick_event(environ)  # an after-tick run, or the early run the tick's prep job starts (phase tick)
    last, failed_open = {}, False
    if tick and needs_history(table):
        try:
            last = fetch_last_success(table, api)
        except Exception as error:  # noqa: BLE001 - any failure to read history means "run everything" and says so
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
