"""LIFE-975 step 4d: the System table's Connections row, and the one writer of the System health database's owned rows."""
# Owned rows are those with `Managed by = v7-inbox-writer`.
# The database moved by id to Monitoring & Alerts (data source from the LIFEOS_NOTION_SYSTEM_HEALTH_SOURCE setting). Its only writer was the LIFE-711 view writer; LIFE-792 (Nov 16 2026) retired that
# writer with the mirror view and nothing wrote the rows again, so they have said `Last OK 11/16 · 8:30 PM CT` ever since. This module is the writer now, and it writes only owned rows.
# A connection works when the domains job that uses it passed (GitHub's run history of domains.yml, read with the token the step already has; no new credential). Last OK is when that job last finished
# with success; Health is 🟢 ok when its newest finished run passed and 🔴 unavailable when it failed. A row with no job is a connection nothing exercises yet (Concord: the Agent Inbox's Concord source
# is read-only and refuses live reads, lifeos/inbox/sources.py LIVE_READS_ENABLED): it stays `⚪ not connected` and counts as down, because Concord should work (Jim, Nov 25 2026). Off by design: TGP
# Everything, School Outlook, Bytalos Asana. The Todoist row is retired: archived once, never written again. Names, counts and fixed words only.
import datetime as dt
import json
import os
import urllib.request

from lifeos.platform import ct_stamp, notion_ids
from lifeos.platform.notion_client import NotionError

SOURCE_NAME = "SYSTEM_HEALTH_SOURCE"                              # the setting row LIFEOS_NOTION_SYSTEM_HEALTH_SOURCE holds the data source id (never in source)
MANAGED_BY = "v7-inbox-writer"
OFF_BY_DESIGN = ("health:tgp:everything", "health:personal:school_outlook", "health:bytalos:asana")
RETIRED = ("health:personal:todoist",)
JOBS = {                                     # record id -> the domains.yml job that exercises the connection
    "health:personal:jira": "jira", "health:bytalos:jira": "jira",
    "health:personal:calendar": "agenda", "health:bytalos:calendar": "agenda",
    "health:personal:outlook:work": "outlook", "health:personal:outlook:personal": "outlook",
    "health:personal:notion": "dcc",
    "health:personal:interview_os": "interview",
    "health:personal:gmail": "recruiters", "health:bytalos:gmail": "recruiters",
    "health:all:followup": "followup",
}
CONCORD = "health:all:concord"
NAMES = {"health:all:concord": "Concord"}
OK, FAILING, NOT_CONNECTED = "🟢 ok", "🔴 unavailable", "⚪ not connected"
RUNS = 12                                    # the newest domains runs read for evidence (about half a day of ticks)
API = "https://api.github.com"


class ConnectionsError(NotionError):
    pass


def _text(prop):
    return "".join(x.get("plain_text", "") for x in (prop or {}).get("rich_text") or (prop or {}).get("title") or [])


def _select(prop):
    return ((prop or {}).get("select") or {}).get("name") or ""


def source_id(environ=os.environ):
    return notion_ids.configured_id(SOURCE_NAME, environ)


def read_rows(client, source):
    """-> [{"page", "id", "name", "workspace", "health", "last_ok"}] for every row this writer owns. Anything else in the database is never read into the list."""
    if not source:
        raise ConnectionsError("CONNECTIONS_NOT_CONFIGURED")
    rows, cursor = [], None
    body = {"page_size": 100, "filter": {"property": "Managed by", "select": {"equals": MANAGED_BY}}}
    for _ in range(10):
        reply = client.call("POST", f"/data_sources/{source}/query", {**body, **({"start_cursor": cursor} if cursor else {})})
        for page in reply.get("results", []):
            props = page.get("properties", {})
            rows.append({"page": page["id"], "id": _text(props.get("Record ID")), "name": _text(props.get("Source")).split(" (")[0], "workspace": _select(props.get("Workspace")), "health": _select(props.get("Health")), "last_ok": _text(props.get("Last OK"))})
        cursor = reply.get("next_cursor")
        if not reply.get("has_more") or not cursor:
            return rows
    raise ConnectionsError("CONNECTIONS_LISTING_INCOMPLETE")


def last_ok_time(text, now):
    """`11/16 · 8:30 PM CT` -> an aware Central datetime (a date that lands after now is the year before), or None for `—` or anything unreadable."""
    found = ct_stamp.read(text or "")
    if not found or found[0] is None:
        return None
    month, day, hour, minute, half = found
    hour = hour % 12 + (12 if half == "PM" else 0)
    for year in (now.astimezone(ct_stamp.CENTRAL).year, now.astimezone(ct_stamp.CENTRAL).year - 1):
        try:
            moment = dt.datetime(year, month, day, hour, minute, tzinfo=ct_stamp.CENTRAL)
        except ValueError:
            return None
        if moment <= now + dt.timedelta(minutes=5):
            return moment
    return None


def _api(path, token, opener=urllib.request.urlopen):
    request = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "life-os-v7", **({"Authorization": "Bearer " + token} if token else {})})
    with opener(request, timeout=30) as reply:
        return json.load(reply)


def evidence(environ=os.environ, api=_api):
    """-> {job: {"last_ok": aware datetime or None, "failed": bool}} from the newest `domains` runs' jobs, newest first: `last_ok` is when the job last finished with success; `failed` is true when its newest
    finished (not skipped) run did not succeed. {} when the history cannot be read (the rows then keep their last values and go stale by the two-hour rule)."""
    repo, token = (environ.get("GITHUB_REPOSITORY") or "").strip(), (environ.get("GITHUB_TOKEN") or "").strip()
    if not repo:
        return {}
    try:
        runs = api(f"/repos/{repo}/actions/workflows/domains.yml/runs?per_page={RUNS}&status=completed", token)["workflow_runs"]
        out = {}
        for run in runs:
            if run.get("conclusion") not in ("success", "failure"):
                continue
            for job in api(f"/repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100", token)["jobs"]:
                if job.get("conclusion") not in ("success", "failure") or not job.get("completed_at"):
                    continue
                seen = out.setdefault(job["name"], {"last_ok": None, "failed": job["conclusion"] == "failure"})
                if job["conclusion"] == "success" and seen["last_ok"] is None:
                    seen["last_ok"] = dt.datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00"))
        return out
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def plan(rows, seen, now):
    """-> [(page id, {"Health": option, "Last OK": text})] for each owned row whose job evidence changed it. Rows with no job, off by design and retired are left as they are."""
    updates = []
    for row in rows:
        job = JOBS.get(row["id"])
        found = seen.get(job) if job else None
        if not found:
            continue
        health = FAILING if found["failed"] else OK
        last = ct_stamp.when(found["last_ok"]) if found["last_ok"] else row["last_ok"]
        if health != row["health"] or last != row["last_ok"]:
            updates.append((row["page"], {"Health": health, "Last OK": last}))
    return updates


def apply(client, updates, retired_pages):
    """The only writes: each update's Health and Last OK on its own owned row, and the one-time archive of the retired Todoist row."""
    for page, values in updates:
        client.call("PATCH", f"/pages/{page}", {"properties": {"Health": {"select": {"name": values["Health"]}}, "Last OK": {"rich_text": [{"type": "text", "text": {"content": values["Last OK"]}}]}}})
    for page in retired_pages:
        client.call("PATCH", f"/pages/{page}", {"in_trash": True})


def merged(rows, updates):
    """The rows as they will read after `updates` (so a dry run and a live run summarise the same thing)."""
    changed = {page: values for page, values in updates}
    return [{**row, **{"health": changed[row["page"]]["Health"], "last_ok": changed[row["page"]]["Last OK"]}} if row["page"] in changed else row for row in rows]


def summary(rows, now):
    """-> {"total", "connected", "down": [names], "off", "stale_since"} over the owned rows that should work, or None when there are none. Retired rows are ignored; a should-work row is
    connected only when it is 🟢 ok with a Last OK no more than two hours old."""
    live = [r for r in rows if r["id"] not in RETIRED]
    should = [r for r in live if r["id"] not in OFF_BY_DESIGN]
    if not should:
        return None
    down, stale = [], None
    connected = 0
    for row in should:
        at = last_ok_time(row["last_ok"], now)
        fresh = at is not None and now - at <= dt.timedelta(hours=2)
        if row["health"] == OK and fresh:
            connected += 1
            continue
        name = NAMES.get(row["id"]) or row.get("name") or row["id"].split(":", 1)[-1]
        name = name if row.get("workspace") in (None, "", "All", "Personal") else f"{row['workspace']} {name}"
        if name not in down:
            down.append(name)
        if row["health"] == OK and at is not None:
            stale = at if stale is None or at < stale else stale          # a row that says ok but has not been refreshed is stale, not failing
    return {"total": len(should), "connected": connected, "down": down, "off": sum(r["id"] in OFF_BY_DESIGN for r in live), "stale_since": stale}


def retired_pages(rows):
    return [r["page"] for r in rows if r["id"] in RETIRED]
