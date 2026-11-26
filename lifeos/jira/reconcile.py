"""Sprint inventory, ISO names, carry rule, recurrence, closure counts and daily reconciliation. OFF BY DEFAULT.

Gated by MEJ_SPRINT_AUTOMATION (exactly "on"). Every Jira write is an injected executor; a live run without them fails
closed with JIRA_RECONCILE_WRITER_UNVERIFIED. Planning is pure; never closes or starts a sprint (rollover does).
Counts and fixed codes only.
"""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform.jira import JiraError

from .boards import boards, owned

TZ = "America/Chicago"
MIN_FUTURE = 8
RECURRING = "recurring"
STANDARD = ("Task", "Story")
HIGH = ("Highest", "High")
DAILY_HOUR = 8
SPRINT_END = timedelta(days=6, hours=23, minutes=30)
DST_TOLERANCE = timedelta(hours=2)
KEY = "reconcile"


def _when(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def iso_name(project, monday):
    year, week = monday.isocalendar()[:2]
    return f"{project} W{week} {year}"


def week_bounds(monday, tz):
    start = datetime.combine(monday, datetime.min.time(), tzinfo=tz)
    return start, datetime.combine(monday + timedelta(days=6), datetime.min.time(), tzinfo=tz) + timedelta(hours=23, minutes=30)


def monday_of(day):
    return day - timedelta(days=day.weekday())


def _span(sprint, tz):
    start, end = _when(sprint.get("startDate")), _when(sprint.get("endDate"))
    if not start or not end:
        return None
    first, last = start.astimezone(tz).date(), end.astimezone(tz).date()
    if (last - first).days == 6 and first.weekday() == 0:
        return first
    if abs(start - week_bounds(monday_of((start + DST_TOLERANCE).astimezone(tz).date()), tz)[0]) <= DST_TOLERANCE and (end - start) < timedelta(days=8):
        return monday_of((start + DST_TOLERANCE).astimezone(tz).date())
    return None


def plan_sprints(project, active, future, now, tz, minimum=MIN_FUTURE):
    """-> {"rename", "create", "future"}, or JiraError with a fixed code before any write. A non-weekly sprint,
    duplicate week, off-grid future sprint or two active sprints stop the board."""
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
    horizon = minimum
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


def order(key):
    head, _, tail = str(key).rpartition("-")
    return (head, int(tail) if tail.isdigit() else -1, str(key))


def _field(issue, name):
    return (issue.get("fields") or {}).get(name)


def _norm(issue):
    f = issue.get("fields") or {}
    return {"key": issue.get("key"), "type": (f.get("issuetype") or {}).get("name"), "priority": (f.get("priority") or {}).get("name"),
            "category": ((f.get("status") or {}).get("statusCategory") or {}).get("key"), "status": (f.get("status") or {}).get("name") or "",
            "labels": list(f.get("labels") or []), "parent": (f.get("parent") or {}).get("key")}


AUTO_LABEL = "auto-closed"


def carry_plan(issues, recurring=()):
    """Carry rule: In Progress carries at any priority; High/Highest carries when To Do or Blocked; the rest go to the
    backlog. -> {"carry", "backlog", "recurring", "exceptions"}."""
    rows = [_norm(i) for i in issues]
    top = {r["key"]: r for r in rows if r["type"] in STANDARD}
    out = {"carry": [], "backlog": [], "recurring": [], "exceptions": []}
    for r in rows:
        if r["category"] == "done" or r["type"] == "Epic":
            continue
        if r["type"] == "Sub-task":
            continue
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
    for r in rows:
        if r["type"] == "Sub-task" and r["category"] != "done" and r["parent"] in top and "_in" in top[r["parent"]]:
            out["carry" if top[r["parent"]]["_in"] else "backlog"].append(r["key"])
        elif r["type"] == "Sub-task" and r["category"] != "done" and r["parent"] in recurring:
            out["recurring"].append(r["key"])
    return {k: sorted(set(v)) for k, v in out.items()}


def series(issues, configured=()):
    """-> {root key: {"keys", "labelled"}} from the `recurring` label and configuration. A root follows the `Cloners`
    chain; two origins fail closed; a series that lost its label is stopped."""
    by_key = {i["key"]: i for i in issues}
    parent_of = {}
    for i in issues:
        for link in (_field(i, "issuelinks") or []):
            if ((link.get("type") or {}).get("name")) == "Cloners" and link.get("outwardIssue"):
                origin = link["outwardIssue"]["key"]
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
        members = sorted((k for k in by_key if root(k) == r), key=order)
        entry["keys"] = members
        latest = members[-1]
        entry["labelled"] = RECURRING in (_field(by_key[latest], "labels") or [])
    return out


def clone_fields(issue, advance_days=7):
    """Fields copied to a clone: summary, description, assignee, priority, labels, parent Epic; status To Do, due date
    +7 days, no comments or history."""
    f = issue.get("fields") or {}
    description = f.get("description")
    if isinstance(description, str):
        description = description.replace("[x]", "[ ]").replace("[X]", "[ ]")
    due = f.get("duedate")
    return {"summary": f.get("summary"), "description": description, "assignee": (f.get("assignee") or {}).get("accountId"), "priority": (f.get("priority") or {}).get("name"),
            "labels": list(f.get("labels") or []), "parent": (f.get("parent") or {}).get("key"), "status": "To Do", "resolution": None,
            "duedate": (date.fromisoformat(due) + timedelta(days=advance_days)).isoformat() if due else None, "subtasks": [clone_fields(s, advance_days) for s in (f.get("subtasks") or [])]}


def plan_recurrence(issues, found, next_sprint_id, configured=()):
    """-> {"create": [root key], "sync": [(key, fields)], "stopped": n, "exceptions": [codes]}. One occurrence per
    series per next-week sprint; a duplicate fails closed."""
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
        latest = max((m for m in members if ((_field(m, "sprint") or {}).get("id")) != next_sprint_id), key=lambda m: order(m["key"]), default=None)
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


def auto_close_fields(run_id):
    run = str(run_id or "").strip()
    if not run or "\n" in run:
        raise JiraError("JIRA_RECONCILE_IDENTITY_AMBIGUOUS")
    return {"label": AUTO_LABEL, "comment": f"Closed automatically by LIFE OS run {run}."}


def _closed_at(issue):
    f = issue.get("fields") or {}
    return _when(f.get("statuscategorychangedate") or f.get("resolutiondate"))


def closure_counts(issues, since=None):
    """-> manual, automatic and total closed counts. The split starts at `since`; earlier closures count only in the
    total, and without `since` nothing is classified."""
    done = [i for i in issues if _norm(i)["category"] == "done"]
    if since is None:
        return {"manual": None, "automatic": None, "total": len(done), "split": False}
    later = [i for i in done if (_closed_at(i) is not None and _closed_at(i) >= since)]
    auto = sum(1 for i in later if AUTO_LABEL in (_field(i, "labels") or []))
    return {"manual": len(later) - auto, "automatic": auto, "total": len(done), "split": True}


def since(environ=None):
    try:
        value = str((os.environ if environ is None else environ).get("MEJ_AUTOMATION_SINCE") or "").strip()
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except (AttributeError, TypeError, ValueError):
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def writers_verified(environ=None):
    try:
        return str((os.environ if environ is None else environ).get("JIRA_WRITERS_VERIFIED") or "").strip().lower() == "yes"
    except (AttributeError, TypeError):
        return False


def due(now, last_done, tz=None):
    """True on the first run at or after 8:00 AM America/Chicago on a not-yet-reconciled Chicago date."""
    if now.tzinfo is None:
        raise JiraError("JIRA_RECONCILE_CLOCK_INVALID")
    local = now.astimezone(tz or ZoneInfo(TZ))
    return local.hour >= DAILY_HOUR and (last_done is None or last_done != local.date())


def enabled(environ=None):
    """MEJ_SPRINT_AUTOMATION: on only for exactly "on"."""
    try:
        return str((os.environ if environ is None else environ).get("MEJ_SPRINT_AUTOMATION") or "").strip().lower() == "on"
    except (AttributeError, TypeError):
        return False


def future_target(environ=None):
    source = os.environ if environ is None else environ
    raw = str(source.get("JIRA_FUTURE_SPRINTS") or MIN_FUTURE).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise JiraError("JIRA_RECONCILE_CONFIG_INVALID") from None
    if not 1 <= value <= 52:
        raise JiraError("JIRA_RECONCILE_CONFIG_INVALID")
    return value


class Writers:
    """Injected Jira writes, each optional (absent means unverified, fail closed)."""

    def __init__(self, **calls):
        self.__dict__.update(calls)

    def need(self, name):
        if not callable(getattr(self, name, None)):
            raise JiraError("JIRA_RECONCILE_WRITER_UNVERIFIED")
        return getattr(self, name)


def _inventory(client, board_id, project, keys):
    return owned(client.sprints(board_id, "active"), project, keys), owned(client.sprints(board_id, "future"), project, keys)


def reconcile_board(client, project, board_id, now, tz, writers, live, keys=(), sleep=None, minimum=MIN_FUTURE):
    for attempt in range(2):
        active, future = _inventory(client, board_id, project, keys)
        p = plan_sprints(project, active, future, now, tz, minimum)
        if not live:
            return {"would_rename": len(p["rename"]), "would_create": len(p["create"]), "future": len(future) + len(p["create"])}
        if not p["rename"] and not p["create"]:
            return {"renamed": 0, "created": 0, "future": len(future)}
        done = {"renamed": 0, "created": 0}
        for sprint_id, name in p["rename"]:
            writers.need("rename_sprint")(sprint_id, name)
            done["renamed"] += 1
        for name, start, end in p["create"]:
            writers.need("create_sprint")(board_id, name, start, end)
            done["created"] += 1
        active_after, future_after = _inventory(client, board_id, project, keys)
        check = plan_sprints(project, active_after, future_after, now, tz, minimum)
        if not check["rename"] and not check["create"]:
            return {**done, "future": len(future_after)}
        if attempt == 0 and sleep:
            sleep(15)
            continue
        raise JiraError("JIRA_RECONCILE_VERIFY_FAILED")
    raise JiraError("JIRA_RECONCILE_VERIFY_FAILED")


def run(limit, live, environ=os.environ, client=None, now=None, writers=None, state=None, alert=None, sleep=None, extra=None, force=False):
    """The daily reconciliation stage. Off: {"enabled": 0}, nothing read. On: at most once per Chicago date after 8:00
    AM CT, JFM and MEJ as one unit. Live needs injected `writers` and `state` (get() -> last date, set(date)) or fails
    closed."""
    if not enabled(environ):
        return {"enabled": 0}
    minimum = future_target(environ)
    tz = ZoneInfo(TZ)
    now = now or datetime.now(tz)
    last = state.get() if state else None
    if live and (state is None or writers is None):
        raise JiraError("JIRA_RECONCILE_WRITER_UNVERIFIED")
    local = now.astimezone(tz)
    if (force and local.hour < DAILY_HOUR) or (not force and not due(now, last, tz)):
        return {"enabled": 1, "not_due": 1}
    from lifeos.platform.jira import Jira
    configured = [b for b in boards(environ) if not b[3]]
    client = client or Jira.from_env(environ)
    keys = [b[0] for b in boards(environ)]
    total = {"enabled": 1, "boards": len(configured), "ok": 0, "failed": 0, "renamed": 0, "created": 0, "would_rename": 0, "would_create": 0, "why": {}}
    future_by_board = {}
    for position, (project, board_id, _, _) in enumerate(configured, 1):
        try:
            result = reconcile_board(client, project, board_id, now, tz, writers, live, keys, sleep, minimum)
            if extra is not None:
                result = {**result, **extra(client, project, board_id, now, tz, writers, live, keys, sleep)}
        except JiraError as error:
            total["failed"] += 1
            code = (str(error) if str(error).startswith("JIRA_") else "JIRA_ERROR") + f"@{position}"
            total["why"][code] = total["why"].get(code, 0) + 1
            continue
        total["ok"] += 1
        future_by_board[project] = result.get("future", 0)
        for k in ("renamed", "created", "would_rename", "would_create"):
            total[k] += result.get(k, 0)
        for k, v in result.items():
            if k not in ("renamed", "created", "would_rename", "would_create", "future") and isinstance(v, int) and not isinstance(v, bool):
                total[k] = total.get(k, 0) + v
    if total["failed"]:
        if alert:
            alert("Jira daily reconciliation failed", f"{total['failed']} of {total['boards']} boards: " + ", ".join(sorted(total["why"])))
        raise JiraError(f"JIRA_RECONCILE_FAILED:{total['failed']}of{total['boards']}:" + ",".join(sorted(total["why"])))
    if live and state is not None and not force:
        state.set(now.astimezone(tz).date())
    total["future_by_board"] = future_by_board
    total["future_over_target"] = sorted(project for project, count in future_by_board.items() if count > minimum)
    total["future_target"] = minimum
    total["summary"] = f"Jira sprints: {total['ok']} boards in order, {total['created']} sprints added, {total['renamed']} renamed" if live else "Jira sprints: dry run"
    return total
