"""Schedule gate: GitHub drops and delays scheduled triggers, so the workflow carries three crons per hour and this gate
keeps the effective cadence at about one run per hour. Counts only; no job data. Stdlib only.

decide(): run when the newest earlier run (not cancelled) started at least MIN_GAP_MINUTES ago, else skip.
stale(): the newest successful run finished more than STALE_MINUTES ago -> the pipeline needs a human."""
import datetime as dt
import json
import os
import sys
import urllib.request

MIN_GAP_MINUTES = 50          # three crons (:07 :27 :47) -> at most one run per ~hour; a dropped slot is covered by the next
STALE_MINUTES = 150           # no successful run for this long -> open (or update) one GitHub issue
ISSUE_TITLE = "V7 pipeline: no successful run for over 2.5 hours"
LABEL = "pipeline-stale"


def _t(value):
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if value else None


def decide(runs, now, current_id, gap=MIN_GAP_MINUTES):
    """-> 'run' | 'skip'. Earlier runs that were cancelled (including skipped ones) do not count as work."""
    for run in runs:
        if run["id"] == current_id or run.get("conclusion") == "cancelled":
            continue
        started = _t(run.get("run_started_at") or run.get("created_at"))
        if started and (now - started) < dt.timedelta(minutes=gap):
            return "skip"
    return "run"


def stale(runs, now, current_id, minutes=STALE_MINUTES):
    for run in runs:
        if run["id"] != current_id and run.get("conclusion") == "success":
            return (now - _t(run["updated_at"])) > dt.timedelta(minutes=minutes)
    return True


def _api(path, method="GET", body=None):
    request = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def _issue(is_stale):
    try:
        open_issues = _api(f"issues?state=open&labels={LABEL}&per_page=5")
        if is_stale and not open_issues:
            try:
                _api("labels", "POST", {"name": LABEL, "color": "b60205"})
            except Exception:
                pass                                               # label already exists
            _api("issues", "POST", {"title": ISSUE_TITLE, "labels": [LABEL],
                                    "body": "The newest successful hourly run finished more than 2.5 hours ago. Scheduled triggers may be dropped by GitHub, "
                                            "or a stage is failing. Open the Actions tab. This issue closes itself when a run succeeds."})
        elif not is_stale:
            for issue in open_issues:
                _api(f"issues/{issue['number']}", "PATCH", {"state": "closed", "state_reason": "completed"})
    except Exception as error:                                     # alerting must never stop the pipeline
        print(f"gate: issue update skipped ({type(error).__name__})")


def main():
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule" and os.environ.get("TICK") != "true":
        print("gate: not a scheduled run or a timer tick -> run")
        return 0
    current = int(os.environ["GITHUB_RUN_ID"])
    runs = _api("actions/workflows/hourly.yml/runs?per_page=15")["workflow_runs"]
    now = dt.datetime.now(dt.timezone.utc)
    verdict = decide(runs, now, current)
    is_stale = stale(runs, now, current)
    _issue(is_stale)
    print(f"gate: {verdict} (stale={is_stale})")
    if verdict == "skip":
        _api(f"actions/runs/{current}/cancel", "POST")
        import time
        time.sleep(90)                                             # the cancel lands; later steps never start
    return 0


if __name__ == "__main__":
    sys.exit(main())
