"""Schedule gate: GitHub drops and delays scheduled triggers, so the workflow carries three crons per hour and this gate
keeps the effective cadence at about one run per hour. Counts only; no job data. Stdlib only.

decide(): run when the newest earlier TICK run (title "tick": scheduled or timer, never a manual run; not cancelled) started at least MIN_GAP_MINUTES ago, else skip.
stale(): the newest successful run finished more than STALE_MINUTES ago -> the pipeline needs a human; stalled() closes that incident only after the last 3 finished ticks passed (LIFE-690).
failing(): three or more failed ticks in the last 24 hours -> open; none in 24 hours -> recovered; in between -> unchanged (hysteresis, no flapping).
The watchdog owns the incident record (one GitHub issue per kind): it pushes first, and an incident whose push was not accepted stays labelled
`push-pending` and is pushed again on the next watchdog run (every 30 minutes) until the phone service accepts it."""
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

from lifeos.platform import health as health_model          # gate.health() below is the daily check-in's own function

TICK_TITLE = "tick"           # the workflow's run-name for scheduled and timer runs; manual runs are "manual" and never block a tick
MIN_GAP_MINUTES = 50          # three crons (:07 :27 :47) -> at most one run per ~hour; a dropped slot is covered by the next
GAP_FLOOR_MINUTES = 10        # the repository variable GATE_MIN_GAP_MINUTES may lower the gap to this (drain mode: about 20); anything else is ignored and the default stands
STALE_MINUTES = 150           # no successful run for this long -> open (or update) one GitHub issue
ISSUE_TITLE = "V7 pipeline: no successful run for over 2.5 hours"
LABEL = "pipeline-stale"
FAIL_WINDOW_HOURS = 24         # failed ticks counted over this window
FAIL_OPEN = 3                  # this many failed ticks in the window opens the "failing" incident; zero closes it
RECOVER_PASSES = 3             # LIFE-681: this many finished ticks in a row that succeeded close it (and keep it closed) whatever is left in the window
FAIL_TITLE = "V7 pipeline: hourly runs failing repeatedly (3+ failed in 24 hours)"
FAIL_LABEL = "pipeline-failing"
STUCK_TITLE = "V7 pipeline: an hourly tick run has had no conclusion for over 40 minutes"      # LIFE-580 step 3
STUCK_LABEL = "pipeline-run-stuck"
DIGEST_HOUR_UTC = 13           # the daily check-in: 8 AM Chicago in summer time (7 AM in winter); the :17 watchdog run sends it
CHECKIN_WORKFLOW = "watchdog.yml"
CHECKIN_UNREADABLE = "CHECKIN_HISTORY_UNREADABLE"      # LIFE-724: fixed code in the watchdog log line when the watchdog's own run list could not be read; it never pages by itself
DOMAINS_TITLE = "domains (after tick)"


def is_tick_event(environ):
    """LIFE-775: True for a domains run that belongs to a tick: the workflow_run after it, or the early run its prep job dispatches (DOMAINS_PHASE=tick, the workflow input)."""
    return environ.get("GITHUB_EVENT_NAME") == "workflow_run" or environ.get("DOMAINS_PHASE") == "tick"
PENDING = "push-pending"       # the incident exists but its phone push was not accepted yet; retried by the watchdog
REMIND_HOURS = 3               # LIFE-328/329: an incident still open and still true re-pages after this long without activity on its issue
CONFLICT_RUNS_MAX = 30         # LIFE-688: domains runs of the last 24 hours whose logs the daily check-in reads, newest first
CONFLICT_BYTES_MAX = 40_000_000   # and the most log bytes it downloads in all (one compressed archive per run)
CONFLICT_SECONDS_MAX = 90      # and the most time it spends: the watchdog job's own timeout is 3 minutes and its slowest run 34 seconds (docs/platform/timeouts.md)
CONFLICT_COUNT_FROM = dt.datetime(2026, 11, 15, 6, 5, tzinfo=dt.timezone.utc)      # Jim decided the writer chain at 12:05 AM CT on Nov 15 2026: earlier conflicts are history, so the first check-in is not triggered by a run that predates the fixes
CONFLICT_CODE = re.compile(r"NOTION_HTTP_409|[A-Z0-9_]*PAGE_WALK_NOTION_HTTP_404|PROTECTED_REGION_CHANGED|NOTION_HTTP_429")
JOB_LOG = re.compile(r"\d+_(.+)\.txt")                 # a job's log file in a run archive
CONFLICT_CODES = ("NOTION_HTTP_409", "PAGE_WALK_NOTION_HTTP_404", "PROTECTED_REGION_CHANGED")      # a write conflict; NOTION_HTTP_429 is a rate limit and counted apart
RATE_LIMIT_CODE = "NOTION_HTTP_429"
CONFLICT_LOGS_UNREADABLE = "WRITE_CONFLICT_LOGS_UNREADABLE"      # fixed codes of the count; they only ever appear in the check-in text and never page by themselves
CONFLICT_CAP_REACHED = "WRITE_CONFLICT_CAP_REACHED"
CONFLICT_UNAVAILABLE = "WRITE_CONFLICT_COUNT_UNAVAILABLE"
CONFLICT_RULE = "rule: any write conflict moves the Daily Report writers to the full chain (option A, LIFE-688)"

SCHEDULED_WORKFLOWS = {"safety.yml": 1, "watchdog.yml": 3}   # LIFE-692: each workflow with a `schedule:` trigger that no other alert covers -> how many finished scheduled runs in a row must pass to close its incident
SCHEDULED_COVERED = {"hourly.yml": "its scheduled runs are ticks: pipeline_failing, pipeline_stalled and run_failed already page for them"}      # the other scheduled workflows and why none is checked twice
SCHEDULED_OWN_ALERT = {"safety.yml": "Phone alert"}          # a failed run whose own alert step passed has already paged: the central check pushes no second alert for it
SCHEDULED_RUNS_READ = 8        # finished scheduled runs read per workflow (one list call each)
SCHEDULED_RED_FAMILY, SCHEDULED_OK_FAMILY = "scheduled_red", "scheduled_recovered"      # registry families scheduled_red_* (PAGE) and scheduled_recovered_* (INFO)
SCHEDULED_UNREADABLE = "SCHEDULED_RUNS_UNREADABLE"          # fixed code shown in the watchdog line when a workflow's runs cannot be read; it never pages by itself
SCHEDULED_FAILED = ("failure", "timed_out", "startup_failure")


def _t(value):
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if value else None


def _now():
    return dt.datetime.now(dt.timezone.utc)


def min_gap(environ=None):
    """The gap between ticks: the repository variable GATE_MIN_GAP_MINUTES when it is a whole number from GAP_FLOOR_MINUTES to MIN_GAP_MINUTES, else MIN_GAP_MINUTES."""
    raw = ((environ if environ is not None else os.environ).get("GATE_MIN_GAP_MINUTES") or "").strip()
    return int(raw) if raw.isdigit() and GAP_FLOOR_MINUTES <= int(raw) <= MIN_GAP_MINUTES else MIN_GAP_MINUTES


STUCK_MINUTES = 40             # LIFE-580: a tick with no conclusion this long after it was created is stuck (a real tick takes 5 to 20 minutes; the prep job's own timeout ends a run that is still going, so one this old is stuck)


def stuck(run, now, minutes=STUCK_MINUTES):
    """True for a run that has no conclusion yet and was created more than `minutes` ago."""
    created = _t(run.get("created_at"))
    return run.get("conclusion") is None and created is not None and (now - created) > dt.timedelta(minutes=minutes)


def stuck_ticks(runs, now):
    """LIFE-580 step 3: the tick runs that are queued or running and have had no conclusion for more than STUCK_MINUTES. The gate and the ticker already ignore them (so later ticks still run);
    this is what lets a person hear about one, because GitHub refuses every way to cancel it."""
    return [r for r in runs if r.get("display_title") == TICK_TITLE and r.get("status") in ACTIVE and stuck(r, now)]


REAP_GRACE_MINUTES = 20        # LIFE-691: a cancel GitHub accepted should have ended the run within one watchdog cycle; a tick run still active this long after the stuck limit is one the reaper cannot end


def reap(runs, now, call=None):
    """LIFE-691: ask GitHub to cancel every stuck tick run (queued or running more than STUCK_MINUTES). -> {"found": n, "cancelled": [ids GitHub accepted], "refused": [ids it refused, as it did on Nov 11 2026],
    "still_stuck": [ids still active STUCK_MINUTES + REAP_GRACE_MINUTES after they were created]}. `call(path, method)` is the GitHub API (injected in tests). Never raises: a refusal is data."""
    call = call or _api
    out = {"found": 0, "cancelled": [], "refused": [], "still_stuck": []}
    for run in stuck_ticks(runs, now):
        out["found"] += 1
        try:
            call(f"actions/runs/{run['id']}/cancel", "POST")
            out["cancelled"].append(run["id"])
        except Exception:                                          # noqa: BLE001 - GitHub refused, or the call failed: the incident (once) and the skip rule cover it
            out["refused"].append(run["id"])
        if stuck(run, now, STUCK_MINUTES + REAP_GRACE_MINUTES):
            out["still_stuck"].append(run["id"])
    return out


def reaper_alerts(reaped):
    """The stuck-run incident signal: True (open it) when GitHub refused a cancel or a run it accepted a cancel for is still active after the grace period; None (leave the incident as it is) while
    accepted cancels have not taken effect yet; False when no tick run is stuck. A cancel that worked raises no alert."""
    if reaped["refused"] or reaped["still_stuck"]:
        return True
    return None if reaped["found"] else False


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


def finished_ticks(runs):
    """Finished hourly ticks (success or failure), newest first. Manual and cancelled runs never count."""
    done = [run for run in runs if run.get("display_title") == TICK_TITLE and run.get("conclusion") in ("success", "failure")]
    return sorted(done, key=lambda run: run.get("updated_at") or run.get("created_at") or "", reverse=True)


def passes(runs):
    """LIFE-690: True when the last RECOVER_PASSES finished ticks all succeeded: the one rule every run-based incident closes on (docs/platform/health-model.md)."""
    return health_model.recovered([run.get("conclusion") for run in finished_ticks(runs)], RECOVER_PASSES)


def stalled(runs, now, current_id=0):
    """LIFE-690: -> True (open) | False (recovered) | None (unchanged). Open while no tick has succeeded for STALE_MINUTES; once a tick succeeded it closes only after the last 3 finished ticks passed,
    so a single lucky run cannot close the incident and the next failure reopen it (open and recovered pushes only on a real change of state)."""
    if stale(runs, now, current_id):
        return True
    return False if passes(runs) else None


def failing(runs, now, window_hours=FAIL_WINDOW_HOURS):
    """-> True (open) | False (recovered) | None (unchanged). Counts failed ticks only: manual runs and cancelled runs never count.
    LIFE-681: the last RECOVER_PASSES finished ticks all succeeded -> recovered at once (and never opened), however many failures are still inside the window."""
    if passes(runs):
        return False
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
    """True only inside the normal send window (the first half hour of DIGEST_HOUR_UTC). `checkin_decision` is what `watch` uses; this stays as the window test."""
    return now.hour == DIGEST_HOUR_UTC and now.minute < 30


def _ct(moment):
    """US Central clock text for a note in the check-in ("7:00 AM CT"); an unusable zone database falls back to the plain hour so a note never stops the check-in."""
    try:
        from zoneinfo import ZoneInfo                              # noqa: PLC0415
        local = moment.astimezone(ZoneInfo("America/Chicago"))
        return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} CT"
    except Exception:                                              # noqa: BLE001
        return f"{moment.hour}:{moment.minute:02d} UTC"


def checkin_decision(now, watchdog_runs, own_id=None):
    """LIFE-724: the daily check-in goes out ONCE per check-in day even when GitHub skips the 7 AM CT window (it started no watchdog run between 7:00 and 7:29 AM CT on Nov 15 2026, so no check-in came).
    The day opens at DIGEST_HOUR_UTC. The check-in is due when this run started at or after that hour and no OTHER watchdog run has started since it (each run that reaches this code sends it, so an earlier
    run in the day is the record; nothing new is stored). A run that failed or was cancelled does not count, so a day whose only runs died still gets its check-in. `watchdog_runs` is None when the run list
    could not be read: inside the normal window the check-in still goes (the old rule), outside it nothing is sent and the fixed code CHECKIN_HISTORY_UNREADABLE is logged.
    -> (due, note, code): `note` is a sentence appended to the check-in (late or history unreadable), `code` a fixed word for the watchdog log line."""
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
        if started and started >= start and now >= started and (run.get("status") != "completed" or run.get("conclusion") == "success"):
            return False, "", None                                  # an earlier run today already sent it (or is sending it)
    if in_window:
        return True, "", None
    return True, f" Sent late: GitHub did not start the watchdog in the {_ct(start)} window; this is the first run today, at {_ct(now)}.", None


def coverage(observed, window_hours=24):
    """LIFE-528 (LIFETMP-62): runs seen against runs expected in the window (one hourly tick an hour) -> (observed, expected, missing). A run missing from the list is a missed hour."""
    return observed, window_hours, max(0, window_hours - observed)


def health(runs, domain_runs, now, window_hours=24, late=(), conflicts=""):
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
            + (f" Late or missing scheduled jobs ({len(late)}): {', '.join(late)}." if late else "")      # LIFE-627
            + (f" {conflicts}" if conflicts else ""))                                                       # LIFE-688: the write-conflict line; nothing else in the check-in changes


def _api(path, method="GET", body=None):
    request = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                                                # hand the redirect back: the signed address must be fetched without the token


def _logs(run_id, limit):
    """LIFE-688: the log archive of one run, as bytes, at most `limit` of them (read-only: the token needs only actions: read). GitHub answers with a redirect to a signed address; that address is fetched
    without the token. Raises when GitHub refuses or the archive is larger than `limit`."""
    request = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}/logs",
                                     headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        urllib.request.build_opener(_NoRedirect).open(request, timeout=30)
        raise ValueError(CONFLICT_LOGS_UNREADABLE)                  # an answer without a redirect has no archive
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
    """-> {(job, code): 1} for the codes that surface in one run's job logs: each (job, fixed code) once per run, so a retry loop that prints a code twice counts once. A page-walk code with a job's prefix
    (`HIRING_PAGE_WALK_NOTION_HTTP_404`) counts as PAGE_WALK_NOTION_HTTP_404. Fixed codes only: no log line, name or id is kept."""
    found = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in archive.namelist():
            named = JOB_LOG.fullmatch(name)                        # GitHub stores each job's whole log as `<n>_<job>.txt`; the `<job>/system.txt` files are the runner's, not the job's
            if not named:
                continue
            job = named.group(1).strip()
            with archive.open(name) as handle:
                text = handle.read(5_000_000).decode("utf-8", "replace")
            for code in CONFLICT_CODE.findall(text):
                found[(job, "PAGE_WALK_NOTION_HTTP_404" if code.endswith("PAGE_WALK_NOTION_HTTP_404") else code)] = 1
    return found


def write_conflicts(domain_runs, now, logs=None, clock=time.monotonic, window_hours=24, count_from=CONFLICT_COUNT_FROM):
    """LIFE-688: the daily write-conflict count. Reads the logs of the domains runs finished in the last `window_hours` (newest first, at most CONFLICT_RUNS_MAX runs, CONFLICT_BYTES_MAX bytes and
    CONFLICT_SECONDS_MAX seconds in all; one archive per run) and counts, per job, the fixed codes that surfaced: write conflicts (409, page-walk 404, protected region changed) and, apart, rate limits (429).
    -> {"runs", "read", "unreadable", "capped", "conflicts": {job: n}, "rate_limits": {job: n}, "codes": {(job, code): n}}. Never raises for one run: an unreadable log is counted under the fixed code
    CONFLICT_LOGS_UNREADABLE (lifeos.platform.isolate), and the count says it is incomplete instead of guessing. Counts only; it never pages by itself."""
    isolate = importlib.import_module("lifeos.platform.isolate")    # GitHub watchdog only: the Oracle ticker never runs the check-in, so its installer does not copy isolate.py
    logs = logs or _logs
    cutoff = now - dt.timedelta(hours=window_hours)
    clipped = bool(count_from) and count_from > cutoff                 # the count starts at `count_from`: until a full window has passed it covers less than 24 hours, and says so
    cutoff = count_from if clipped else cutoff
    runs = sorted((r for r in domain_runs if r.get("conclusion") in ("success", "failure") and (_t(r.get("updated_at") or r.get("created_at")) or cutoff) > cutoff),
                  key=lambda r: r.get("created_at") or "", reverse=True)[:CONFLICT_RUNS_MAX]
    out = {"runs": len(runs), "read": 0, "unreadable": 0, "capped": False, "conflicts": {}, "rate_limits": {}, "codes": {}, "since": count_from if clipped else None}
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
    """The one line the daily check-in carries. N conflicts in the last 24 hours, per job, and rate limits apart; when N is above 0 the line states the rule plainly. An unreadable or capped count says
    so under its fixed code. `result` None means the runs themselves could not be listed."""
    if result is None:
        return f"Notion write conflicts: unavailable ({CONFLICT_UNAVAILABLE})."

    def by_job(counts):
        return ", ".join(f"{job} {n}" for job, n in sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    n, m = sum(result["conflicts"].values()), sum(result["rate_limits"].values())
    text = f"Notion write conflicts in the last 24 h: {n}" + (f" ({by_job(result['conflicts'])})" if n else "") + f"; rate limits: {m}" + (f" ({by_job(result['rate_limits'])})" if m else "") + "."
    notes = ([f"{CONFLICT_LOGS_UNREADABLE} for {result['unreadable']} of {result['runs']} runs"] if result["unreadable"] else []) \
        + ([f"{CONFLICT_CAP_REACHED} after {result['read']} of {result['runs']} runs"] if result["capped"] else [])
    if notes:
        text += " Count incomplete: " + "; ".join(notes) + "."
    if result.get("since"):
        text += " Counted since the writer-chain decision (Nov 15 2026, 12:05 AM CT)."          # a shorter window, said plainly, until 24 hours have passed
    return text + (f" {CONFLICT_RULE}." if n else "")


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


def scheduled_signal(runs, passes):
    """LIFE-692: True when the newest finished SCHEDULED run of a workflow failed (open), False when its newest `passes` finished scheduled runs passed (recovered), else None (unchanged).
    Cancelled, skipped and unfinished runs and manual runs never count; `runs` are the workflow's scheduled runs, any order."""
    finished = sorted((r for r in runs if r.get("event", "schedule") == "schedule" and r.get("conclusion") in SCHEDULED_FAILED + ("success",)),
                      key=lambda r: r.get("created_at") or "", reverse=True)
    if not finished:
        return None
    if finished[0]["conclusion"] in SCHEDULED_FAILED:
        return True
    return False if health_model.recovered([r["conclusion"] for r in finished], passes) else None


def _own_alert_fired(workflow, runs, api):
    """True when the newest failed scheduled run of `workflow` already pushed its own alert (its named step passed), so the central check stays quiet for that run: one alert per failure."""
    step = SCHEDULED_OWN_ALERT.get(workflow)
    failed = [r for r in runs if r.get("conclusion") in SCHEDULED_FAILED]
    if not step or not failed:
        return False
    newest = max(failed, key=lambda r: r.get("created_at") or "")
    jobs = api(f"actions/runs/{newest['id']}/jobs").get("jobs") or []
    return any(item.get("name") == step and item.get("conclusion") == "success" for job in jobs for item in job.get("steps") or [])


def scheduled_checks(now, push=None, api=None):
    """LIFE-692: every scheduled workflow that raises no alert of its own on failure pages ONCE when its newest scheduled run goes red and once (INFO) when it recovers: one GitHub issue per
    workflow (label `scheduled-red-<name>`), a registry key per state change, no reminders (a second red run adds nothing). One list call per workflow; an unreadable workflow is skipped under
    SCHEDULED_UNREADABLE and never pages by itself. It is self-contained on purpose: the Oracle ticker runs this module without isolate.py. -> {"changes": {workflow: change}, "unreadable": [workflow]}"""
    api, out = api or _api, {"changes": {}, "unreadable": []}
    for workflow, passes in SCHEDULED_WORKFLOWS.items():
        try:
            runs = api(f"actions/workflows/{workflow}/runs?event=schedule&per_page={SCHEDULED_RUNS_READ}")["workflow_runs"]
            signal = scheduled_signal(runs, passes)
            if signal is True and _own_alert_fired(workflow, runs, api):
                signal = None
            name = workflow.removesuffix(".yml")
            word = name.replace("-", "_")
            out["changes"][workflow] = sync_incident(
                f"scheduled-red-{name}", f"V7 scheduled workflow red: {name}",
                f"The newest scheduled run of the {name} workflow ({workflow}) failed. Open the Actions tab. This issue closes itself when its last {passes} finished scheduled run(s) passed.", signal,
                (f"V7 scheduled workflow failed: {name}", f"The scheduled {name} workflow failed on its newest run. Open the Actions tab.", "page", health_model.state_key(SCHEDULED_RED_FAMILY, word)),
                (f"V7 scheduled workflow recovered: {name}", f"The {name} workflow's scheduled runs pass again.", "info", health_model.state_key(SCHEDULED_OK_FAMILY, word)),
                push, now, remind=False)
        except Exception:                                          # noqa: BLE001 - one workflow's unreadable history is that workflow's gap, recorded by fixed code, never an alert
            out["unreadable"].append(workflow)
    return out


def watch(now=None, push=None, checkin=True):
    """Independent watchdog (its own workflow): the gate only runs when an hourly run starts, so a total stall (no GitHub schedule, no timer tick)
    would stay silent. Two incidents, both raised from outside the pipeline: no successful run for 2.5 hours, and three or more failed ticks in 24 hours.
    Never touches job data."""
    runs = _api("actions/workflows/hourly.yml/runs?per_page=60")["workflow_runs"]
    now = now or dt.datetime.now(dt.timezone.utc)
    reaped = reap(runs, now)                                       # LIFE-691: cancel what is stuck; alert (once, below) only when GitHub refuses or the run survives
    is_stale, fail_signal, stuck_signal = stalled(runs, now, 0), failing(runs, now), reaper_alerts(reaped)
    changes = [
        sync_incident(LABEL, ISSUE_TITLE, "The newest successful hourly run finished more than 2.5 hours ago. Scheduled triggers may be dropped by GitHub, "
                      "or a stage is failing. Open the Actions tab. This issue closes itself when the last 3 hourly runs have passed.", is_stale,
                      ("V7 pipeline stalled", "No hourly run has succeeded for 2.5 hours. Check cron-job.org and the Actions tab.", "page", "pipeline_stalled"),
                      ("V7 pipeline recovered", "The last 3 hourly runs passed.", "info", "pipeline_stalled_recovered"), push, now),
        sync_incident(FAIL_LABEL, FAIL_TITLE, "Three or more hourly runs failed in the last 24 hours (each one also pushed 'V7 run failed'). Open the Actions tab. "
                      "This issue closes itself when the last 3 hourly runs have passed (or after 24 hours with no failed run).", fail_signal,
                      ("V7 hourly runs failing repeatedly", "3+ failed runs in 24 hours. Open the Actions tab.", "page", "pipeline_failing"),
                      ("V7 hourly runs recovered", "The last 3 hourly runs passed.", "info", "pipeline_failing_recovered"), push, now, remind=not last_tick_passed(runs)),
        sync_incident(STUCK_LABEL, STUCK_TITLE, "An hourly tick run has been queued or running for more than 40 minutes. A real tick takes 5 to 20 minutes and the prep job's own timeout ends a run that is still going, so this run is stuck "
                      "(it happened on Nov 11 2026 and GitHub refused every cancel). The watchdog asks GitHub to cancel it; this issue opens only when GitHub refuses or the run survives the cancel. Later ticks are not blocked: the gate and the ticker ignore it. Open the Actions tab; if the run cannot be cancelled, "
                      "report it to GitHub support. This issue closes itself when no tick run is stuck.", stuck_signal,
                      ("V7 tick run stuck", "An hourly tick run has had no conclusion for over 40 minutes. Later ticks still run. Open the Actions tab.", "info", "tick_run_stuck"),
                      ("V7 stuck tick run cleared", "No hourly tick run is stuck.", "info", "tick_run_stuck_recovered"), push, now),
    ]
    scheduled = scheduled_checks(now, push)                      # LIFE-692: the scheduled workflows that raise no alert of their own
    sent, checkin_note, checkin_code = None, "", None
    if checkin:                                                    # LIFE-330: the Oracle ticker runs incidents only; the check-in stays with the GitHub watchdog
        try:                                                       # LIFE-724: one read of the watchdog's own recent runs tells whether today's check-in already went out
            history = _api(f"actions/workflows/{CHECKIN_WORKFLOW}/runs?per_page=30")["workflow_runs"]
        except Exception:                                          # noqa: BLE001 - unreadable: the decision falls back to the normal window only
            history = None
        due, checkin_note, checkin_code = checkin_decision(now, history, os.environ.get("GITHUB_RUN_ID"))
    else:
        due = False
    if due:
        try:
            domain_runs = _api("actions/workflows/domains.yml/runs?per_page=60")["workflow_runs"]
            listed = True
        except Exception:                                          # the check-in must still go out, and say what it could not read
            domain_runs, listed = [], False
        try:                                                       # LIFE-688: the daily write-conflict count; it can only add its line, never stop or change the rest of the check-in
            conflicts = write_conflicts(domain_runs, now) if listed else None
        except Exception:                                          # noqa: BLE001
            conflicts = None
        conflict_text = conflict_line(conflicts)
        try:                                                       # LIFE-627: late or missing scheduled jobs ride in the same check-in; an unreadable history adds nothing
            scheduled_jobs = importlib.import_module("lifeos.platform.scheduled_jobs")       # GitHub watchdog only: the Oracle ticker never runs the check-in, so its installer does not copy this module
            late = scheduled_jobs.late_names(scheduled_jobs.evaluate(now, scheduled_jobs.fetch(_api)))
        except Exception:                                          # noqa: BLE001
            late = ()
        title, body = health(runs, domain_runs, now, late=late, conflicts=conflict_text)
        body += checkin_note                                       # LIFE-724: a late or history-unreadable check-in says so, once, in its own text
        sent = (push or _push)(title, body, "info", "daily_check")          # LIFE-603: _push takes the registered alert key; the call without it crashed every real check-in
    print(f"watchdog: stale={is_stale} failing={fail_signal} changes={changes} checkin={sent} reaper={reaped['found']}/{len(reaped['cancelled'])}/{len(reaped['refused'])}/{len(reaped['still_stuck'])}" + (f" {SCHEDULED_UNREADABLE}={','.join(scheduled['unreadable'])}" if scheduled["unreadable"] else "") + (f" {checkin_code}" if checkin_code else ""))
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
