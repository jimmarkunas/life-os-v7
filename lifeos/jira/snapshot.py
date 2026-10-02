"""Read-only Jira execution snapshot per board, kept in the private database (never in this public repo) for the report.
Output is counts only: ticket keys, summaries and sprint names are private."""
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.platform.jira import JiraError
from .boards import boards
from . import store

LOCAL_TZ = "America/Chicago"
FIELDS = "summary,status,issuetype,priority,duedate,parent"
SCHEMA_V = 1


def compact(issue):
    fields = issue.get("fields") or {}
    status = fields.get("status") or {}
    return {"key": issue.get("key"), "summary": fields.get("summary") or "", "status": status.get("name") or "Unknown",
            "category": (status.get("statusCategory") or {}).get("key") or "",
            "type": (fields.get("issuetype") or {}).get("name") or "Unknown",
            "priority": (fields.get("priority") or {}).get("name") or "Unspecified", "due": fields.get("duedate"),
            "parent": (fields.get("parent") or {}).get("key")}


def _summary(sprint):
    return None if not sprint else {"id": sprint.get("id"), "name": sprint.get("name"), "state": sprint.get("state"),
                                    "start": sprint.get("startDate"), "end": sprint.get("endDate")}


def _by_start(sprints):
    return sorted(sprints, key=lambda s: (s.get("startDate") or "9999", int(s.get("id") or 0)))


def project_snapshot(client, project, board_id, triage_all, now):
    board = client.board(board_id)
    if str(board.get("type", "")).lower() != "scrum":
        raise JiraError("JIRA_BOARD_NOT_SCRUM")
    active, future = _by_start(client.sprints(board_id, "active")), _by_start(client.sprints(board_id, "future"))
    if len(active) > 1:
        raise JiraError("JIRA_ACTIVE_AMBIGUOUS")
    current = active[0] if active else (future[0] if future else None)
    following = (future[0] if future else None) if active else (future[1] if len(future) > 1 else None)
    today = now.date().isoformat()

    def issues(jql):
        return [compact(i) for i in client.board_issues(board_id, jql + " ORDER BY Rank ASC", FIELDS)]

    def tasks(sprint):
        if not sprint or sprint.get("id") is None:
            return []
        return issues(f"project = {project} AND sprint = {sprint['id']} AND issuetype = Task AND statusCategory != Done")

    triage_type = "issuetype != Epic" if triage_all else "issuetype = Task"
    done = issues(f"project = {project} AND issuetype != Epic AND sprint in openSprints() AND statusCategory = Done") if active else []
    return {"schema": SCHEMA_V, "project": project, "taken_at": now.isoformat(), "timezone": LOCAL_TZ,
            "board": {"id": board_id, "name": board.get("name")},
            "current_sprint": _summary(current), "next_sprint": _summary(following),
            "current_tasks": tasks(current), "next_tasks": tasks(following),
            "overdue": issues(f'project = {project} AND issuetype != Epic AND statusCategory != Done AND duedate < "{today}"'),
            "blocked": issues(f"project = {project} AND issuetype != Epic AND status = blocked"),
            "triage": issues(f"project = {project} AND sprint is EMPTY AND {triage_type} AND statusCategory != Done"),
            "done": done}


def run(limit, live, environ=os.environ, client=None, now=None, connect=None):
    from lifeos.platform.jira import Jira
    configured = boards(environ)
    client = client or Jira.from_env(environ)
    now = now or datetime.now(ZoneInfo(LOCAL_TZ))
    total = {"projects": len(configured), "ok": 0, "failed": 0, "saved": 0, "current": 0, "next": 0, "overdue": 0,
             "blocked": 0, "triage": 0, "done": 0, "why": {}}
    built = []
    for position, (project, board_id, triage_all) in enumerate(configured, 1):
        try:
            snap = project_snapshot(client, project, board_id, triage_all, now)
        except JiraError as error:
            total["failed"] += 1
            code = (str(error) if str(error).startswith("JIRA_") else "JIRA_ERROR") + f"@{position}"   # board position, never its name
            total["why"][code] = total["why"].get(code, 0) + 1
            continue
        total["ok"] += 1
        built.append(snap)
        for key, field in (("current", "current_tasks"), ("next", "next_tasks"), ("overdue", "overdue"),
                           ("blocked", "blocked"), ("triage", "triage"), ("done", "done")):
            total[key] += len(snap[field])
    if live and built:
        with (connect or __import__("lifeos.platform.db", fromlist=["connect"]).connect)() as connection:
            store.ensure_schema(connection)
            for snap in built:
                store.save(connection, snap)
                total["saved"] += 1
    if total["failed"]:
        raise JiraError(f"JIRA_SNAPSHOT_FAILED:{total['failed']}of{total['projects']}:" + ",".join(sorted(total["why"])))
    return total
