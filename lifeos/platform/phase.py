"""LIFE-775: has this tick's early domains run already been started?

    python3 -m lifeos.platform.phase     run by the gate job of domains.yml for an after-tick run; writes early_covered=true|false|unknown to $GITHUB_OUTPUT

The hourly prep job starts `domains.yml` with `phase: tick` as soon as the tick passes its gate, so every domain job that reads nothing the tick writes runs while the tick runs.
The run that starts when the tick finishes (workflow_run) then runs only megibow, interview and granola, UNLESS no early run exists (the dispatch failed or was cancelled): then it
runs every job, as before. Only a clear "no early run" (false) lets it run the early jobs; unreadable (unknown) leaves them out, because two runs writing the same pages is worse than one skipped hour
(every job is due again next hour). One tick makes one early run: the gate between ticks is 50 minutes, so a dispatch run created after this tick started belongs to this tick."""
import datetime as dt
import os
import sys

from lifeos.platform import gate

GRACE = dt.timedelta(minutes=2)


def _t(value):
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def covered(runs, tick_started):
    """True when a `domains (after tick)` dispatch run created at or after this tick started exists and was not cancelled. `runs`: the domains workflow's runs, any order."""
    for run in runs:
        if run.get("display_title") != gate.DOMAINS_TITLE or run.get("event") != "workflow_dispatch" or run.get("conclusion") == "cancelled":
            continue
        if _t(run.get("created_at")) >= tick_started - GRACE:
            return True
    return False


def main(environ=os.environ, api=gate._api):
    output = "unknown"
    try:
        started = _t(environ["TICK_STARTED"])
        runs = api("actions/workflows/domains.yml/runs?event=workflow_dispatch&per_page=20").get("workflow_runs", [])
        output = "true" if covered(runs, started) else "false"
        print(f"phase: early run {'found' if output == 'true' else 'not found'}; the after-tick run {'leaves the early jobs to it' if output == 'true' else 'runs every job'}")
    except Exception as error:                                          # noqa: BLE001 - any failure means "do not double-run" and says so
        print(f"phase: early run unreadable ({type(error).__name__}); the after-tick run leaves the early jobs out")
    path = environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as handle:
            handle.write(f"early_covered={output}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
