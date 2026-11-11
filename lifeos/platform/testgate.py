"""LIFE-570: should the hourly prep job re-run the unit tests?

pr-tests already runs the full suite on every push to main. When the commit an hourly run is about to test already has a green `pr-tests` push run on main, re-running the same
tests proves nothing new, so prep skips them and says why. FAIL CLOSED: anything but a confirmed green run on THIS exact commit (a lookup that fails, no run, a newer run that is not
green, a run too old, a SHA that is not a full commit id) means the tests RUN. The age cap keeps the time-dependent tests (the index freshness check, date-based checks) running a few
times a day even when main does not change.

    python -m lifeos.platform.testgate        # env: GITHUB_SHA, GITHUB_REPOSITORY, GITHUB_TOKEN; writes skip=true|false to $GITHUB_OUTPUT and one line to $GITHUB_STEP_SUMMARY

Stdlib only. Counts and a short commit id; no job data."""
import datetime as dt
import json
import os
import re
import urllib.request

WORKFLOW = "pr-tests.yml"
PATH = ".github/workflows/pr-tests.yml"
SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_AGE_HOURS = 6                       # a green run older than this does not excuse the hourly run (time-dependent tests still run about four times a day)


def _t(value):
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def decide(runs, sha, now, max_age_hours=MAX_AGE_HOURS):
    """-> (skip: bool, reason: str). `runs`: the Actions runs of pr-tests for this commit. Only a confirmed green push run on main for exactly `sha`, finished recently, skips."""
    if not isinstance(sha, str) or not SHA.match(sha):
        return False, "no full commit id"
    mine = [r for r in (runs or []) if isinstance(r, dict) and r.get("head_sha") == sha and r.get("head_branch") == "main" and r.get("event") == "push" and r.get("path") == PATH]
    if not mine:
        return False, "no pr-tests push run on main for this commit"
    newest = max(mine, key=lambda r: (r.get("id") or 0, r.get("run_attempt") or 0))
    if newest.get("status") != "completed":
        return False, "the newest pr-tests run is still going"
    if newest.get("conclusion") != "success":
        return False, f"the newest pr-tests run is {newest.get('conclusion') or 'unfinished'}, not green"
    try:
        done = _t(newest["updated_at"])
    except (KeyError, ValueError, TypeError):
        return False, "the green run has no readable finish time"
    age = now - done
    if age < dt.timedelta(0) or age > dt.timedelta(hours=max_age_hours):
        return False, f"the green run is older than {max_age_hours} hours"
    return True, f"pr-tests green on SHA {sha[:12]} (finished {int(age.total_seconds() // 60)} min ago)"


def fetch(sha, repo, token, opener=urllib.request.urlopen):
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW}/runs?branch=main&event=push&head_sha={sha}&per_page=20",
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "lifeos-testgate"})
    with opener(request, timeout=30) as response:
        return json.loads(response.read()).get("workflow_runs") or []


def main(environ=None, opener=urllib.request.urlopen, now=None):
    environ = os.environ if environ is None else environ
    sha, repo, token = environ.get("GITHUB_SHA", ""), environ.get("GITHUB_REPOSITORY", ""), environ.get("GITHUB_TOKEN", "")
    try:
        skip, reason = decide(fetch(sha, repo, token, opener), sha, now or dt.datetime.now(dt.timezone.utc))
    except Exception:                                                    # noqa: BLE001 - fail closed: any lookup problem means the tests run
        skip, reason = False, "the pr-tests lookup failed"
    line = f"tests skipped: {reason}" if skip else f"tests run: {reason}"
    print(line)
    for path, text in ((environ.get("GITHUB_OUTPUT"), f"skip={'true' if skip else 'false'}\n"), (environ.get("GITHUB_STEP_SUMMARY"), f"{line}\n")):
        if path:
            with open(path, "a") as handle:
                handle.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
