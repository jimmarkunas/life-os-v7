"""Schedule gate: GitHub drops and delays scheduled triggers, so the workflow carries three crons per hour and this gate
keeps the effective cadence at about one run per hour. Counts only; no job data. Stdlib only.

decide(): run when the newest earlier TICK run (title "tick": scheduled or timer, never a manual run; not cancelled) started at least MIN_GAP_MINUTES ago, else skip.
stale(): the newest successful run finished more than STALE_MINUTES ago -> the pipeline needs a human.
failing(): three or more failed ticks in the last 24 hours -> open; none in 24 hours -> recovered; in between -> unchanged (hysteresis, no flapping).
The watchdog owns the incident record (one GitHub issue per kind): it pushes first, and an incident whose push was not accepted stays labelled
`push-pending` and is pushed again on the next watchdog run (every 30 minutes) until the phone service accepts it."""
import datetime as dt
import json
import os
import sys
import urllib.request

TICK_TITLE = "tick"           # the workflow's run-name for scheduled and timer runs; manual runs are "manual" and never block a tick
MIN_GAP_MINUTES = 50          # three crons (:07 :27 :47) -> at most one run per ~hour; a dropped slot is covered by the next
STALE_MINUTES = 150           # no successful run for this long -> open (or update) one GitHub issue
ISSUE_TITLE = "V7 pipeline: no successful run for over 2.5 hours"
LABEL = "pipeline-stale"
FAIL_WINDOW_HOURS = 24         # failed ticks counted over this window
FAIL_OPEN = 3                  # this many failed ticks in the window opens the "failing" incident; zero closes it
FAIL_TITLE = "V7 pipeline: hourly runs failing repeatedly (3+ failed in 24 hours)"
FAIL_LABEL = "pipeline-failing"
DIGEST_HOUR_UTC = 13           # the daily check-in: 8 AM Chicago in summer time (7 AM in winter); the :17 watchdog run sends it
DOMAINS_TITLE = "domains (after tick)"
PENDING = "push-pending"       # the incident exists but its phone push was not accepted yet; retried by the watchdog


def _t(value):
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if value else None


def decide(runs, now, current_id, gap=MIN_GAP_MINUTES):
    """-> 'run' | 'skip'. Cancelled (including skipped) runs and manual runs do not count."""
    for run in runs:
        if run["id"] == current_id or run.get("conclusion") == "cancelled" or run.get("display_title") != TICK_TITLE:
            continue
        started = _t(run.get("run_started_at") or run.get("created_at"))
        if started and (now - started) < dt.timedelta(minutes=gap):
            return "skip"
    return "run"


ACTIVE = ("queued", "in_progress", "pending", "waiting")
WAIT_MINUTES = 25                  # a manual run waits this long for a tick that is already running, then goes on


def active(runs, current_id, title):
    """Other runs of this workflow with this title (tick | manual) that are queued or running now."""
    return [r for r in runs if r["id"] != current_id and r.get("status") in ACTIVE and r.get("display_title") == title]


def stale(runs, now, current_id, minutes=STALE_MINUTES):
    for run in runs:
        if run["id"] != current_id and run.get("conclusion") == "success":
            return (now - _t(run["updated_at"])) > dt.timedelta(minutes=minutes)
    return True


def failing(runs, now, window_hours=FAIL_WINDOW_HOURS):
    """-> True (open) | False (recovered) | None (unchanged). Counts failed ticks only: manual runs and cancelled runs never count."""
    cutoff = now - dt.timedelta(hours=window_hours)
    failed = sum(1 for run in runs if run.get("display_title") == TICK_TITLE and run.get("conclusion") == "failure"
                 and (_t(run.get("updated_at") or run.get("created_at")) or cutoff) > cutoff)
    if failed >= FAIL_OPEN:
        return True
    return False if failed == 0 else None


def digest_due(now):
    return now.hour == DIGEST_HOUR_UTC and now.minute < 30


def health(runs, domain_runs, now, window_hours=24):
    """-> (title, body): the daily check-in, counts only. Its absence is the alarm: if the watchdog or the phone path is dead, no check-in arrives."""
    cutoff = now - dt.timedelta(hours=window_hours)
    recent = [r for r in runs if r.get("display_title") == TICK_TITLE and r.get("conclusion") in ("success", "failure")
              and (_t(r.get("updated_at") or r.get("created_at")) or cutoff) > cutoff]
    failed = sum(1 for r in recent if r["conclusion"] == "failure")
    good = [r for r in recent if r["conclusion"] == "success"]
    since = int((now - max(_t(r["updated_at"]) for r in good)).total_seconds() // 60) if good else None
    domains = sum(1 for r in domain_runs if r.get("display_title") == DOMAINS_TITLE and r.get("conclusion") == "failure"
                  and (_t(r.get("updated_at") or r.get("created_at")) or cutoff) > cutoff)
    ok = failed == 0 and domains == 0 and since is not None and since < 90
    last = "no good run" if since is None else f"last good run {since} min ago"
    return (f"V7 daily check: {'OK' if ok else 'DEGRADED'}",
            f"Last 24 h: {len(recent)} hourly runs, {len(good)} ok, {failed} failed; {last}; {domains} failed domain run(s).")


def _api(path, method="GET", body=None):
    request = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def _push(title, body, severity):
    from lifeos.platform import alerts                              # noqa: PLC0415
    return alerts.ntfy(os.environ.get("NTFY_TOPIC", ""), title, body, severity)


def sync_incident(label, title, body, signal, open_push, close_push, push=None):
    """One incident = one GitHub issue. signal: True open | False recovered | None unchanged. -> "opened" | "closed" | "retried" | None.
    Push first: an incident whose push is not accepted is still opened (the record must exist) but labelled `push-pending`, so the next run pushes again."""
    push = push or _push
    try:
        issues = _api(f"issues?state=open&labels={label}&per_page=5")
        if signal is True and not issues:
            ok = push(*open_push)
            try:
                _api("labels", "POST", {"name": label, "color": "b60205"})
            except Exception:
                pass                                               # label already exists
            _api("issues", "POST", {"title": title, "labels": [label] + ([] if ok else [PENDING]), "body": body})
            return "opened"
        if signal is False and issues:
            push(*close_push)                                      # recovered is INFO: a missed one is visible in the issue history, never blocks closing
            for issue in issues:
                _api(f"issues/{issue['number']}", "PATCH", {"state": "closed", "state_reason": "completed"})
            return "closed"
        retried = None
        for issue in issues:
            if signal is not False and PENDING in [(item.get("name") if isinstance(item, dict) else item) for item in issue.get("labels", [])]:
                if push(*open_push):
                    _api(f"issues/{issue['number']}/labels/{PENDING}", "DELETE")
                retried = "retried"
        return retried
    except Exception as error:                                     # alerting must never stop the pipeline
        print(f"gate: incident update skipped ({type(error).__name__})")
        return None


def watch(now=None, push=None):
    """Independent watchdog (its own workflow): the gate only runs when an hourly run starts, so a total stall (no GitHub schedule, no timer tick)
    would stay silent. Two incidents, both raised from outside the pipeline: no successful run for 2.5 hours, and three or more failed ticks in 24 hours.
    Never touches job data."""
    runs = _api("actions/workflows/hourly.yml/runs?per_page=60")["workflow_runs"]
    now = now or dt.datetime.now(dt.timezone.utc)
    is_stale, fail_signal = stale(runs, now, 0), failing(runs, now)
    changes = [
        sync_incident(LABEL, ISSUE_TITLE, "The newest successful hourly run finished more than 2.5 hours ago. Scheduled triggers may be dropped by GitHub, "
                      "or a stage is failing. Open the Actions tab. This issue closes itself when a run succeeds.", is_stale,
                      ("V7 pipeline stalled", "No hourly run has succeeded for 2.5 hours. Check cron-job.org and the Actions tab.", "page"),
                      ("V7 pipeline recovered", "A run succeeded.", "info")),
        sync_incident(FAIL_LABEL, FAIL_TITLE, "Three or more hourly runs failed in the last 24 hours (each one also pushed 'V7 run failed'). Open the Actions tab. "
                      "This issue closes itself after 24 hours with no failed run.", fail_signal,
                      ("V7 hourly runs failing repeatedly", "3+ failed runs in 24 hours. Open the Actions tab.", "page"),
                      ("V7 hourly runs recovered", "No failed run in 24 hours.", "info")),
    ]
    sent = None
    if digest_due(now):
        try:
            domain_runs = _api("actions/workflows/domains.yml/runs?per_page=60")["workflow_runs"]
        except Exception:                                          # the check-in must still go out, and say what it could not read
            domain_runs = []
        title, body = health(runs, domain_runs, now)
        sent = (push or _push)(title, body, "info")
    print(f"watchdog: stale={is_stale} failing={fail_signal} changes={changes} checkin={sent}")
    return 0


def main():
    if "--watch" in sys.argv:
        return watch()
    current = int(os.environ["GITHUB_RUN_ID"])
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule" and os.environ.get("TICK") != "true":
        import time
        waited = 0                                                  # a manual run never cancels or replaces a tick: it has its own concurrency group and waits here for a running tick
        while waited < WAIT_MINUTES * 60:
            runs = _api("actions/workflows/hourly.yml/runs?per_page=15")["workflow_runs"]
            if not active(runs, current, TICK_TITLE):
                break
            time.sleep(30)
            waited += 30
        print(f"gate: manual run -> run (waited {waited // 60} min for a tick)")
        return 0
    runs = _api("actions/workflows/hourly.yml/runs?per_page=15")["workflow_runs"]
    now = dt.datetime.now(dt.timezone.utc)
    verdict = decide(runs, now, current)
    if verdict == "run" and active(runs, current, "manual"):
        verdict = "skip"                                            # a manual run is queued or running: it has priority, the next tick covers the hour
    print(f"gate: {verdict}")
    if verdict == "skip":
        _api(f"actions/runs/{current}/cancel", "POST")
        import time
        time.sleep(90)                                             # the cancel lands; later steps never start
    return 0


if __name__ == "__main__":
    sys.exit(main())
