"""LIFE-918 (the unbuilt LIFE-915 parts): sprint inventory and ISO names, carry rule, recurrence, closure counts, daily reconciliation. OFF BY DEFAULT.

Gate: the Runtime Configuration switch MEJ_SPRINT_AUTOMATION (lifeos.accountability.switches), exactly "on"; off, `run` does nothing and returns {"enabled": 0}. JFM (board 5) and MEJ (board 38) are one unit: a board's
`readonly` flag in JIRA_BOARDS still skips it. Every Jira WRITE is an injected executor (create_sprint, rename_sprint, create_clone, sync_fields); this module never builds one, and a live run without them fails closed
with JIRA_RECONCILE_WRITER_UNVERIFIED (the tenant contract for sprint rename and issue create is UNVERIFIED; LIFE-918 proves it before any activation). Planning is pure and tested on synthetic data.
Never closes or starts a sprint (rollover owns the boundary); daily reconciliation repairs inventory, recurrence and drift only. Output: counts and fixed codes; keys, names and summaries stay private.

Owner decisions (Notion canon, LIFE-915 section, Nov 24 2026): 3 recurring Tasks are marked Done at the boundary with exactly one next-week clone; 4 at least four future week-long sprints; 6 names `PROJECT S{week} W{week} {year}`,
ISO week-year, Monday to Sunday America/Chicago; 7 carry: In Progress any priority, High/Highest To Do or Blocked, the rest to backlog, no roster; 8 existing recurrence configuration plus the `recurring` label, deduplicated;
10 manual, automatic and total closed kept apart; 11 removing the label stops the series; 12 edits reach future unfinished occurrences only; 13 rename in place, conflicts detected before any write; 14 inspect before retry."""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.accountability import switches
from lifeos.platform.jira import JiraError

from .boards import boards, owned

TZ = "America/Chicago"
MIN_FUTURE = 4
RECURRING = "recurring"
STANDARD = ("Task", "Story")
HIGH = ("Highest", "High")
DAILY_HOUR = 8
SPRINT_END = timedelta(days=6, hours=23, minutes=30)                      # the existing rollover's week: Monday 00:00 to Sunday 23:30
DST_TOLERANCE = timedelta(hours=2)
KEY = "reconcile"                                                         # the existing v7_jira_rollover state table, its own row (no new store)


def _when(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def iso_name(project, monday):
    """PROJECT S{week} W{week} {year} with the ISO week and ISO week-year of the Monday (week 53 and the year change follow ISO)."""
    year, week = monday.isocalendar()[:2]
    return f"{project} S{week} W{week} {year}"


def week_bounds(monday, tz):
    start = datetime.combine(monday, datetime.min.time(), tzinfo=tz)
    return start, datetime.combine(monday + timedelta(days=6), datetime.min.time(), tzinfo=tz) + timedelta(hours=23, minutes=30)


def monday_of(day):
    return day - timedelta(days=day.weekday())


def _span(sprint, tz):
    """The sprint's local Monday if it is exactly one Monday-to-Sunday week, else None."""
    start, end = _when(sprint.get("startDate")), _when(sprint.get("endDate"))
    if not start or not end:
        return None
    first, last = start.astimezone(tz).date(), end.astimezone(tz).date()
    if (last - first).days == 6 and first.weekday() == 0:
        return first
    if abs(start - week_bounds(monday_of((start + DST_TOLERANCE).astimezone(tz).date()), tz)[0]) <= DST_TOLERANCE and (end - start) < timedelta(days=8):
        return monday_of((start + DST_TOLERANCE).astimezone(tz).date())     # a legacy stamp off by the DST shift is still that week
    return None


def plan_sprints(project, active, future, now, tz, minimum=MIN_FUTURE):
    """-> {"rename": [(sprint_id, name)], "create": [(name, start, end)], "future": n} from the board's active and future sprints, or JiraError with a fixed code before any write.
    Conflicts (a sprint that is not one Monday-Sunday week, two sprints for one week, a future sprint off the weekly grid, two active sprints) stop the board. Closed sprints are never read here."""
    if len(active) > 1:
        raise JiraError("JIRA_ACTIVE_AMBIGUOUS")
    today = now.astimezone(tz).date()
    current = monday_of(today)
    if active:
        current = _span(active[0], tz)
        if current is None:
            raise JiraError("JIRA_RECONCILE_SPRINT_CONFLICT")
    by_week = {}
    for sprint in future:
        monday = _span(sprint, tz)
        if monday is None or monday <= current:
            raise JiraError("JIRA_RECONCILE_SPRINT_CONFLICT")
        if monday in by_week:
            raise JiraError("JIRA_RECONCILE_SPRINT_CONFLICT")
        by_week[monday] = sprint
    horizon = max([minimum] + [(m - current).days // 7 for m in by_week])
    rename, create = [], []
    if active and active[0].get("name") != iso_name(project, current):
        rename.append((int(active[0]["id"]), iso_name(project, current)))
    for offset in range(1, horizon + 1):
        monday = current + timedelta(days=7 * offset)
        if monday in by_week:
            if by_week[monday].get("name") != iso_name(project, monday):
                rename.append((int(by_week[monday]["id"]), iso_name(project, monday)))
        else:
            start, end = week_bounds(monday, tz)
            create.append((iso_name(project, monday), start, end))
    return {"rename": rename, "create": create, "future": horizon}


def _field(issue, name):
    return (issue.get("fields") or {}).get(name)


def _norm(issue):
    f = issue.get("fields") or {}
    return {"key": issue.get("key"), "type": (f.get("issuetype") or {}).get("name"), "priority": (f.get("priority") or {}).get("name"),
            "category": ((f.get("status") or {}).get("statusCategory") or {}).get("key"), "status": (f.get("status") or {}).get("name") or "",
            "labels": list(f.get("labels") or []), "parent": (f.get("parent") or {}).get("key")}


def carry_plan(issues, recurring=()):
    """-> {"carry": [...], "backlog": [...], "recurring": [...], "exceptions": [...]} for the unfinished issues of the ending sprint (decisions 1, 2, 7). Epics are excluded; Sub-tasks follow their parent; a recurring
    series is closed Done with its clone instead of carried (3); an unknown standard type is an exception, never a silent move."""
    rows = [_norm(i) for i in issues]
    top = {r["key"]: r for r in rows if r["type"] in STANDARD}
    out = {"carry": [], "backlog": [], "recurring": [], "exceptions": []}
    for r in rows:
        if r["category"] == "done" or r["type"] == "Epic":
            continue
        if r["type"] == "Sub-task":
            continue                                                        # decided below, with its parent
        if r["type"] not in STANDARD:
            out["exceptions"].append(r["key"])
            continue
        blocked = r["status"].strip().lower() == "blocked"
        progressing = r["category"] == "indeterminate" and not blocked
        if r["key"] in recurring:
            out["recurring"].append(r["key"])
        elif progressing or r["priority"] in HIGH:
            out["carry"].append(r["key"])
            r["_in"] = True
        else:
            out["backlog"].append(r["key"])
            r["_in"] = False
    for r in rows:                                                          # children follow their parent, decided after every parent is known
        if r["type"] == "Sub-task" and r["category"] != "done" and r["parent"] in top and "_in" in top[r["parent"]]:
            out["carry" if top[r["parent"]]["_in"] else "backlog"].append(r["key"])
        elif r["type"] == "Sub-task" and r["category"] != "done" and r["parent"] in recurring:
            out["recurring"].append(r["key"])
    return {k: sorted(set(v)) for k, v in out.items()}


def series(issues, configured=()):
    """-> {root key: {"keys": [...], "labelled": bool}} from the `recurring` label and the existing configuration, deduplicated by root (decision 8). An occurrence's root follows its `Cloners` link chain, never its
    title; a chain with two origins fails closed. A configured series whose latest occurrence has lost the label is stopped (11): it appears with labelled False and is never created or synchronized."""
    by_key = {i["key"]: i for i in issues}
    parent_of = {}
    for i in issues:
        for link in (_field(i, "issuelinks") or []):
            if ((link.get("type") or {}).get("name")) == "Cloners" and link.get("inwardIssue"):
                origin = link["inwardIssue"]["key"]
                if parent_of.get(i["key"], origin) != origin:
                    raise JiraError("JIRA_RECONCILE_IDENTITY_AMBIGUOUS")
                parent_of[i["key"]] = origin

    def root(key, seen=()):
        if key in seen:
            raise JiraError("JIRA_RECONCILE_IDENTITY_AMBIGUOUS")
        return root(parent_of[key], seen + (key,)) if key in parent_of else key
    out = {}
    wanted = {i["key"] for i in issues if RECURRING in (_field(i, "labels") or [])} | {k for k in configured if k in by_key}
    for key in sorted(wanted):
        entry = out.setdefault(root(key), {"keys": [], "labelled": False})
        entry["keys"].append(key)
    for r, entry in out.items():
        members = sorted(k for k in by_key if root(k) == r)
        entry["keys"] = members
        latest = members[-1]
        entry["labelled"] = RECURRING in (_field(by_key[latest], "labels") or [])
    return out


def clone_fields(issue, advance_days=7):
    """Q8: copy summary, description, assignee, priority, labels, parent Epic; status To Do, resolution cleared, description checkboxes unchecked, due date +7 days; no comments, attachments, worklogs or history."""
    f = issue.get("fields") or {}
    description = f.get("description")
    if isinstance(description, str):
        description = description.replace("[x]", "[ ]").replace("[X]", "[ ]")
    due = f.get("duedate")
    return {"summary": f.get("summary"), "description": description, "assignee": (f.get("assignee") or {}).get("accountId"), "priority": (f.get("priority") or {}).get("name"),
            "labels": list(f.get("labels") or []), "parent": (f.get("parent") or {}).get("key"), "status": "To Do", "resolution": None,
            "duedate": (date.fromisoformat(due) + timedelta(days=advance_days)).isoformat() if due else None, "subtasks": [clone_fields(s, advance_days) for s in (f.get("subtasks") or [])]}


def plan_recurrence(issues, found, next_sprint_id, configured=()):
    """-> {"create": [root key], "sync": [(future key, fields)], "stopped": n, "exceptions": [codes]}. Exactly one occurrence per series per next-week sprint: none means create from the latest occurrence, more than one is a
    duplicate and fails closed for that series (14: never a blind create). `found` is series() of the same issues; `issues` carry their sprint in the `sprint` field. Sync reaches unfinished future occurrences only."""
    by_key = {i["key"]: i for i in issues}
    out = {"create": [], "sync": [], "stopped": 0, "exceptions": []}
    for root_key, entry in sorted(found.items()):
        if not entry["labelled"]:
            out["stopped"] += 1
            continue
        members = [by_key[k] for k in entry["keys"]]
        in_next = [m for m in members if ((_field(m, "sprint") or {}).get("id")) == next_sprint_id]
        if len(in_next) > 1:
            out["exceptions"].append("JIRA_RECONCILE_DUPLICATE_OCCURRENCE")
            continue
        latest = max((m for m in members if ((_field(m, "sprint") or {}).get("id")) != next_sprint_id), key=lambda m: m["key"], default=None)
        if not in_next:
            out["create"].append(root_key)
            continue
        future = in_next[0]
        if latest is not None and _field(future, "status") and ((_field(future, "status") or {}).get("statusCategory") or {}).get("key") != "done":
            want = clone_fields(latest, 0)
            have = clone_fields(future, 0)
            drift = {k: want[k] for k in ("summary", "description", "assignee", "priority", "labels", "parent") if want[k] != have[k]}
            if drift:
                out["sync"].append((future["key"], drift))
    return out


def closure_counts(closures):
    """-> manual, automatic and total closed, kept apart (10). `closures` are (key, "manual"|"automatic"|None); an unverifiable provenance counts only toward total and is reported as unverified, never guessed."""
    manual = sum(1 for _, p in closures if p == "manual")
    auto = sum(1 for _, p in closures if p == "automatic")
    return {"manual": manual, "automatic": auto, "total": len(closures), "unverified": len(closures) - manual - auto}


def due(now, last_done, tz=None):
    """True on the first run at or after 8:00 AM America/Chicago on a Chicago date that has not reconciled yet (seven days a week, DST-aware); `now` must carry a time zone."""
    if now.tzinfo is None:
        raise JiraError("JIRA_RECONCILE_CLOCK_INVALID")
    local = now.astimezone(tz or ZoneInfo(TZ))
    return local.hour >= DAILY_HOUR and (last_done is None or last_done != local.date())


class Writers:
    """The injected Jira writes; every method is optional and absent means UNVERIFIED (fail closed). create_sprint(board_id, name, start, end), rename_sprint(sprint_id, name), create_clone(root_key, fields),
    sync_fields(key, fields)."""

    def __init__(self, **calls):
        self.__dict__.update(calls)

    def need(self, name):
        if not callable(getattr(self, name, None)):
            raise JiraError("JIRA_RECONCILE_WRITER_UNVERIFIED")
        return getattr(self, name)


def _inventory(client, board_id, project, keys):
    return owned(client.sprints(board_id, "active"), project, keys), owned(client.sprints(board_id, "future"), project, keys)


def reconcile_board(client, project, board_id, now, tz, writers, live, keys=(), sleep=None):
    """Plan the board's sprint inventory; live, write it through `writers`, read it back and re-plan once on a lag (14). -> counts."""
    for attempt in range(2):
        active, future = _inventory(client, board_id, project, keys)
        p = plan_sprints(project, active, future, now, tz)
        if not live:
            return {"would_rename": len(p["rename"]), "would_create": len(p["create"]), "future": p["future"]}
        if not p["rename"] and not p["create"]:
            return {"renamed": 0, "created": 0, "future": p["future"]}
        done = {"renamed": 0, "created": 0}
        for sprint_id, name in p["rename"]:
            writers.need("rename_sprint")(sprint_id, name)
            done["renamed"] += 1
        for name, start, end in p["create"]:
            writers.need("create_sprint")(board_id, name, start, end)
            done["created"] += 1
        check = plan_sprints(project, *_inventory(client, board_id, project, keys), now, tz)
        if not check["rename"] and not check["create"]:
            return {**done, "future": check["future"]}
        if attempt == 0 and sleep:
            sleep(15)                                                       # a known Jira consistency lag: one re-plan from actual state, never a blind repeat
            continue
        raise JiraError("JIRA_RECONCILE_VERIFY_FAILED")
    raise JiraError("JIRA_RECONCILE_VERIFY_FAILED")


def run(limit, live, environ=os.environ, client=None, now=None, writers=None, state=None, alert=None, sleep=None):
    """The daily reconciliation stage. Off (switch not "on"): {"enabled": 0}, nothing read. On: at most once per Chicago date at or after 8:00 AM CT, per board (JFM and MEJ as one unit: any board's failure fails
    the run and nothing is marked done). Dry run reads and plans only. Live needs injected `writers` and `state` (get() -> last date, set(date)); without them it fails closed. `alert(subject, details)` is the existing inbox send."""
    if not switches.on("MEJ_SPRINT_AUTOMATION", environ):
        return {"enabled": 0}
    tz = ZoneInfo(TZ)
    now = now or datetime.now(tz)
    last = state.get() if state else None
    if live and (state is None or writers is None):
        raise JiraError("JIRA_RECONCILE_WRITER_UNVERIFIED")
    if not due(now, last, tz):
        return {"enabled": 1, "not_due": 1}
    from lifeos.platform.jira import Jira
    configured = [b for b in boards(environ) if not b[3]]
    client = client or Jira.from_env(environ)
    keys = [b[0] for b in boards(environ)]
    total = {"enabled": 1, "boards": len(configured), "ok": 0, "failed": 0, "renamed": 0, "created": 0, "would_rename": 0, "would_create": 0, "why": {}}
    for position, (project, board_id, _, _) in enumerate(configured, 1):
        try:
            result = reconcile_board(client, project, board_id, now, tz, writers, live, keys, sleep)
        except JiraError as error:
            total["failed"] += 1
            code = (str(error) if str(error).startswith("JIRA_") else "JIRA_ERROR") + f"@{position}"
            total["why"][code] = total["why"].get(code, 0) + 1
            continue
        total["ok"] += 1
        for k in ("renamed", "created", "would_rename", "would_create"):
            total[k] += result.get(k, 0)
    if total["failed"]:
        if alert:
            alert("Jira daily reconciliation failed", f"{total['failed']} of {total['boards']} boards: " + ", ".join(sorted(total["why"])))
        raise JiraError(f"JIRA_RECONCILE_FAILED:{total['failed']}of{total['boards']}:" + ",".join(sorted(total["why"])))
    if live and state is not None:
        state.set(now.astimezone(tz).date())
    total["summary"] = f"Jira sprints: {total['ok']} boards in order, {total['created']} sprints added, {total['renamed']} renamed" if live else "Jira sprints: dry run"
    return total
