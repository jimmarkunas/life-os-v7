"""Schedule gate: GitHub drops and delays scheduled triggers, so the workflow carries three crons per hour and this gate
keeps the effective cadence at about one run per hour. Counts only; no job data. Stdlib only.

decide(): run when the newest earlier TICK run (title "tick": scheduled or timer, never a manual run; not cancelled) started at least MIN_GAP_MINUTES ago, else skip.
stale(): the newest successful run finished more than STALE_MINUTES ago -> the pipeline needs a human.
failing(): three or more failed ticks in the last 24 hours -> open; none in 24 hours -> recovered; in between -> unchanged (hysteresis, no flapping).
The watchdog owns the incident record (one GitHub issue per kind): it pushes first, and an incident whose push was not accepted stays labelled
`push-pending` and is pushed again on the next watchdog run (every 30 minutes) until the phone service accepts it."""
import datetime as dt
import importlib
import json
import os
import sys
import urllib.request

TICK_TITLE = "tick"           # the workflow's run-name for scheduled and timer runs; manual runs are "manual" and never block a tick
MIN_GAP_MINUTES = 50          # three crons (:07 :27 :47) -> at most one run per ~hour; a dropped slot is covered by the next
GAP_FLOOR_MINUTES = 10        # the repository variable GATE_MIN_GAP_MINUTES may lower the gap to this (drain mode: about 20); anything else is ignored and the default stands
STALE_MINUTES = 150           # no successful run for this long -> open (or update) one GitHub issue
ISSUE_TITLE = "V7 pipeline: no successful run for over 2.5 hours"
LABEL = "pipeline-stale"
FAIL_WINDOW_HOURS = 24         # failed ticks counted over this window
FAIL_OPEN = 3                  # this many failed ticks in the window opens the "failing" incident; zero closes it
FAIL_TITLE = "V7 pipeline: hourly runs failing repeatedly (3+ failed in 24 hours)"
FAIL_LABEL = "pipeline-failing"
STUCK_TITLE = "V7 pipeline: an hourly tick run has had no conclusion for over 40 minutes"      # LIFE-580 step 3
STUCK_LABEL = "pipeline-run-stuck"
DIGEST_HOUR_UTC = 13           # the daily check-in: 8 AM Chicago in summer time (7 AM in winter); the :17 watchdog run sends it
DOMAINS_TITLE = "domains (after tick)"
PENDING = "push-pending"       # the incident exists but its phone push was not accepted yet; retried by the watchdog
REMIND_HOURS = 3               # LIFE-328/329: an incident still open and still true re-pages after this long without activity on its issue


def _t(value):
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if value else None


def _now():
    return dt.datetime.now(dt.timezone.utc)


def min_gap(environ=None):
    """The gap between ticks: the repository variable GATE_MIN_GAP_MINUTES when it is a whole number from GAP_FLOOR_MINUTES to MIN_GAP_MINUTES, else MIN_GAP_MINUTES."""
    raw = ((environ if environ is not None else os.environ).get("GATE_MIN_GAP_MINUTES") or "").strip()
    return int(raw) if raw.isdigit() and GAP_FLOOR_MINUTES <= int(raw) <= MIN_GAP_MINUTES else MIN_GAP_MINUTES


STUCK_MINUTES = 40             # LIFE-580: a tick with no conclusion this long after it was created is stuck (a real tick takes 5 to 20 minutes; GitHub ends its prep job at 45)


def stuck(run, now, minutes=STUCK_MINUTES):
    """True for a run that has no conclusion yet and was created more than `minutes` ago."""
    created = _t(run.get("created_at"))
    return run.get("conclusion") is None and created is not None and (now - created) > dt.timedelta(minutes=minutes)


def stuck_ticks(runs, now):
    """LIFE-580 step 3: the tick runs that are queued or running and have had no conclusion for more than STUCK_MINUTES. The gate and the ticker already ignore them (so later ticks still run);
    this is what lets a person hear about one, because GitHub refuses every way to cancel it."""
    return [r for r in runs if r.get("display_title") == TICK_TITLE and r.get("status") in ACTIVE and stuck(r, now)]


def decide(runs, now, current_id, gap=None):
    """-> 'run' | 'skip'. Cancelled (including skipped) runs and manual runs do not count. LIFE-580: nor does a tick run that has had no conclusion for more than
    STUCK_MINUTES (GitHub left run 43095033439 queued for good on Nov 11 2026 and refused every cancel): one stuck run must not stop every later tick."""
    gap = min_gap() if gap is None else gap
    for run in runs:
        if run["id"] == current_id or run.get("conclusion") == "cancelled" or run.get("display_title") != TICK_TITLE:
            continue
        if stuck(run, now):
            continue
        started = _t(run.get("run_started_at") or run.get("created_at"))
        if started and (now - started) < dt.timedelta(minutes=gap):
            return "skip"
    return "run"


ACTIVE = ("queued", "in_progress", "pending", "waiting")
WAIT_MINUTES = 25                  # how long a run waits in line for an older run of the other kind (D210)


def active(runs, current_id, title, now=None):
    """Other runs of this workflow with this title (tick | manual) that are queued or running now. LIFE-604: with `now`, a run that is stuck (see `stuck`) is not counted,
    the same cut-off `decide` has; without it nothing is cut."""
    return [r for r in runs if r["id"] != current_id and r.get("status") in ACTIVE and r.get("display_title") == title and not (now is not None and stuck(r, now))]


def ahead(runs, current_id, title, created, now=None):
    """D210: the active runs of this title that were created before this run. Ticks and manual runs take turns in creation order:
    each waits only for OLDER runs of the other kind, so two runs can never wait on each other. `now` as for `active`."""
    return [r for r in active(runs, current_id, title, now) if (r.get("created_at") or "") < created]


def wait_turn(title, current_id, created, fetch, sleep, minutes=WAIT_MINUTES, clock=None):
    """Wait while an older `title` run is active. -> (minutes waited, True when it is this run's turn, False when the wait ran out). `clock` (returns the current time) turns on the stuck cut-off."""
    waited = 0
    while True:
        if not ahead(fetch(), current_id, title, created, clock() if clock else None):
            return waited // 60, True
        if waited >= minutes * 60:
            return waited // 60, False
        sleep(30)
        waited += 30


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


def last_tick_passed(runs):
    """LIFE-675: True when the newest finished tick succeeded. The failing incident then stays open until its failures age out, but is not re-paged."""
    finished = [run for run in runs if run.get("display_title") == TICK_TITLE and run.get("conclusion") in ("success", "failure")]
    newest = max(finished, key=lambda run: run.get("updated_at") or run.get("created_at") or "", default=None)
    return bool(newest) and newest.get("conclusion") == "success"


def digest_due(now):
    return now.hour == DIGEST_HOUR_UTC and now.minute < 30


def coverage(observed, window_hours=24):
    """LIFE-528 (LIFETMP-62): runs seen against runs expected in the window (one hourly tick an hour) -> (observed, expected, missing). A run missing from the list is a missed hour."""
    return observed, window_hours, max(0, window_hours - observed)


def health(runs, domain_runs, now, window_hours=24, late=()):
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
            f"Last 24 h: {len(recent)} hourly runs, {len(good)} ok, {failed} failed; {last}; {domains} failed domain run(s). "
            f"Coverage: {len(recent)} of {window_hours} expected, {coverage(len(recent), window_hours)[2]} missed."
            + (f" Late or missing scheduled jobs ({len(late)}): {', '.join(late)}." if late else ""))      # LIFE-627


def _api(path, method="GET", body=None):
    request = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def _push(title, body, severity, key):
    """LIFE-75: the incident's push goes through the alert registry by key; its severity is the registry's, the `severity` here is only what the call site meant (a test checks they agree)."""
    from lifeos.platform import alerts                              # noqa: PLC0415
    return alerts.push(key, title, body)


def _hours_open(issue, now):
    opened = _t(issue.get("created_at"))
    return int((now - opened).total_seconds() // 3600) if opened else None


def remind_due(issue, now, hours=REMIND_HOURS):
    """LIFE-329: due when the issue has been quiet (no update: opening, a reminder comment, a label change, Jim's own comment) for `hours`.
    The issue itself is the record, so the watchdog stays stateless; an issue with no timestamp is never reminded."""
    quiet = _t(issue.get("updated_at") or issue.get("created_at"))
    return bool(quiet) and (now - quiet) >= dt.timedelta(hours=hours)


def sync_incident(label, title, body, signal, open_push, close_push, push=None, now=None, remind=True):
    """One incident = one GitHub issue. signal: True open | False recovered | None unchanged. -> "opened" | "closed" | "retried" | "reminded" | None.
    Push first: an incident whose push is not accepted is still opened (the record must exist) but labelled `push-pending`, so the next run pushes again.
    LIFE-329: while the signal is still True, an open incident re-pages every REMIND_HOURS (one page on Nov 3 2026 at 4 AM went unseen for a 21-hour outage);
    an accepted reminder adds one comment to the issue, which restarts the clock. An unchanged signal (None) never reminds."""
    push = push or _push
    now = now or dt.datetime.now(dt.timezone.utc)
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
            elif signal is True and remind and open_push[2] == "page" and remind_due(issue, now):    # LIFE-675: an INFO incident (drift, a stuck run) is pushed once, never re-paged
                age = _hours_open(issue, now)
                still = f"{open_push[1]} Still open after {age} h." if age is not None else open_push[1]
                if push(f"Still open: {open_push[0]}", still, *open_push[2:]):
                    _api(f"issues/{issue['number']}/comments", "POST", {"body": f"Reminder pushed to the phone (open {age} h). The next one follows after {REMIND_HOURS} quiet hours."})
                retried = "reminded"
        return retried
    except Exception as error:                                     # alerting must never stop the pipeline
        print(f"gate: incident update skipped ({type(error).__name__})")
        return None


def watch(now=None, push=None, checkin=True):
    """Independent watchdog (its own workflow): the gate only runs when an hourly run starts, so a total stall (no GitHub schedule, no timer tick)
    would stay silent. Two incidents, both raised from outside the pipeline: no successful run for 2.5 hours, and three or more failed ticks in 24 hours.
    Never touches job data."""
    runs = _api("actions/workflows/hourly.yml/runs?per_page=60")["workflow_runs"]
    now = now or dt.datetime.now(dt.timezone.utc)
    is_stale, fail_signal, stuck_signal = stale(runs, now, 0), failing(runs, now), bool(stuck_ticks(runs, now))
    changes = [
        sync_incident(LABEL, ISSUE_TITLE, "The newest successful hourly run finished more than 2.5 hours ago. Scheduled triggers may be dropped by GitHub, "
                      "or a stage is failing. Open the Actions tab. This issue closes itself when a run succeeds.", is_stale,
                      ("V7 pipeline stalled", "No hourly run has succeeded for 2.5 hours. Check cron-job.org and the Actions tab.", "page", "pipeline_stalled"),
                      ("V7 pipeline recovered", "A run succeeded.", "info", "pipeline_stalled_recovered"), push, now),
        sync_incident(FAIL_LABEL, FAIL_TITLE, "Three or more hourly runs failed in the last 24 hours (each one also pushed 'V7 run failed'). Open the Actions tab. "
                      "This issue closes itself after 24 hours with no failed run.", fail_signal,
                      ("V7 hourly runs failing repeatedly", "3+ failed runs in 24 hours. Open the Actions tab.", "page", "pipeline_failing"),
                      ("V7 hourly runs recovered", "No failed run in 24 hours.", "info", "pipeline_failing_recovered"), push, now, remind=not last_tick_passed(runs)),
        sync_incident(STUCK_LABEL, STUCK_TITLE, "An hourly tick run has been queued or running for more than 40 minutes. A real tick takes 5 to 20 minutes and GitHub ends the prep job at 45, so this run is stuck "
                      "(it happened on Nov 11 2026 and GitHub refused every cancel). Later ticks are not blocked: the gate and the ticker ignore it. Open the Actions tab; if the run cannot be cancelled, "
                      "report it to GitHub support. This issue closes itself when no tick run is stuck.", stuck_signal,
                      ("V7 tick run stuck", "An hourly tick run has had no conclusion for over 40 minutes. Later ticks still run. Open the Actions tab.", "info", "tick_run_stuck"),
                      ("V7 stuck tick run cleared", "No hourly tick run is stuck.", "info", "tick_run_stuck_recovered"), push, now),
    ]
    sent = None
    if checkin and digest_due(now):                               # LIFE-330: the Oracle ticker runs incidents only; the check-in stays with the GitHub watchdog
        try:
            domain_runs = _api("actions/workflows/domains.yml/runs?per_page=60")["workflow_runs"]
        except Exception:                                          # the check-in must still go out, and say what it could not read
            domain_runs = []
        try:                                                       # LIFE-627: late or missing scheduled jobs ride in the same check-in; an unreadable history adds nothing
            scheduled_jobs = importlib.import_module("lifeos.platform.scheduled_jobs")       # GitHub watchdog only: the Oracle ticker never runs the check-in, so its installer does not copy this module
            late = scheduled_jobs.late_names(scheduled_jobs.evaluate(now, scheduled_jobs.fetch(_api)))
        except Exception:                                          # noqa: BLE001
            late = ()
        title, body = health(runs, domain_runs, now, late=late)
        sent = (push or _push)(title, body, "info", "daily_check")          # LIFE-603: _push takes the registered alert key; the call without it crashed every real check-in
    print(f"watchdog: stale={is_stale} failing={fail_signal} changes={changes} checkin={sent}")
    return 0


def main():
    if "--watch" in sys.argv:
        return watch()
    import time
    current = int(os.environ["GITHUB_RUN_ID"])
    created = _api(f"actions/runs/{current}")["created_at"]

    def fetch():
        return _api("actions/workflows/hourly.yml/runs?per_page=15")["workflow_runs"]

    if os.environ.get("GITHUB_EVENT_NAME") != "schedule" and os.environ.get("TICK") != "true":
        waited, _ = wait_turn(TICK_TITLE, current, created, fetch, time.sleep, clock=_now)   # a manual run never cancels a tick: it waits for an older one, then goes on
        print(f"gate: manual run -> run (waited {waited} min for a tick)")
        return 0
    verdict = decide(fetch(), dt.datetime.now(dt.timezone.utc), current)
    if verdict == "run":
        waited, turn = wait_turn("manual", current, created, fetch, time.sleep, clock=_now)  # D210: a tick queues behind an older manual run instead of cancelling itself
        verdict = "run" if turn else "skip"                         # still busy after the wait: the next tick covers the hour
        print(f"gate: waited {waited} min for a manual run")
    print(f"gate: {verdict}")
    if verdict == "skip":
        _api(f"actions/runs/{current}/cancel", "POST")
        import time
        time.sleep(90)                                             # the cancel lands; later steps never start
    return 0


if __name__ == "__main__":
    sys.exit(main())
