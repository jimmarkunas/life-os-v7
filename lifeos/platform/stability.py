"""LIFE-694: the stability clock, how many completed clean days in a row the platform has had (cap 7), counted from GitHub's own history.

A clean day (America/Chicago calendar day) is a day with no failed hourly tick, no failed domains run and no platform incident issue open. A platform incident issue is one labelled with a type in PLATFORM_LABELS
or a `scheduled-red-<workflow>` label (docs/platform/stability-clock.md lists what counts and what does not). Counting starts on START, the day this merged: no earlier day is ever credited, and today's partial day is not.
Read-only: two GitHub list calls with the job's own token; it never writes and keeps no store. Anything it cannot read (no token, an error, a list longer than it will page) is `unverified`, never a healthy 0."""
import datetime as dt
import json
import time
import urllib.request
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.platform import gate

TZ = ZoneInfo("America/Chicago")
START = dt.date(2026, 11, 15)                    # the Chicago day LIFE-694 merged: counting starts here
NEED = 7
PLATFORM_LABELS = ("pipeline-stale", "pipeline-failing", "pipeline-run-stuck", "deploy-stalled")
SCHEDULED_RED = "scheduled-red-"                  # LIFE-692: one label per scheduled workflow the watchdog covers (gate.SCHEDULED_WORKFLOWS), `scheduled-red-<name without .yml>`
RUN_TITLES = {"hourly.yml": gate.TICK_TITLE, "domains.yml": gate.DOMAINS_TITLE}
FAILED = ("failure", "timed_out", "startup_failure")
PER_PAGE, MAX_PAGES, DEADLINE_SECONDS = 100, 5, 40


class Unreadable(Exception):
    """Fixed code only (never a value)."""


def _get(environ, path, opener):
    token, repo = (environ.get("GITHUB_TOKEN") or "").strip(), (environ.get("GITHUB_REPOSITORY") or "").strip()
    if not token or not repo:
        raise Unreadable("STABILITY_NO_TOKEN")
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with opener(request, timeout=15) as response:
            return json.loads(response.read() or b"null")
    except (OSError, ValueError):
        raise Unreadable("STABILITY_GITHUB_UNREADABLE") from None


def _pages(environ, path, key, opener, began):
    """Every item of a list endpoint, MAX_PAGES at most; a list that is longer, or a read that takes too long, is unreadable (never a partial answer)."""
    items = []
    for page in range(1, MAX_PAGES + 1):
        if time.monotonic() - began > DEADLINE_SECONDS:
            raise Unreadable("STABILITY_GITHUB_UNREADABLE")
        body = _get(environ, f"{path}{'&' if '?' in path else '?'}per_page={PER_PAGE}&page={page}", opener)
        batch = (body or {}).get(key) if key else body
        if not isinstance(batch, list):
            raise Unreadable("STABILITY_GITHUB_UNREADABLE")
        items += batch
        if len(batch) < PER_PAGE:
            return items
    raise Unreadable("STABILITY_TOO_MANY")


def _day(stamp):
    try:
        return dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(TZ).date()
    except ValueError:
        raise Unreadable("STABILITY_GITHUB_UNREADABLE") from None


def counted_labels():
    """Every issue label whose open issue makes a day not clean: the platform incident types plus one scheduled-red label per scheduled workflow the watchdog covers."""
    return PLATFORM_LABELS + tuple(SCHEDULED_RED + name.removesuffix(".yml") for name in gate.SCHEDULED_WORKFLOWS)


def failure_days(environ, first, today, opener=urllib.request.urlopen):
    """The Chicago days from `first` to `today` on which a tick or domains run failed or a platform incident issue was open."""
    began, bad = time.monotonic(), set()
    since = (dt.datetime.combine(first, dt.time(), TZ) - dt.timedelta(days=1)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for workflow, title in RUN_TITLES.items():
        runs = _pages(environ, f"actions/workflows/{workflow}/runs?status=completed&created={quote('>=' + (first - dt.timedelta(days=1)).isoformat())}", "workflow_runs", opener, began)
        for run in runs:
            if run.get("display_title") == title and run.get("conclusion") in FAILED:
                bad.add(_day(run.get("updated_at") or run.get("created_at")))
    for label in counted_labels():                                   # a list per label (open ones, and closed ones updated lately): bounded, and a long list of other issues can never hide an incident
        issues = _pages(environ, f"issues?state=open&labels={quote(label)}", None, opener, began) + _pages(environ, f"issues?state=closed&labels={quote(label)}&since={quote(since)}", None, opener, began)
        for issue in issues:
            if "pull_request" in issue:
                continue
            opened, closed = _day(issue.get("created_at")), (_day(issue["closed_at"]) if issue.get("closed_at") else None)
            day = max(opened, first)
            while day <= today and (closed is None or day <= closed):
                bad.add(day)
                day += dt.timedelta(days=1)
    return {d for d in bad if first <= d <= today}


def count(bad_days, today, start=START):
    """-> (clean days in a row, state). Pure. `bad_days`: Chicago days with a failure or an open platform incident issue. A failure today resets at once; today itself is never credited."""
    first = max(start, today - dt.timedelta(days=NEED))
    yesterday = today - dt.timedelta(days=1)
    last = max((d for d in bad_days if first <= d <= today), default=None)
    if last is None:
        days = max(0, (yesterday - first).days + 1)
    else:
        days = max(0, (yesterday - last).days)
    days = min(days, NEED)
    if days >= NEED:
        return NEED, "complete"
    return days, ("reset" if days == 0 and last is not None else "counting")


def clock(environ, now, opener=urllib.request.urlopen, start=START):
    """-> {"state": counting | reset | complete | unverified, "days": n, "why": fixed code or ""}. Never raises."""
    today = now.astimezone(TZ).date() if now.tzinfo else now.date()
    try:
        first = max(start, today - dt.timedelta(days=NEED))
        days, state = count(failure_days(environ, first, today, opener), today, start)
    except Unreadable as error:
        return {"state": "unverified", "days": 0, "why": str(error)}
    return {"state": state, "days": days, "why": ""}
