"""The Command Center's `Runs OK today` card: hourly ticks today (America/Chicago midnight on) that finished without a
failure, from GitHub's run history. A tick is a completed, non-cancelled `hourly.yml` run titled `tick`; it FAILED if
it or a follow-on run (`domains (after tick)`, `recruiters.yml`, `capacity-card.yml`) failed. Unreadable history is
`unverified`, never a healthy count.
"""
import datetime as dt
import json
import urllib.request
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.platform import gate

TZ = ZoneInfo("America/Chicago")
FAILED = ("failure", "timed_out", "startup_failure")
DONE = FAILED + ("success",)
TICKS = "hourly.yml"
FOLLOW_ON = {"domains.yml": gate.DOMAINS_TITLE, "recruiters.yml": None, "capacity-card.yml": None}
PER_PAGE, MAX_PAGES = 100, 5
NAME = "Runs OK today"


class Unreadable(Exception):
    """Fixed code only, never a value."""


def _get(environ, path, opener):
    token, repo = (environ.get("GITHUB_TOKEN") or "").strip(), (environ.get("GITHUB_REPOSITORY") or "").strip()
    if not token or not repo:
        raise Unreadable("RUNS_NO_TOKEN")
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with opener(request, timeout=15) as response:
            return json.loads(response.read() or b"null")
    except (OSError, ValueError):
        raise Unreadable("RUNS_GITHUB_UNREADABLE") from None


def _runs(environ, workflow, since, opener):
    out = []
    for page in range(1, MAX_PAGES + 1):
        body = _get(environ, f"actions/workflows/{workflow}/runs?created={quote('>=' + since)}&per_page={PER_PAGE}&page={page}", opener)
        batch = (body or {}).get("workflow_runs") if isinstance(body, dict) else None
        if not isinstance(batch, list):
            raise Unreadable("RUNS_GITHUB_UNREADABLE")
        out += batch
        if len(batch) < PER_PAGE:
            return out
    raise Unreadable("RUNS_TOO_MANY")


def _at(run):
    try:
        return dt.datetime.fromisoformat(str(run.get("created_at")).replace("Z", "+00:00"))
    except ValueError:
        raise Unreadable("RUNS_GITHUB_UNREADABLE") from None


def tally(ticks, follow_ons):
    """-> {"ok", "total", "failed": [run url, oldest first], "latest_failed"}."""
    ticks = sorted((t for t in ticks if t.get("display_title") == gate.TICK_TITLE and t.get("conclusion") in DONE), key=_at)
    bad = {t["id"]: t.get("conclusion") in FAILED for t in ticks}
    first_failed = {}
    for follow in follow_ons:
        if follow.get("conclusion") not in FAILED:
            continue
        owners = [t for t in ticks if _at(t) <= _at(follow)]
        if owners:
            owner = owners[-1]["id"]
            if not bad[owner]:
                first_failed[owner] = follow.get("html_url")
            bad[owner] = True
    failed = [(t.get("html_url") if t.get("conclusion") in FAILED else first_failed.get(t["id"]) or t.get("html_url")) for t in ticks if bad[t["id"]]]
    return {"ok": sum(1 for t in ticks if not bad[t["id"]]), "total": len(ticks), "failed": failed, "latest_failed": bool(ticks) and bad[ticks[-1]["id"]]}


def read(environ, now, opener=urllib.request.urlopen):
    """-> {"state": ok | failed | latest_failed | paused | none | unverified, "ok", "total", "failed": [urls], "why"}. Never raises."""
    now = now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = midnight.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        ticks = [r for r in _runs(environ, TICKS, since, opener) if _at(r) >= midnight]
        follow = [r for name, title in FOLLOW_ON.items() for r in _runs(environ, name, since, opener) if (title is None or r.get("display_title") == title) and _at(r) >= midnight]
        paused = (_get(environ, f"actions/workflows/{TICKS}", opener) or {}).get("state") not in (None, "active")
        result = tally(ticks, follow)
    except Unreadable as error:
        return {"state": "unverified", "ok": 0, "total": 0, "failed": [], "why": str(error)}
    if paused:
        state = "paused"
    elif result["total"] == 0:
        state = "none"
    elif result["latest_failed"]:
        state = "latest_failed"
    elif result["failed"]:
        state = "failed"
    else:
        state = "ok"
    return {"state": state, "ok": result["ok"], "total": result["total"], "failed": result["failed"], "why": ""}
