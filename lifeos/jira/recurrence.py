"""Recurring-series clones and closure counts of one board, shared by the daily reconcile and the rollover boundary.
Writes go through injected writers and fail closed when unverified."""

from datetime import timedelta

from lifeos.platform.jira import JiraError

from . import reconcile as rc
from .boards import owned

SERIES_FIELDS = "summary,description,assignee,priority,labels,parent,issuelinks,sprint,status,duedate,subtasks,issuetype"
CLOSURE_FIELDS = "labels,status,statuscategorychangedate,resolutiondate"
NO_SPRINT = -1


def jobs(issues, next_id, configured=()):
    """-> (plan, [(root key, latest key, clone fields)]). `next_id` None means the next sprint does not exist yet."""
    marker = NO_SPRINT if next_id is None else int(next_id)
    found = rc.series(issues, configured)
    plan = rc.plan_recurrence(issues, found, marker, configured)
    by_key = {i["key"]: i for i in issues}
    todo = []
    for root in plan["create"]:
        members = [by_key[k] for k in found[root]["keys"] if ((rc._field(by_key[k], "sprint") or {}).get("id")) != marker]
        latest = max(members, key=lambda m: rc.order(m["key"]))
        todo.append((root, latest["key"], rc.clone_fields(latest, 7)))
    return plan, todo


def _next_sprint(client, project, board_id, now, tz, keys):
    monday = rc.monday_of(now.astimezone(tz).date())
    future = owned(client.sprints(board_id, "future"), project, keys)
    for sprint in future:
        if rc._span(sprint, tz) == monday + timedelta(days=7):
            return int(sprint["id"])
    return None


def for_board(client, project, board_id, now, tz, writers, live, keys=(), sleep=None):
    """One occurrence per labelled series in next week's sprint; drift on unfinished future occurrences is
    synchronized. Live: write, read back, re-plan once; anything undone is JIRA_RECONCILE_VERIFY_FAILED. -> counts."""
    for attempt in range(2):
        target = _next_sprint(client, project, board_id, now, tz, keys)
        plan, todo = jobs(client.board_issues(board_id, "labels = recurring", SERIES_FIELDS), target)
        if plan["exceptions"]:
            raise JiraError(plan["exceptions"][0])
        if not live:
            return {"would_clone": len(todo), "would_sync": len(plan["sync"]), "series_stopped": plan["stopped"]}
        if not todo and not plan["sync"]:
            return {"cloned": 0, "synced": 0, "series_stopped": plan["stopped"]}
        if todo and target is None:
            raise JiraError("JIRA_RECONCILE_SPRINT_CONFLICT")
        done = {"cloned": 0, "synced": 0}
        for root, latest, fields in todo:
            writers.need("create_clone")(root, fields, target, latest)
            done["cloned"] += 1
        for key, drift in plan["sync"]:
            writers.need("sync_fields")(key, drift)
            done["synced"] += 1
        again, more = jobs(client.board_issues(board_id, "labels = recurring", SERIES_FIELDS), _next_sprint(client, project, board_id, now, tz, keys))
        if not more and not again["sync"]:
            return {**done, "series_stopped": plan["stopped"]}
        if attempt == 0 and sleep:
            sleep(15)
            continue
        raise JiraError("JIRA_RECONCILE_VERIFY_FAILED")
    raise JiraError("JIRA_RECONCILE_VERIFY_FAILED")


def closures(client, board_id, since):
    """-> this week's closure counts: closures_total, plus closures_manual and closures_automatic once `since` is set.
    """
    issues = client.board_issues(board_id, "statusCategory = Done AND statusCategoryChangedDate >= startOfWeek()", CLOSURE_FIELDS)
    counts = rc.closure_counts(issues, since)
    out = {"closures_total": counts["total"]}
    if counts["split"]:
        out.update({"closures_manual": counts["manual"], "closures_automatic": counts["automatic"]})
    return out


def extra(environ):
    since = rc.since(environ)

    def hook(client, project, board_id, now, tz, writers, live, keys, sleep):
        out = for_board(client, project, board_id, now, tz, writers, live, keys, sleep)
        out.update(closures(client, board_id, since))
        return out
    return hook


def summary(total, live):
    done = "added" if live else "would add"
    line = f"Jira sprints: {total.get('ok', 0)} of {total.get('boards', 0)} boards in order, {total.get('created' if live else 'would_create', 0)} sprints {done}, {total.get('renamed' if live else 'would_rename', 0)} {'renamed' if live else 'would rename'}"
    line += f", {total.get('cloned' if live else 'would_clone', 0)} recurring {'cloned' if live else 'to clone'}"
    counts = total.get("future_by_board") or {}
    if counts:
        target = total.get("future_target", 8)
        line += " · future sprints " + ", ".join(f"{name} {count}" + (f" (over {target})" if count > target else "") for name, count in sorted(counts.items()))
    if "closures_total" in total:
        line += f" · closed this week {total['closures_total']}"
        if "closures_automatic" in total:
            line += f" ({total['closures_automatic']} automatic, {total.get('closures_manual', 0)} manual)"
    return line
