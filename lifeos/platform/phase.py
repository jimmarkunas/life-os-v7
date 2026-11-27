"""Whether this tick's early domain jobs were already started.

`python3 -m lifeos.platform.phase` writes early_covered=true|false|unknown to $GITHUB_OUTPUT. Only a clear false lets
the after-tick run do the early jobs; unknown skips them, since two runs writing the same pages is worse than one
skipped hour.
"""

import datetime as dt
import os
import sys

from lifeos.platform import gate

GRACE = dt.timedelta(minutes=2)


def _t(value):
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def covered(runs, tick_started):
    """True when a non-cancelled `domains (after tick)` dispatch run started at or after this tick exists."""
    for run in runs:
        if (
            run.get("display_title") != gate.DOMAINS_TITLE
            or run.get("event") != "workflow_dispatch"
            or run.get("conclusion") == "cancelled"
        ):
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
        print(
            f"phase: early run {'found' if output == 'true' else 'not found'}; the after-tick run {'leaves the early jobs to it' if output == 'true' else 'runs every job'}"
        )
    except Exception as error:  # noqa: BLE001
        print(f"phase: early run unreadable ({type(error).__name__}); the after-tick run leaves the early jobs out")
    path = environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as handle:
            handle.write(f"early_covered={output}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
