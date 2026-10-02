"""Weekly sprint rollover (ported from V1's proven script). Read-only unless live; fails closed on ambiguity; never closes a
sprint before its end, never closes it before the carry-forward is verified, and verifies every step it takes.
Output is counts and fixed codes only: sprint names, issue keys and summaries are private."""
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform.jira import JiraError
from .boards import boards, owned

LOCAL_TZ = "America/Chicago"
UNFINISHED_FIELDS = "summary,status,issuetype"


def _when(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def resolve_current(active, future, now, tz):
    """(sprint, catchup). Exactly one active sprint, or - recovery - exactly one future sprint that ended within 48 hours."""
    if len(active) == 1:
        return active[0], False
    if len(active) > 1:
        raise JiraError("JIRA_ACTIVE_AMBIGUOUS")
    catchup = []
    for sprint in future:
        start, end = _when(sprint.get("startDate")), _when(sprint.get("endDate"))
        if start and end and start.astimezone(tz) <= now and end.astimezone(tz) <= now and now - end.astimezone(tz) <= timedelta(hours=48):
            catchup.append(sprint)
    if len(catchup) != 1:
        raise JiraError("JIRA_NO_ROLLOVER_CANDIDATE")
    return catchup[0], True


def target_week(sprint, tz):
    end = _when(sprint.get("endDate"))
    if not end:
        raise JiraError("JIRA_NO_END_DATE")
    end_local = end.astimezone(tz)
    start = datetime.combine(end_local.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    if start.weekday() != 0:
        raise JiraError("JIRA_NOT_MONDAY")
    return end_local, start, start + timedelta(days=6, hours=23, minutes=59, seconds=59)


def sprint_name(project, start, end):
    if start.year == end.year:
        return f"{project} Sprint — {start.strftime('%b %-d')}–{end.strftime('%b %-d, %Y')}"
    return f"{project} Sprint — {start.strftime('%b %-d, %Y')}–{end.strftime('%b %-d, %Y')}"


def _matches(sprint, start, end, tz):
    s, e = _when(sprint.get("startDate")), _when(sprint.get("endDate"))
    return bool(s and e and s.astimezone(tz).date() == start.date() and e.astimezone(tz).date() == end.date())


def _state(client, sprint_id, expected, code):
    if client.sprint(sprint_id).get("state") != expected:
        raise JiraError(code)


def plan(client, project, board_id, now, tz, keys=()):
    if str(client.board(board_id).get("type", "")).lower() != "scrum":
        raise JiraError("JIRA_BOARD_NOT_SCRUM")
    active = owned(client.sprints(board_id, "active"), project, keys)
    future = owned(client.sprints(board_id, "future"), project, keys)
    sprint, catchup = resolve_current(active, future, now, tz)
    sprint_id = int(sprint["id"])
    # every unfinished item in the sprint moves, whatever its project: cross-assigned work must not be stranded in a closed sprint
    unfinished = [i["key"] for i in client.sprint_issues(sprint_id, "statusCategory != Done", UNFINISHED_FIELDS) if i.get("key")]
    end_local, start, end = target_week(sprint, tz)
    matching = [s for s in future if int(s.get("id", -1)) != sprint_id and _matches(s, start, end, tz)]
    if len(matching) > 1:
        raise JiraError("JIRA_TARGET_AMBIGUOUS")
    return {"sprint_id": sprint_id, "catchup": catchup, "unfinished": unfinished, "end_local": end_local,
            "start": start, "end": end, "target_id": int(matching[0]["id"]) if matching else None,
            "name": sprint_name(project, start, end)}


def execute(client, project, board_id, p, now, sleep=time.sleep, keys=()):
    if now < p["end_local"]:
        raise JiraError("JIRA_EARLY_ROLLOVER")
    created = 0
    target_id = p["target_id"]
    if target_id is None:
        target_id = int(client.create_sprint(board_id, p["name"], p["start"], p["end"])["id"])
        created = 1
    _state(client, target_id, "future", "JIRA_TARGET_NOT_FUTURE")
    if p["catchup"]:
        client.set_sprint_state(p["sprint_id"], "active")
        _state(client, p["sprint_id"], "active", "JIRA_CATCHUP_FAILED")
    if p["unfinished"]:
        client.move_issues(target_id, p["unfinished"])
        have = {i["key"] for i in client.sprint_issues(target_id, "statusCategory != Done", "summary") if i.get("key")}
        if any(key not in have for key in p["unfinished"]):
            raise JiraError("JIRA_CARRY_VERIFY_FAILED")           # Jira's sprint reads can lag; nothing has been closed yet
    client.set_sprint_state(p["sprint_id"], "closed")
    _state(client, p["sprint_id"], "closed", "JIRA_CLOSE_VERIFY_FAILED")
    client.set_sprint_state(target_id, "active")
    _state(client, target_id, "active", "JIRA_START_VERIFY_FAILED")
    after = owned(client.sprints(board_id, "active"), project, keys)
    if len(after) != 1 or int(after[0].get("id", -1)) != target_id:
        raise JiraError("JIRA_FINAL_VERIFY_FAILED")
    return {"created": created, "carried": len(p["unfinished"])}


def run_project(client, project, board_id, live, now, tz, sleep, keys=()):
    for attempt in range(2):
        p = plan(client, project, board_id, now, tz, keys)
        if not live:
            return {"reuse": int(p["target_id"] is not None), "would_create": int(p["target_id"] is None),
                    "would_carry": len(p["unfinished"]), "catchup": int(p["catchup"])}
        try:
            return {**execute(client, project, board_id, p, now, sleep, keys), "closed": 1, "started": 1}
        except JiraError as error:
            if str(error) == "JIRA_CARRY_VERIFY_FAILED" and attempt == 0:
                sleep(15)                                         # known Jira consistency lag: one re-plan, then give up
                continue
            raise


def run(limit, live, environ=os.environ, client=None, now=None, sleep=time.sleep):
    from lifeos.platform.jira import Jira
    configured = boards(environ)
    client = client or Jira.from_env(environ)
    tz = ZoneInfo(LOCAL_TZ)
    now = now or datetime.now(tz)
    total = {"projects": len(configured), "ok": 0, "failed": 0, "reuse": 0, "would_create": 0, "would_carry": 0,
             "catchup": 0, "skipped_readonly": 0, "created": 0, "carried": 0, "closed": 0, "started": 0, "writes": 0, "why": {}}
    keys = [entry[0] for entry in configured]
    for position, (project, board_id, _, readonly) in enumerate(configured, 1):
        if readonly:
            total["skipped_readonly"] += 1
            total["ok"] += 1
            continue
        try:
            result = run_project(client, project, board_id, live, now, tz, sleep, keys)
        except JiraError as error:
            total["failed"] += 1
            code = (str(error) if str(error).startswith("JIRA_") else "JIRA_ERROR") + f"@{position}"   # board position, never its name
            total["why"][code] = total["why"].get(code, 0) + 1
            continue
        total["ok"] += 1
        for key, value in result.items():
            total[key] = total.get(key, 0) + value
        total["writes"] += result.get("created", 0) + result.get("closed", 0) + result.get("started", 0) if live else 0
    if total["failed"]:
        raise JiraError(f"JIRA_ROLLOVER_FAILED:{total['failed']}of{total['projects']}:" + ",".join(sorted(total["why"])))
    return total
