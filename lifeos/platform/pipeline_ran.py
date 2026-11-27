"""Whether the tick's hourly run ran its pipeline (its `finish` job ran).

Writes ran=true|false|unknown to $GITHUB_OUTPUT; the workflow treats unknown as true, since the guarded steps are
bounded and repeat harmlessly.
"""

import os
import sys

from lifeos.platform import gate

RAN = ("success", "failure")


def ran(jobs):
    return any(job.get("name") == "finish" and job.get("conclusion") in RAN for job in jobs)


def main(environ=os.environ, api=gate._api):
    output = "unknown"
    try:
        run_id = str(environ["TICK_RUN"]).strip()
        jobs = api(f"actions/runs/{run_id}/jobs?per_page=100").get("jobs", [])
        output = "true" if ran(jobs) else "false"
        print(
            f"pipeline: the tick's finish job {'ran' if output == 'true' else 'did not run'}; the moved jobs {'run' if output == 'true' else 'are skipped'}"
        )
    except Exception as error:  # noqa: BLE001
        print(f"pipeline: the tick's jobs are unreadable ({type(error).__name__}); the moved jobs run")
    path = environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as handle:
            handle.write(f"ran={output}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
