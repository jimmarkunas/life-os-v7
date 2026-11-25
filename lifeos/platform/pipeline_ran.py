"""LIFE-856: did the tick's hourly run run its pipeline (its `finish` job ran), so the after-tick jobs that moved out of `finish` run only then.

    python3 -m lifeos.platform.pipeline_ran     run by the gate job of domains.yml for an after-tick run; writes ran=true|false|unknown to $GITHUB_OUTPUT

Network Leads (Step 7c) and the scheduled Cloud Fit backlog (Step 6a2) used to run inside hourly.yml's `finish` job, which only runs when the prep job's gate let the tick through (`needs.prep.outputs.pipeline`).
They now run in the after-tick domains run; this reads the triggering hourly run's jobs and says whether `finish` actually ran (success or failure). An unreadable answer is `unknown`, and the workflow treats it as
true: both steps are bounded and repeat harmlessly, while skipping them would drop an hour of due work."""
import os
import sys

from lifeos.platform import gate

RAN = ("success", "failure")


def ran(jobs):
    """True when a job named `finish` finished with success or failure (skipped or cancelled is false); False when there is no such job."""
    return any(job.get("name") == "finish" and job.get("conclusion") in RAN for job in jobs)


def main(environ=os.environ, api=gate._api):
    output = "unknown"
    try:
        run_id = str(environ["TICK_RUN"]).strip()
        jobs = api(f"actions/runs/{run_id}/jobs?per_page=100").get("jobs", [])
        output = "true" if ran(jobs) else "false"
        print(f"pipeline: the tick's finish job {'ran' if output == 'true' else 'did not run'}; the moved jobs {'run' if output == 'true' else 'are skipped'}")
    except Exception as error:                                          # noqa: BLE001 - unreadable means "unknown", never a guess of false
        print(f"pipeline: the tick's jobs are unreadable ({type(error).__name__}); the moved jobs run")
    path = environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as handle:
            handle.write(f"ran={output}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
