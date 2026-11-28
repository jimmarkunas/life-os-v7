"""Schedule gate and watchdog. GitHub drops and delays scheduled triggers, so the workflow carries three crons per
hour and this gate keeps the cadence at about one run per hour. Counts only; no job data. Stdlib only.

The watchdog owns the incident record (one GitHub issue per kind) and pushes first; an unaccepted push leaves the
label `push-pending` for the next run."""

import datetime as dt
import importlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile

from lifeos.platform import health as health_model

TICK_TITLE = "tick"
MIN_GAP_MINUTES = 50
GAP_FLOOR_MINUTES = 10
STALE_MINUTES = 150
ISSUE_TITLE = "V7 pipeline: no successful run for over 2.5 hours"
LABEL = "pipeline-stale"
FAIL_WINDOW_HOURS = 24
FAIL_OPEN = 3
RECOVER_PASSES = 3
API_READ_TRIES = 3  # a GET to the GitHub API that answers 5xx or drops is repeated; a POST or PATCH never is
API_READ_PAUSE = 2
FAIL_TITLE = "V7 pipeline: hourly runs failing repeatedly (3+ failed in 24 hours)"
FAIL_LABEL = "pipeline-failing"
STUCK_TITLE = "V7 pipeline: an hourly tick run has had no conclusion for over 40 minutes"
STUCK_LABEL = "pipeline-run-stuck"
DIGEST_HOUR_UTC = 13
CHECKIN_WORKFLOW = "watchdog.yml"
CHECKIN_UNREADABLE = "CHECKIN_HISTORY_UNREADABLE"
DOMAINS_TITLE = "domains (after tick)"


def is_tick_event(environ):
    """True for a domains run that belongs to a tick (the workflow_run after it, or its early prep-job run)."""
    return environ.get("GITHUB_EVENT_NAME") == "workflow_run" or environ.get("DOMAINS_PHASE") == "tick"


PENDING = "push-pending"
REMIND_HOURS = 3
CONFLICT_RUNS_MAX = 30
CONFLICT_BYTES_MAX = 40_000_000
CONFLICT_SECONDS_MAX = 90
CONFLICT_COUNT_FROM = dt.datetime(2026, 11, 15, 6, 5, tzinfo=dt.timezone.utc)  # older conflicts are not counted
CONFLICT_CODE = re.compile(
    r"NOTION_HTTP_409|[A-Z0-9_]*PAGE_WALK_NOTION_HTTP_404|PROTECTED_REGION_CHANGED|NOTION_HTTP_429"
)
JOB_LOG = re.compile(r"\d+_(.+)\.txt")
CONFLICT_CODES = ("NOTION_HTTP_409", "PAGE_WALK_NOTION_HTTP_404", "PROTECTED_REGION_CHANGED")
RATE_LIMIT_CODE = "NOTION_HTTP_429"
CONFLICT_LOGS_UNREADABLE = "WRITE_CONFLICT_LOGS_UNREADABLE"
CONFLICT_CAP_REACHED = "WRITE_CONFLICT_CAP_REACHED"
CONFLICT_UNAVAILABLE = "WRITE_CONFLICT_COUNT_UNAVAILABLE"
CONFLICT_RULE = "rule: any write conflict moves the Daily Report writers to the full chain (option A, LIFE-688)"

SCHEDULED_WORKFLOWS = {"safety.yml": 1, "watchdog.yml": 3}
SCHEDULED_COVERED = {
    "hourly.yml": "its scheduled runs are ticks: pipeline_failing, pipeline_stalled and run_failed already page for them",
    "v3-pages.yml": "its stages are continue-on-error and only warn (LIFE-730), so the run does not go red from a stage; no second alert",
}
SCHEDULED_OWN_ALERT = {"safety.yml": "Phone alert"}
SCHEDULED_RUNS_READ = 8
SCHEDULED_RED_FAMILY, SCHEDULED_OK_FAMILY = "scheduled_red", "scheduled_recovered"
SCHEDULED_UNREADABLE = "SCHEDULED_RUNS_UNREADABLE"
SCHEDULED_FAILED = ("failure", "timed_out", "startup_failure")


def _t(value):
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if value else None


def _now():
    return dt.datetime.now(dt.timezone.utc)


def min_gap(environ=None):
    raw = ((environ if environ is not None else os.environ).get("GATE_MIN_GAP_MINUTES") or "").strip()
    return int(raw) if raw.isdigit() and GAP_FLOOR_MINUTES <= int(raw) <= MIN_GAP_MINUTES else MIN_GAP_MINUTES


STUCK_MINUTES = 40


def stuck(run, now, minutes=STUCK_MINUTES):
    created = _t(run.get("created_at"))
    return run.get("conclusion") is None and created is not None and (now - created) > dt.timedelta(minutes=minutes)


def stuck_ticks(runs, now):
    """Tick runs queued or running with no conclusion for more than STUCK_MINUTES; the gate ignores them."""
    return [r for r in runs if r.get("display_title") == TICK_TITLE and r.get("status") in ACTIVE and stuck(r, now)]


REAP_GRACE_MINUTES = 20


def reap(runs, now, call=None):
    call = call or _api
    out = {"found": 0, "cancelled": [], "refused": [], "still_stuck": []}
    for run in stuck_ticks(runs, now):
        out["found"] += 1
        try:
            call(f"actions/runs/{run['id']}/cancel", "POST")
            out["cancelled"].append(run["id"])
        except Exception:  # noqa:
            out["refused"].append(run["id"])
        if stuck(run, now, STUCK_MINUTES + REAP_GRACE_MINUTES):
            out["still_stuck"].append(run["id"])
    return out


def reaper_alerts(reaped):
    """True (open) when a cancel was refused or left the run active past the grace period; None while pending; False
    when none is stuck.
    """
    if reaped["refused"] or reaped["still_stuck"]:
        return True
    return None if reaped["found"] else False


def decide(runs, now, current_id, gap=None):
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
WAIT_MINUTES = 25


def active(runs, current_id, title, now=None):
    return [
        r
        for r in runs
        if r["id"] != current_id
        and r.get("status") in ACTIVE
        and r.get("display_title") == title
        and not (now is not None and stuck(r, now))
    ]


def ahead(runs, current_id, title, created, now=None):
    return [r for r in active(runs, current_id, title, now) if (r.get("created_at") or "") < created]


def wait_turn(title, current_id, created, fetch, sleep, minutes=WAIT_MINUTES, clock=None):
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


def finished_ticks(runs):
    done = [
        run
        for run in runs
        if run.get("display_title") == TICK_TITLE and run.get("conclusion") in ("success", "failure")
    ]
    return sorted(done, key=lambda run: run.get("updated_at") or run.get("created_at") or "", reverse=True)


def passes(runs):
    return health_model.recovered([run.get("conclusion") for run in finished_ticks(runs)], RECOVER_PASSES)


def stalled(runs, now, current_id=0):
    """-> True (open) | False (recovered) | None. No tick succeeded for STALE_MINUTES; closes after RECOVER_PASSES."""
    if stale(runs, now, current_id):
        return True
    return False if passes(runs) else None


def failing(runs, now, window_hours=FAIL_WINDOW_HOURS):
    """-> True (open) | False (recovered) | None. Failed ticks only; closes after RECOVER_PASSES."""
    if passes(runs):
        return False
    cutoff = now - dt.timedelta(hours=window_hours)
    failed = sum(
        1
        for run in runs
        if run.get("display_title") == TICK_TITLE
        and run.get("conclusion") == "failure"
        and (_t(run.get("updated_at") or run.get("created_at")) or cutoff) > cutoff
    )
    if failed >= FAIL_OPEN:
        return True
    return False if failed == 0 else None


def last_tick_passed(runs):
    """True when the newest finished tick succeeded: the failing incident stays open but is not re-paged."""
    finished = [
        run
        for run in runs
        if run.get("display_title") == TICK_TITLE and run.get("conclusion") in ("success", "failure")
    ]
    newest = max(finished, key=lambda run: run.get("updated_at") or run.get("created_at") or "", default=None)
    return bool(newest) and newest.get("conclusion") == "success"


def digest_due(now):
    return now.hour == DIGEST_HOUR_UTC and now.minute < 30


def _ct(moment):
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        local = moment.astimezone(ZoneInfo("America/Chicago"))
        return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} CT"
    except Exception:  # noqa: BLE001
        return f"{moment.hour}:{moment.minute:02d} UTC"


def checkin_decision(now, watchdog_runs, own_id=None):
    start = now.replace(hour=DIGEST_HOUR_UTC, minute=0, second=0, microsecond=0)
    if now < start:
        return False, "", None
    in_window = digest_due(now)
    if watchdog_runs is None:
        if in_window:
            return True, " Check-in history was unreadable; sent in the normal window.", CHECKIN_UNREADABLE
        return False, "", CHECKIN_UNREADABLE
    for run in watchdog_runs:
        if run.get("name") != "watchdog" or str(run.get("id")) == str(own_id):
            continue
        started = _t(run.get("run_started_at") or run.get("created_at"))
        if (
            started
            and started >= start
            and now >= started
            and (run.get("status") != "completed" or run.get("conclusion") == "success")
        ):
            return False, "", None
    if in_window:
        return True, "", None
    return (
        True,
        f" Sent late: GitHub did not start the watchdog in the {_ct(start)} window; this is the first run today, at {_ct(now)}.",
        None,
    )


def coverage(observed, window_hours=24):
    return observed, window_hours, max(0, window_hours - observed)


def health(runs, domain_runs, now, window_hours=24, late=(), conflicts=""):
    cutoff = now - dt.timedelta(hours=window_hours)
    recent = [
        r
        for r in runs
        if r.get("display_title") == TICK_TITLE
        and r.get("conclusion") in ("success", "failure")
        and (_t(r.get("updated_at") or r.get("created_at")) or cutoff) > cutoff
    ]
    failed = sum(1 for r in recent if r["conclusion"] == "failure")
    good = [r for r in recent if r["conclusion"] == "success"]
    since = int((now - max(_t(r["updated_at"]) for r in good)).total_seconds() // 60) if good else None
    domains = sum(
        1
        for r in domain_runs
        if r.get("display_title") == DOMAINS_TITLE
        and r.get("conclusion") == "failure"
        and (_t(r.get("updated_at") or r.get("created_at")) or cutoff) > cutoff
    )
    ok = failed == 0 and domains == 0 and since is not None and since < 90
    last = "no good run" if since is None else f"last good run {since} min ago"
    return (
        f"V7 daily check: {'OK' if ok else 'DEGRADED'}",
        f"Last 24 h: {len(recent)} hourly runs, {len(good)} ok, {failed} failed; {last}; {domains} failed domain run(s). "
        f"Coverage: {len(recent)} of {window_hours} expected, {coverage(len(recent), window_hours)[2]} missed."
        + (f" Late or missing scheduled jobs ({len(late)}): {', '.join(late)}." if late else "")
        + (f" {conflicts}" if conflicts else ""),
    )


def _api(path, method="GET", body=None):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    for attempt in range(API_READ_TRIES if method == "GET" else 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            if method != "GET" or error.code < 500 or attempt == API_READ_TRIES - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if method != "GET" or attempt == API_READ_TRIES - 1:
                raise
        time.sleep(API_READ_PAUSE * (attempt + 1))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _logs(run_id, limit):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}/logs",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        urllib.request.build_opener(_NoRedirect).open(request, timeout=30)
        raise ValueError(CONFLICT_LOGS_UNREADABLE)
    except urllib.error.HTTPError as answer:
        target = answer.headers.get("Location") if answer.code in (301, 302, 303, 307, 308) else None
        if not target:
            raise
    with urllib.request.urlopen(urllib.request.Request(target), timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(CONFLICT_LOGS_UNREADABLE)
    return data


def conflict_codes_in(archive_bytes):
    found = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in archive.namelist():
            named = JOB_LOG.fullmatch(name)
            if not named:
                continue
            job = named.group(1).strip()
            with archive.open(name) as handle:
                text = handle.read(5_000_000).decode("utf-8", "replace")
            for code in CONFLICT_CODE.findall(text):
                found[(job, "PAGE_WALK_NOTION_HTTP_404" if code.endswith("PAGE_WALK_NOTION_HTTP_404") else code)] = 1
    return found


def write_conflicts(domain_runs, now, logs=None, clock=time.monotonic, window_hours=24, count_from=CONFLICT_COUNT_FROM):
    """Daily write-conflict count over the last `window_hours` of domains runs: per job, conflicts and, apart, rate
    limits. Never raises; never pages.
    """
    isolate = importlib.import_module("lifeos.platform.isolate")
    logs = logs or _logs
    cutoff = now - dt.timedelta(hours=window_hours)
    clipped = bool(count_from) and count_from > cutoff
    cutoff = count_from if clipped else cutoff
    runs = sorted(
        (
            r
            for r in domain_runs
            if r.get("conclusion") in ("success", "failure")
            and (_t(r.get("updated_at") or r.get("created_at")) or cutoff) > cutoff
        ),
        key=lambda r: r.get("created_at") or "",
        reverse=True,
    )[:CONFLICT_RUNS_MAX]
    out = {
        "runs": len(runs),
        "read": 0,
        "unreadable": 0,
        "capped": False,
        "conflicts": {},
        "rate_limits": {},
        "codes": {},
        "since": count_from if clipped else None,
    }
    started, left = clock(), [CONFLICT_BYTES_MAX]

    def one(run):
        if clock() - started > CONFLICT_SECONDS_MAX or left[0] <= 0:
            out["capped"] = True
            return None
        data = logs(run["id"], left[0])
        left[0] -= len(data)
        return conflict_codes_in(data)

    done = isolate.run_items(runs, one, code_of=lambda error: CONFLICT_LOGS_UNREADABLE)
    out["unreadable"] = len(done.failures)
    for _, found in done.results:
        if found is None:
            continue
        out["read"] += 1
        for (job, code), n in found.items():
            out["codes"][(job, code)] = out["codes"].get((job, code), 0) + n
            kind = "rate_limits" if code == RATE_LIMIT_CODE else "conflicts"
            out[kind][job] = out[kind].get(job, 0) + n
    return out


def conflict_line(result):
    if result is None:
        return f"Notion write conflicts: unavailable ({CONFLICT_UNAVAILABLE})."

    def by_job(counts):
        return ", ".join(f"{job} {n}" for job, n in sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    n, m = sum(result["conflicts"].values()), sum(result["rate_limits"].values())
    text = (
        f"Notion write conflicts in the last 24 h: {n}"
        + (f" ({by_job(result['conflicts'])})" if n else "")
        + f"; rate limits: {m}"
        + (f" ({by_job(result['rate_limits'])})" if m else "")
        + "."
    )
    notes = (
        [f"{CONFLICT_LOGS_UNREADABLE} for {result['unreadable']} of {result['runs']} runs"]
        if result["unreadable"]
        else []
    ) + ([f"{CONFLICT_CAP_REACHED} after {result['read']} of {result['runs']} runs"] if result["capped"] else [])
    if notes:
        text += " Count incomplete: " + "; ".join(notes) + "."
    if result.get("since"):
        text += " Counted since the writer-chain decision (Nov 15 2026, 12:05 AM CT)."
    return text + (f" {CONFLICT_RULE}." if n else "")


def _push(title, body, severity, key):
    from lifeos.platform import alerts  # noqa: PLC0415

    return alerts.push(key, title, body)


def _hours_open(issue, now):
    opened = _t(issue.get("created_at"))
    return int((now - opened).total_seconds() // 3600) if opened else None


def remind_due(issue, now, hours=REMIND_HOURS):
    quiet = _t(issue.get("updated_at") or issue.get("created_at"))
    return bool(quiet) and (now - quiet) >= dt.timedelta(hours=hours)


def sync_incident(label, title, body, signal, open_push, close_push, push=None, now=None, remind=True):
    """One incident = one GitHub issue. signal: True open | False recovered | None unchanged."""
    push = push or _push
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        issues = _api(f"issues?state=open&labels={label}&per_page=5")
        if signal is True and not issues:
            ok = push(*open_push)
            try:
                _api("labels", "POST", {"name": label, "color": "b60205"})
            except Exception:
                pass
            _api("issues", "POST", {"title": title, "labels": [label] + ([] if ok else [PENDING]), "body": body})
            return "opened"
        if signal is False and issues:
            push(*close_push)
            for issue in issues:
                _api(f"issues/{issue['number']}", "PATCH", {"state": "closed", "state_reason": "completed"})
            return "closed"
        retried = None
        for issue in issues:
            if signal is not False and PENDING in [
                (item.get("name") if isinstance(item, dict) else item) for item in issue.get("labels", [])
            ]:
                if push(*open_push):
                    _api(f"issues/{issue['number']}/labels/{PENDING}", "DELETE")
                retried = "retried"
            elif signal is True and remind and open_push[2] == "page" and remind_due(issue, now):
                age = _hours_open(issue, now)
                still = f"{open_push[1]} Still open after {age} h." if age is not None else open_push[1]
                if push(f"Still open: {open_push[0]}", still, *open_push[2:]):
                    _api(
                        f"issues/{issue['number']}/comments",
                        "POST",
                        {
                            "body": f"Reminder pushed to the phone (open {age} h). The next one follows after {REMIND_HOURS} quiet hours."
                        },
                    )
                retried = "reminded"
        return retried
    except Exception as error:
        print(f"gate: incident update skipped ({type(error).__name__})")
        return None


def scheduled_signal(runs, passes):
    finished = sorted(
        (
            r
            for r in runs
            if r.get("event", "schedule") == "schedule" and r.get("conclusion") in SCHEDULED_FAILED + ("success",)
        ),
        key=lambda r: r.get("created_at") or "",
        reverse=True,
    )
    if not finished:
        return None
    if finished[0]["conclusion"] in SCHEDULED_FAILED:
        return True
    return False if health_model.recovered([r["conclusion"] for r in finished], passes) else None


def _own_alert_fired(workflow, runs, api):
    step = SCHEDULED_OWN_ALERT.get(workflow)
    failed = [r for r in runs if r.get("conclusion") in SCHEDULED_FAILED]
    if not step or not failed:
        return False
    newest = max(failed, key=lambda r: r.get("created_at") or "")
    jobs = api(f"actions/runs/{newest['id']}/jobs").get("jobs") or []
    return any(
        item.get("name") == step and item.get("conclusion") == "success"
        for job in jobs
        for item in job.get("steps") or []
    )


def scheduled_checks(now, push=None, api=None):
    api, out = api or _api, {"changes": {}, "unreadable": []}
    for workflow, passes in SCHEDULED_WORKFLOWS.items():
        try:
            runs = api(f"actions/workflows/{workflow}/runs?event=schedule&per_page={SCHEDULED_RUNS_READ}")[
                "workflow_runs"
            ]
            signal = scheduled_signal(runs, passes)
            if signal is True and _own_alert_fired(workflow, runs, api):
                signal = None
            name = workflow.removesuffix(".yml")
            word = name.replace("-", "_")
            out["changes"][workflow] = sync_incident(
                f"scheduled-red-{name}",
                f"V7 scheduled workflow red: {name}",
                f"The newest scheduled run of the {name} workflow ({workflow}) failed. Open the Actions tab. This issue closes itself when its last {passes} finished scheduled run(s) passed.",
                signal,
                (
                    f"V7 scheduled workflow failed: {name}",
                    f"The scheduled {name} workflow failed on its newest run. Open the Actions tab.",
                    "page",
                    health_model.state_key(SCHEDULED_RED_FAMILY, word),
                ),
                (
                    f"V7 scheduled workflow recovered: {name}",
                    f"The {name} workflow's scheduled runs pass again.",
                    "info",
                    health_model.state_key(SCHEDULED_OK_FAMILY, word),
                ),
                push,
                now,
                remind=False,
            )
        except Exception:  # noqa:
            out["unreadable"].append(workflow)
    return out


def watch(now=None, push=None, checkin=True):
    runs = _api("actions/workflows/hourly.yml/runs?per_page=60")["workflow_runs"]
    now = now or dt.datetime.now(dt.timezone.utc)
    reaped = reap(runs, now)
    is_stale, fail_signal, stuck_signal = stalled(runs, now, 0), failing(runs, now), reaper_alerts(reaped)
    changes = [
        sync_incident(
            LABEL,
            ISSUE_TITLE,
            "The newest successful hourly run finished more than 2.5 hours ago. Scheduled triggers may be dropped by GitHub, "
            "or a stage is failing. Open the Actions tab. This issue closes itself when the last 3 hourly runs have passed.",
            is_stale,
            (
                "V7 pipeline stalled",
                "No hourly run has succeeded for 2.5 hours. Check cron-job.org and the Actions tab.",
                "page",
                "pipeline_stalled",
            ),
            ("V7 pipeline recovered", "The last 3 hourly runs passed.", "info", "pipeline_stalled_recovered"),
            push,
            now,
        ),
        sync_incident(
            FAIL_LABEL,
            FAIL_TITLE,
            "Three or more hourly runs failed in the last 24 hours (each one also pushed 'V7 run failed'). Open the Actions tab. "
            "This issue closes itself when the last 3 hourly runs have passed (or after 24 hours with no failed run).",
            fail_signal,
            (
                "V7 hourly runs failing repeatedly",
                "3+ failed runs in 24 hours. Open the Actions tab.",
                "page",
                "pipeline_failing",
            ),
            ("V7 hourly runs recovered", "The last 3 hourly runs passed.", "info", "pipeline_failing_recovered"),
            push,
            now,
            remind=not last_tick_passed(runs),
        ),
        sync_incident(
            STUCK_LABEL,
            STUCK_TITLE,
            "An hourly tick run has been queued or running for more than 40 minutes. A real tick takes 5 to 20 minutes and the prep job's own timeout ends a run that is still going, so this run is stuck "
            "(it happened on Nov 11 2026 and GitHub refused every cancel). The watchdog asks GitHub to cancel it; this issue opens only when GitHub refuses or the run survives the cancel. Later ticks are not blocked: the gate and the ticker ignore it. Open the Actions tab; if the run cannot be cancelled, "
            "report it to GitHub support. This issue closes itself when no tick run is stuck.",
            stuck_signal,
            (
                "V7 tick run stuck",
                "An hourly tick run has had no conclusion for over 40 minutes. Later ticks still run. Open the Actions tab.",
                "info",
                "tick_run_stuck",
            ),
            ("V7 stuck tick run cleared", "No hourly tick run is stuck.", "info", "tick_run_stuck_recovered"),
            push,
            now,
        ),
    ]
    scheduled = scheduled_checks(now, push)
    sent, checkin_note, checkin_code = None, "", None
    if checkin:
        try:
            history = _api(f"actions/workflows/{CHECKIN_WORKFLOW}/runs?per_page=30")["workflow_runs"]
        except Exception:  # noqa:
            history = None
        due, checkin_note, checkin_code = checkin_decision(now, history, os.environ.get("GITHUB_RUN_ID"))
    else:
        due = False
    if due:
        try:
            domain_runs = _api("actions/workflows/domains.yml/runs?per_page=60")["workflow_runs"]
            listed = True
        except Exception:
            domain_runs, listed = [], False
        try:
            conflicts = write_conflicts(domain_runs, now) if listed else None
        except Exception:  # noqa: BLE001
            conflicts = None
        conflict_text = conflict_line(conflicts)
        try:  # an unreadable history adds nothing
            scheduled_jobs = importlib.import_module("lifeos.platform.scheduled_jobs")
            late = scheduled_jobs.late_names(scheduled_jobs.evaluate(now, scheduled_jobs.fetch(_api)))
        except Exception:  # noqa: BLE001
            late = ()
        title, body = health(runs, domain_runs, now, late=late, conflicts=conflict_text)
        body += checkin_note
        sent = (push or _push)(title, body, "info", "daily_check")
    print(
        f"watchdog: stale={is_stale} failing={fail_signal} changes={changes} checkin={sent} reaper={reaped['found']}/{len(reaped['cancelled'])}/{len(reaped['refused'])}/{len(reaped['still_stuck'])}"
        + (f" {SCHEDULED_UNREADABLE}={','.join(scheduled['unreadable'])}" if scheduled["unreadable"] else "")
        + (f" {checkin_code}" if checkin_code else "")
    )
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
        waited, _ = wait_turn(TICK_TITLE, current, created, fetch, time.sleep, clock=_now)
        print(f"gate: manual run -> run (waited {waited} min for a tick)")
        return 0
    verdict = decide(fetch(), dt.datetime.now(dt.timezone.utc), current)
    if verdict == "run":
        waited, turn = wait_turn("manual", current, created, fetch, time.sleep, clock=_now)
        verdict = "run" if turn else "skip"
        print(f"gate: waited {waited} min for a manual run")
    print(f"gate: {verdict}")
    if verdict == "skip":
        _api(f"actions/runs/{current}/cancel", "POST")
        import time

        time.sleep(90)
    return 0


if __name__ == "__main__":
    sys.exit(main())
