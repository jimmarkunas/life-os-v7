"""LIFE-854: the four employer-board windows (1:00 AM, 7:00 AM, 1:00 PM, 7:00 PM Central), decided from boards.yml's own run history.

    python3 -m lifeos.platform.boards_window            run by the `window` job; writes run, mode, limit, timeout_minutes, deadline_minutes, slot, slot_ct and late_reason to $GITHUB_OUTPUT
    python3 -m lifeos.platform.boards_window dispatch   run by every hourly tick: starts boards.yml (auto) when a window has opened and holds no run yet

GitHub's own cron never fired for this workflow (Nov 22: no run at 12, 13 or 18 UTC), so the hourly tick, which the Oracle ticker starts reliably, starts the window. The two crons stay as a backup; the gate
makes any extra firing a skip. A moment belongs to the latest window that has opened (Central time); a window with a run (scheduled, auto or still running) is a duplicate; a previous window with no successful
run makes this one a catch-up (leftovers first); an unreadable history runs a normal window and says so. A manual run always runs and holds nothing. Counts and fixed codes only; stdlib only."""
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

from lifeos.platform import gate, limits, switches

CENTRAL = ZoneInfo("America/Chicago")
SLOTS_CT = ((1, 0), (7, 0), (13, 0), (19, 0))
WORKFLOW = "boards.yml"
CRAWL_JOB = "boards"                      # the job that crawls; the gate job beside it is `window`
HISTORY_RUNS = 30                         # four days of windows, with room for manual runs between them
RUNNING = ("queued", "in_progress", "waiting", "pending")
LATE_MINUTES = 30                         # a start later than this after the window opened is named in the receipt
REAL = ("success", "failure", "timed_out")
WINDOW_TIMEOUT_MINUTES = 15               # Jim, Nov 22 2026 (LIFE-854; was 35: the measured window is about 5 minutes)
CATCHUP_TIMEOUT_MINUTES = 25               # was 60
DEADLINE_MARGIN_MINUTES = 3               # the crawl stops starting boards this long before its step timeout, so its receipt is always written


SWITCH = "BOARDS_IN_TICK"                 # LIFE-905: lifeos/platform/switches.json; off = the four windows own the crawl (today), on = the hourly tick does


def owner(environ):
    """-> "tick" or "windows": the one crawler that owns the employer boards. Each stands down while the other owns them; both read the same state tables, so a flip loses nothing."""
    return "tick" if switches.state(SWITCH, environ) else "windows"


def window_running(api=gate._api):
    """-> True when a boards.yml run is queued or running (the tick then leaves boards alone), or when that cannot be read (fail closed: never two crawlers)."""
    try:
        runs = api(f"actions/workflows/{WORKFLOW}/runs?per_page=10").get("workflow_runs", [])
        return any(item.get("status") in RUNNING for item in runs)
    except Exception:                                               # noqa: BLE001 - an unreadable history means a crawler may be running
        return True


def _utc(day, hour, minute):
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=CENTRAL).astimezone(dt.timezone.utc)


def current_slot(now):
    """-> the start (UTC) of the latest window that has opened by `now`."""
    local = now.astimezone(CENTRAL)
    best = None
    for back in (0, 1):
        day = (local - dt.timedelta(days=back)).date()
        for hour, minute in SLOTS_CT:
            moment = _utc(day, hour, minute)
            if moment <= now and (best is None or moment > best):
                best = moment
    return best


def previous_slot(slot):
    return current_slot(slot - dt.timedelta(seconds=1))


def next_slot(slot):
    """-> the start of the window after the one that opened at `slot`."""
    moment = slot
    while current_slot(moment) == slot:
        moment += dt.timedelta(minutes=30)
    return current_slot(moment)


def label(slot):
    moment = slot.astimezone(CENTRAL)
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'} CT"


def _started(item):
    value = item.get("run_started_at") or item["created_at"]
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def counts(item):
    """Does this run hold a window? A scheduled run, or one the hourly tick started (its run name ends in ` auto`); a manual run holds nothing."""
    return item.get("event") == "schedule" or (item.get("event") == "workflow_dispatch" and str(item.get("display_title") or "").endswith(" auto"))


def plan(slot, mode, late_reason):
    """-> the numbers a window runs with: the cap, the step timeout and the soft deadline inside it."""
    catchup = mode == "catchup"
    timeout = CATCHUP_TIMEOUT_MINUTES if catchup else WINDOW_TIMEOUT_MINUTES
    return {"run": True, "mode": mode, "limit": limits.BOARDS_CATCHUP_PER_RUN if catchup else limits.BOARDS_PER_RUN, "timeout_minutes": timeout,
            "deadline_minutes": timeout - DEADLINE_MARGIN_MINUTES, "slot": slot.strftime("%Y-%m-%dT%H:%M:%SZ"), "slot_ct": label(slot), "late_reason": late_reason, "why": mode}


def stand_down(slot, why):
    return {"run": False, "why": why, "slot": slot.strftime("%Y-%m-%dT%H:%M:%SZ"), "slot_ct": label(slot), "mode": "none", "limit": 0, "timeout_minutes": 0, "deadline_minutes": 0, "late_reason": ""}


def decide(now, runs, jobs_of, current_id=0):
    """-> the plan for this firing, or {"run": False, "why": "window_already_ran", ...}. `runs`: finished runs of the workflow, newest first; `jobs_of(run)` -> that run's job records (name, conclusion, status)."""
    slot = current_slot(now)
    held = {}                                                       # window start -> True when its run succeeded
    try:
        for item in runs:
            if item.get("id") == current_id or not counts(item):
                continue
            if item.get("status") in RUNNING:
                held[current_slot(_started(item))] = True                   # a window already being crawled (or waiting its turn) is not started twice
                continue
            crawl = [j for j in jobs_of(item) if j.get("name") == CRAWL_JOB]
            if not any(j.get("conclusion") in REAL for j in crawl):
                continue                                            # cancelled, skipped by the gate or never started: it held no window
            ok = any(j.get("conclusion") == "success" for j in crawl)
            window = current_slot(_started(item))
            held[window] = held.get(window, False) or ok
    except Exception:                                               # noqa: BLE001 - any failure to read history means a normal window, and says so
        return plan(slot, "window", "history_unreadable")
    if slot in held:
        return stand_down(slot, "window_already_ran")
    reasons = []
    mode = "window"
    if held and not held.get(previous_slot(slot), False):
        mode = "catchup"
        reasons.append("previous_window_incomplete" if previous_slot(slot) in held else "previous_window_missed")
    minutes = int((now - slot).total_seconds() // 60)
    if minutes > LATE_MINUTES:
        reasons.append(f"started_{minutes}_min_after_the_window_opened")
    return plan(slot, mode, "; ".join(reasons))


def fetch_history(api=gate._api):
    runs = api(f"actions/workflows/{WORKFLOW}/runs?per_page={HISTORY_RUNS}").get("workflow_runs", [])
    return runs, lambda run: api(f"actions/runs/{run['id']}/jobs?per_page=100").get("jobs", [])


def summary(result):
    lines = ["### Employer boards: window decision (LIFE-854)", "", f"- Window: {result['slot_ct']}", f"- Mode: {result['mode']}"]
    if result["run"]:
        lines += [f"- Cap: {result['limit']} boards, step timeout {result['timeout_minutes']} minutes"]
    lines += [f"- Decision: {'run' if result['run'] else 'skip (' + result['why'] + ')'}"]
    if result["late_reason"]:
        lines += [f"- Late-window reason: {result['late_reason']}"]
    return "\n".join(lines) + "\n"


def main(environ=os.environ, api=gate._api, now=None, out=print):
    now = now or dt.datetime.now(dt.timezone.utc)
    if environ.get("BOARDS_EVENT") == "workflow_dispatch" and environ.get("BOARDS_AUTO") != "true":
        mode = "catchup" if environ.get("BOARDS_MODE_INPUT") == "catchup" else "window"
        result = plan(current_slot(now), mode, "manual_run")
    elif owner(environ) == "tick":                                  # LIFE-905: the hourly tick crawls the boards; a scheduled or auto window stands down (a manual run above never does)
        result = stand_down(current_slot(now), "tick_owns")
    else:
        try:
            runs, jobs_of = fetch_history(api)
            result = decide(now, runs, jobs_of, int(environ.get("GITHUB_RUN_ID") or 0))
        except Exception as error:                                  # noqa: BLE001 - the history call itself failed
            out(f"boards window: history unreadable ({type(error).__name__}); a normal window runs")
            result = plan(current_slot(now), "window", "history_unreadable")
    text = summary(result)
    out(text)
    lines = "".join(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n" for key, value in result.items())
    for path, content in ((environ.get("GITHUB_OUTPUT"), lines), (environ.get("GITHUB_STEP_SUMMARY"), text)):
        if path:
            with open(path, "a") as handle:
                handle.write(content)
    return 0


def dispatch(environ=os.environ, api=gate._api, now=None, out=print):
    """Called by the hourly tick: start boards.yml (auto) when a window has opened and holds no run. Never fails the tick (a problem is a printed code and exit 0)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if owner(environ) == "tick":
        out("boards window: the hourly tick owns the boards; no window started")
        return 0
    try:
        runs, jobs_of = fetch_history(api)
        result = decide(now, runs, jobs_of)
    except Exception as error:                                              # noqa: BLE001 - an unreadable history starts a normal window; the gate in the run decides again
        out(f"boards window: history unreadable ({type(error).__name__}); starting the window")
        result = plan(current_slot(now), "window", "history_unreadable")
    if not result["run"]:
        out(f"boards window: {result['slot_ct']} already has a run; nothing to start")
        return 0
    try:
        api(f"actions/workflows/{WORKFLOW}/dispatches", "POST", {"ref": environ.get("GITHUB_REF_NAME") or "main", "inputs": {"auto": "true", "mode": result["mode"]}})
    except Exception as error:                                              # noqa: BLE001 - the backup crons or the next hourly tick start it; the tick itself must not fail
        out(f"boards window: dispatch failed ({type(error).__name__}); the next tick tries again")
        return 0
    out(f"boards window: started the {result['slot_ct']} window ({result['mode']}{'; ' + result['late_reason'] if result['late_reason'] else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(dispatch() if sys.argv[1:] == ["dispatch"] else main())
