"""Scheduled 7/14-day Jira sprint rollover: read-only unless live, fails closed on ambiguity, verifies every step.

Never closes a sprint before its end or before the carry-forward is verified. Output: counts and fixed codes only
(names, keys and summaries are private).
"""
import os
import time
from datetime import datetime, time as clock, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform.jira import JiraError
from . import reconcile, recurrence
from .boards import boards, owned

LOCAL_TZ = "America/Chicago"
UNFINISHED_FIELDS = "summary,status,issuetype,parent"
AUTO_AFTER_HOUR = 0


def _when(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def resolve_current(active, future, now, tz):
    """(sprint, catchup): the one active sprint, or, for recovery, the one future sprint that ended within 48 hours.
    """
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


def sprint_days(sprint, tz):
    start, end = _when(sprint.get("startDate")), _when(sprint.get("endDate"))
    if not start or not end:
        raise JiraError("JIRA_NO_END_DATE")
    first, last = start.astimezone(tz).date(), end.astimezone(tz).date()
    days = (last - first).days + 1
    if days not in (7, 14) or first.weekday() != 0 or last.weekday() != 6:
        raise JiraError("JIRA_UNSUPPORTED_CADENCE")
    return days


def target_week(sprint, tz):
    days = sprint_days(sprint, tz)
    end_local = _when(sprint["endDate"]).astimezone(tz)
    start = datetime.combine(end_local.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    if start.weekday() != 0:
        raise JiraError("JIRA_NOT_MONDAY")
    return end_local, start, start + timedelta(days=days - 1, hours=23, minutes=30)


def sprint_name(project, start, end):
    if start.year == end.year:
        return f"{project} Sprint — {start.strftime('%b %-d')}–{end.strftime('%b %-d, %Y')}"
    return f"{project} Sprint — {start.strftime('%b %-d, %Y')}–{end.strftime('%b %-d, %Y')}"


DST_TOLERANCE = timedelta(hours=2)


def _matches(sprint, start, end, tz):
    """True when the sprint is the target week, compared as instants within the DST shift."""
    s, e = _when(sprint.get("startDate")), _when(sprint.get("endDate"))
    return bool(s and e and abs(s - start) <= DST_TOLERANCE and abs(e - end) <= DST_TOLERANCE)


def _state(client, sprint_id, expected, code):
    if client.sprint(sprint_id).get("state") != expected:
        raise JiraError(code)


def split_unfinished(issues):
    """-> (keys to carry, keys stranded). A sub-task moves with its parent; one whose parent is Done is stranded (left
    out)."""
    present = {i["key"] for i in issues if i.get("key")}
    carry, stranded = [], []
    for issue in issues:
        key = issue.get("key")
        if not key:
            continue
        fields = issue.get("fields") or {}
        subtask = bool((fields.get("issuetype") or {}).get("subtask"))
        (stranded if subtask and (fields.get("parent") or {}).get("key") not in present else carry).append(key)
    return carry, stranded


def preview_mej_selective_carry(issues, approved_keys, finite_keys=(), backlog_verified=False, already_carried=()):
    """Pure preflight for an approved Task roster (not wired into run/execute); never calls Jira."""
    if not isinstance(issues, (list, tuple)) or not isinstance(approved_keys, (list, tuple, set, frozenset)) or not isinstance(finite_keys, (list, tuple, set, frozenset)):
        raise JiraError("JIRA_MEJ_ROSTER_INVALID")
    keys = list(approved_keys)
    if len(keys) != len(set(keys)) or any(not isinstance(k, str) or not k.startswith("MEJ-") or not k[4:].isdigit() for k in keys):
        raise JiraError("JIRA_MEJ_ROSTER_INVALID")
    protected = set(finite_keys)
    if any(not isinstance(k, str) for k in protected) or set(keys) & protected:
        raise JiraError("JIRA_MEJ_FINITE_NOT_CARRY")
    found = {}
    for issue in issues:
        if not isinstance(issue, dict) or not isinstance(issue.get("key"), str) or issue["key"] in found:
            raise JiraError("JIRA_MEJ_INVENTORY_INVALID")
        fields = issue.get("fields") or {}
        typ = fields.get("issuetype") or {}
        name = typ.get("name")
        if not isinstance(name, str) or not name:
            raise JiraError("JIRA_MEJ_INVENTORY_INVALID")
        found[issue["key"]] = (name, bool(typ.get("subtask")), (fields.get("parent") or {}).get("key"))
    done_before = set(already_carried) & set(keys) if backlog_verified else set()
    if any(key not in found and key not in done_before for key in keys):
        raise JiraError("JIRA_MEJ_ROSTER_INVALID")
    for key in keys:
        if key in done_before and key not in found:
            continue
        name, subtask, _ = found[key]
        if subtask or name not in ("Task", "Story"):
            raise JiraError("JIRA_MEJ_ROSTER_INVALID")
    # Without a verified backlog readback, unfinished standard work outside the roster must block close.
    for key, (name, subtask, _) in found.items():
        if not subtask and key not in keys and not backlog_verified:
            raise JiraError("JIRA_MEJ_BACKLOG_UNVERIFIED")
    stranded, backlog = [], [key for key, (_, subtask, _) in found.items() if not subtask and key not in keys]
    carry = [key for key in keys if key in found]
    for key, (_, subtask, parent) in found.items():
        if not subtask:
            continue
        if backlog_verified and not parent:
            raise JiraError("JIRA_MEJ_INVENTORY_INVALID")
        if parent in keys:
            carry.append(key)
        elif backlog_verified and parent in backlog:
            backlog.append(key)
        else:
            stranded.append(key)
    if backlog_verified:
        return carry, stranded, backlog
    return carry, stranded


def select_carry(project, issues, recurring=(), roster=None):
    if roster is None or project != "MEJ":
        return reconcile.carry_plan(issues, recurring)
    base = reconcile.carry_plan(issues, recurring)
    skip = set(base["recurring"]) | set(base["exceptions"])
    kept = [i for i in issues if i.get("key") not in skip]
    carry, stranded, backlog = preview_mej_selective_carry(kept, roster["roster"], roster.get("finite", ()), backlog_verified=True)
    return {"carry": sorted(set(carry)), "backlog": sorted(set(backlog) | set(stranded)), "recurring": base["recurring"], "exceptions": base["exceptions"]}


MEJ_PROJECT = "MEJ"
MAX_ROSTER = 50


def selective_config(config):
    """Validated roster config: roster, finite, consent and a `backlog` object (move_to_backlog, backlog_issues) or
    None."""
    if not isinstance(config, dict):
        raise JiraError("JIRA_MEJ_ROSTER_INVALID")
    roster, finite, consent = config.get("roster"), config.get("finite", ()), config.get("consent")
    if not isinstance(roster, (list, tuple)) or len(roster) > MAX_ROSTER or not isinstance(finite, (list, tuple)):
        raise JiraError("JIRA_MEJ_ROSTER_INVALID")
    if not isinstance(consent, str) or not consent.strip():
        raise JiraError("JIRA_MEJ_ROSTER_INVALID")
    return {"roster": list(roster), "finite": list(finite), "consent": consent.strip(), "backlog": config.get("backlog")}


def _members(client, sprint_id):
    return {i["key"] for i in client.sprint_issues(sprint_id, "statusCategory != Done", "summary") if i.get("key")}


def _backlog_keys(backlog, board_id):
    try:
        return {i["key"] for i in backlog.backlog_issues(board_id, "statusCategory != Done", "summary") if i.get("key")}
    except Exception as error:  # noqa: BLE001
        raise JiraError("JIRA_MEJ_BACKLOG_FAILED") from error


def _orphans(raw):
    """Stranded only when the parent is verifiably Done (unknown is not Done); otherwise closing would split the pair.
    """
    present = {i["key"] for i in raw if i.get("key")}
    for issue in raw:
        fields = issue.get("fields") or {}
        parent = fields.get("parent") or {}
        if (fields.get("issuetype") or {}).get("subtask") and parent.get("key") and parent["key"] not in present:
            status = ((parent.get("fields") or {}).get("status") or {})
            if status.get("name") != "Done" and (status.get("statusCategory") or {}).get("key") != "done":
                raise JiraError("JIRA_MEJ_DESTINATION_MISMATCH")


def _selective_partition(client, sprint_id, raw, target_id, selective):
    """Partition unfinished work for the roster path into carry, backlog and stranded. Never writes."""
    if client.sprint(sprint_id).get("state") not in ("active", "future"):
        raise JiraError("JIRA_MEJ_SPRINT_STALE")
    for issue in raw:
        current = ((issue.get("fields") or {}).get("sprint") or {}).get("id")
        if current is not None and int(current) != sprint_id:
            raise JiraError("JIRA_MEJ_SPRINT_STALE")
    _orphans(raw)
    backlog = selective["backlog"]
    already = _members(client, target_id) & set(selective["roster"]) if target_id else set()
    result = preview_mej_selective_carry(raw, selective["roster"], selective["finite"], backlog_verified=backlog is not None, already_carried=already)
    carry, stranded, to_backlog = result if backlog is not None else (*result, [])
    finite = set(selective["finite"])
    return {"unfinished": [k for k in carry if k not in already], "already": sorted(already), "stranded": len(stranded), "backlog": to_backlog, "selective": selective,
            "finite_missed": len([k for k in to_backlog if k in finite])}


def _place_in_backlog(client, board_id, p, target_id):
    selective, keys = p["selective"], p["backlog"]
    backlog = selective["backlog"]
    try:
        backlog.move_to_backlog(keys)
    except Exception as error:  # noqa: BLE001
        raise JiraError("JIRA_MEJ_BACKLOG_FAILED") from error
    held = _backlog_keys(backlog, board_id)
    if any(key not in held for key in keys):
        raise JiraError("JIRA_MEJ_BACKLOG_PARTIAL")
    carried = _members(client, target_id)
    if any(key in held for key in p["unfinished"] + p["already"]) or any(key in carried for key in keys):
        raise JiraError("JIRA_MEJ_DESTINATION_MISMATCH")


def _closable(client, sprint_id, p):
    raw = client.sprint_issues(sprint_id, "statusCategory != Done", UNFINISHED_FIELDS)
    _orphans(raw)
    _, stranded = split_unfinished(raw)
    left = {i["key"] for i in client.sprint_issues(sprint_id, "statusCategory != Done", "summary") if i.get("key")}
    if left != set(stranded):
        raise JiraError("JIRA_MEJ_INVENTORY_INVALID")


def _automation_partition(client, project, sprint_id, raw, stranded, target_id, automation):
    """Decide carry, backlog and recurring clones before any write; any ambiguity stops with a fixed code."""
    if client.sprint(sprint_id).get("state") not in ("active", "future"):
        raise JiraError("JIRA_MEJ_SPRINT_STALE")
    for issue in raw:
        current = ((issue.get("fields") or {}).get("sprint") or {}).get("id")
        if current is not None and int(current) != sprint_id:
            raise JiraError("JIRA_MEJ_SPRINT_STALE")
    _orphans(raw)
    series_issues = client.sprint_issues(sprint_id, "labels = recurring", recurrence.SERIES_FIELDS)
    if target_id:
        series_issues += client.sprint_issues(target_id, "labels = recurring", recurrence.SERIES_FIELDS)
    found = reconcile.series(series_issues)
    recurring = {k for entry in found.values() if entry["labelled"] for k in entry["keys"]}
    rule = select_carry(project, [i for i in raw if i.get("key") not in set(stranded)], recurring, automation.get("roster"))
    if rule["exceptions"]:
        raise JiraError("JIRA_ROLLOVER_UNKNOWN_TYPE")
    again, clones = recurrence.jobs(series_issues, target_id)
    if again["exceptions"]:
        raise JiraError(again["exceptions"][0])
    already = _members(client, target_id) & set(rule["carry"]) if target_id else set()
    writers = automation["writers"]
    held = type("Backlog", (), {"move_to_backlog": staticmethod(lambda keys: writers.need("move_to_backlog")(keys)), "backlog_issues": staticmethod(client.backlog_issues)})
    return {"unfinished": [k for k in rule["carry"] if k not in already], "already": sorted(already), "backlog": rule["backlog"], "selective": {"backlog": held, "roster": []},
            "close_recurring": rule["recurring"], "clones": clones, "finite_missed": 0}


def _execute_automation(client, project, board_id, p, now, sleep, keys, automation):
    """Boundary with automation on: like `execute`, plus one clone per recurring series and the recurring originals
    closed Done, all before the old sprint closes."""
    if now < p["end_local"]:
        raise JiraError("JIRA_EARLY_ROLLOVER")
    writers = automation["writers"]
    for name in ("create_sprint", "move_to_backlog", "create_clone", "close_done"):
        writers.need(name)
    created, target_id = 0, p["target_id"]
    if target_id is None:
        target_id = int(writers.need("create_sprint")(board_id, p["name"], p["start"], p["end"])["id"])
        created = 1
    _state(client, target_id, "future", "JIRA_TARGET_NOT_FUTURE")
    if p["catchup"]:
        client.set_sprint_state(p["sprint_id"], "active")
        _state(client, p["sprint_id"], "active", "JIRA_CATCHUP_FAILED")
    if p["unfinished"]:
        client.move_issues(target_id, p["unfinished"])
        have = {i["key"] for i in client.sprint_issues(target_id, "statusCategory != Done", "summary") if i.get("key")}
        if any(key not in have for key in p["unfinished"]):
            raise JiraError("JIRA_CARRY_VERIFY_FAILED")
    have = _members(client, target_id)
    if any(key not in have for key in p["already"]):
        raise JiraError("JIRA_CARRY_VERIFY_FAILED")
    if p["backlog"]:
        _place_in_backlog(client, board_id, p, target_id)
    for root, latest, fields in recurrence.jobs(client.sprint_issues(p["sprint_id"], "labels = recurring", recurrence.SERIES_FIELDS)
                                                + client.sprint_issues(target_id, "labels = recurring", recurrence.SERIES_FIELDS), target_id)[1]:
        writers.need("create_clone")(root, fields, target_id, latest)
    after = recurrence.jobs(client.sprint_issues(p["sprint_id"], "labels = recurring", recurrence.SERIES_FIELDS)
                            + client.sprint_issues(target_id, "labels = recurring", recurrence.SERIES_FIELDS), target_id)
    if after[1]:
        raise JiraError("JIRA_CARRY_VERIFY_FAILED")
    for key in p["close_recurring"]:
        writers.need("close_done")(key, automation["run_id"])
    left = {i["key"] for i in client.sprint_issues(p["sprint_id"], "statusCategory != Done", "summary,issuetype")
            if i.get("key") and ((i.get("fields") or {}).get("issuetype") or {}).get("name") != "Epic"}
    _, stranded = split_unfinished(client.sprint_issues(p["sprint_id"], "statusCategory != Done", UNFINISHED_FIELDS))
    if left != set(stranded):
        raise JiraError("JIRA_MEJ_INVENTORY_INVALID")
    client.set_sprint_state(p["sprint_id"], "closed")
    _state(client, p["sprint_id"], "closed", "JIRA_CLOSE_VERIFY_FAILED")
    client.set_sprint_state(target_id, "active")
    _state(client, target_id, "active", "JIRA_START_VERIFY_FAILED")
    after = owned(client.sprints(board_id, "active"), project, keys)
    if len(after) != 1 or int(after[0].get("id", -1)) != target_id:
        raise JiraError("JIRA_FINAL_VERIFY_FAILED")
    return {"created": created, "carried": len(p["unfinished"]), "stranded": p["stranded"], "backlogged": len(p["backlog"]),
            "cloned": len(p["clones"]), "recurring_closed": len(p["close_recurring"])}


def plan(client, project, board_id, now, tz, keys=(), auto=False, selective=None, automation=None):
    if str(client.board(board_id).get("type", "")).lower() != "scrum":
        raise JiraError("JIRA_BOARD_NOT_SCRUM")
    active = owned(client.sprints(board_id, "active"), project, keys)
    future = owned(client.sprints(board_id, "future"), project, keys)
    # First activation: open an unambiguous scheduled sprint without inventing a predecessor; an expired future sprint
    # STOPS the run (it needs an explicit recovery decision).
    if not active:
        expired, current = [], []
        for candidate in future:
            first, last = _when(candidate.get("startDate")), _when(candidate.get("endDate"))
            if not first or not last:
                continue
            if last.astimezone(tz) <= now:
                expired.append(candidate)
            elif first.astimezone(tz) <= now < last.astimezone(tz):
                current.append(candidate)
        if len(current) > 1:
            raise JiraError("JIRA_ACTIVE_AMBIGUOUS")
        if not expired and len(current) == 1:
            first = current[0]
            sprint_days(first, tz)
            return {"start_only": True, "sprint_id": int(first["id"])}
        if not expired and not current and auto and future and all(
            _when(s.get("startDate")) and _when(s["startDate"]).astimezone(tz) > now for s in future
        ):
            return {"not_due": True}
    sprint, catchup = resolve_current(active, future, now, tz)
    sprint_id = int(sprint["id"])
    if auto:
        end = _when(sprint.get("endDate"))
        if not end or now < datetime.combine(end.astimezone(tz).date() + timedelta(days=1), clock(AUTO_AFTER_HOUR), tzinfo=tz):
            return {"not_due": True}
    # every unfinished item in the sprint moves, whatever its project: cross-assigned work must not be stranded in a closed sprint
    raw = client.sprint_issues(sprint_id, "statusCategory != Done", recurrence.SERIES_FIELDS if automation else UNFINISHED_FIELDS + (",sprint" if selective else ""))
    unfinished, stranded = split_unfinished(raw)
    end_local, start, end = target_week(sprint, tz)
    matching = [s for s in future if int(s.get("id", -1)) != sprint_id and _matches(s, start, end, tz)]
    if len(matching) > 1:
        raise JiraError("JIRA_TARGET_AMBIGUOUS")
    if sprint_days(sprint, tz) == 14 and not matching:
        raise JiraError("JIRA_TARGET_NOT_STAGED")
    out = {"sprint_id": sprint_id, "catchup": catchup, "unfinished": unfinished, "stranded": len(stranded), "end_local": end_local,
           "start": start, "end": end, "target_id": int(matching[0]["id"]) if matching else None,
           "name": sprint_name(project, start, end)}
    if selective:
        out.update(_selective_partition(client, sprint_id, raw, out["target_id"], selective))
    if automation is not None:
        out["name"] = reconcile.iso_name(project, start.date())
        out.update(_automation_partition(client, project, sprint_id, raw, stranded, out["target_id"], automation))
    return out


def execute(client, project, board_id, p, now, sleep=time.sleep, keys=(), selective=None):
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
            raise JiraError("JIRA_CARRY_VERIFY_FAILED")
    if selective:
        have = _members(client, target_id)
        if any(key not in have for key in p["already"]):
            raise JiraError("JIRA_CARRY_VERIFY_FAILED")
        if p["backlog"]:
            _place_in_backlog(client, board_id, p, target_id)
        _closable(client, p["sprint_id"], p)
    client.set_sprint_state(p["sprint_id"], "closed")
    _state(client, p["sprint_id"], "closed", "JIRA_CLOSE_VERIFY_FAILED")
    client.set_sprint_state(target_id, "active")
    _state(client, target_id, "active", "JIRA_START_VERIFY_FAILED")
    after = owned(client.sprints(board_id, "active"), project, keys)
    if len(after) != 1 or int(after[0].get("id", -1)) != target_id:
        raise JiraError("JIRA_FINAL_VERIFY_FAILED")
    out = {"created": created, "carried": len(p["unfinished"]), "stranded": p["stranded"]}
    if selective:
        out.update({"backlogged": len(p["backlog"]), "finite_missed": p["finite_missed"]})
    return out


def run_project(client, project, board_id, live, now, tz, sleep, keys=(), auto=False, mej_selective=None, automation=None):
    """`mej_selective`: None (the default) keeps every board on the standard path; a config applies only to MEJ."""
    selective = selective_config(mej_selective) if mej_selective is not None and str(project).upper() == MEJ_PROJECT else None
    retry = ("JIRA_CARRY_VERIFY_FAILED", "JIRA_MEJ_BACKLOG_PARTIAL") if selective else ("JIRA_CARRY_VERIFY_FAILED",)
    for attempt in range(2):
        if automation is not None:
            p = plan(client, project, board_id, now, tz, keys, auto, None, automation)
        else:
            p = plan(client, project, board_id, now, tz, keys, auto, selective) if selective else plan(client, project, board_id, now, tz, keys, auto)
        if p.get("not_due"):
            return {"not_due": 1}
        if p.get("start_only"):
            if not live:
                return {"would_start": 1}
            _state(client, p["sprint_id"], "future", "JIRA_TARGET_NOT_FUTURE")
            client.set_sprint_state(p["sprint_id"], "active")
            _state(client, p["sprint_id"], "active", "JIRA_START_VERIFY_FAILED")
            after = owned(client.sprints(board_id, "active"), project, keys)
            if len(after) != 1 or int(after[0].get("id", -1)) != p["sprint_id"]:
                raise JiraError("JIRA_FINAL_VERIFY_FAILED")
            return {"started": 1}
        if not live:
            preview = {"reuse": int(p["target_id"] is not None), "would_create": int(p["target_id"] is None),
                       "would_carry": len(p["unfinished"]), "would_strand": p["stranded"], "catchup": int(p["catchup"])}
            if automation is not None:
                return {**preview, "would_backlog": len(p["backlog"]), "would_clone": len(p["clones"]), "would_close_recurring": len(p["close_recurring"])}
            return {**preview, "would_backlog": len(p["backlog"]), "selective": 1} if selective else preview
        try:
            if automation is not None:
                done = _execute_automation(client, project, board_id, p, now, sleep, keys, automation)
            else:
                done = execute(client, project, board_id, p, now, sleep, keys, selective) if selective else execute(client, project, board_id, p, now, sleep, keys)
            return {**done, "closed": 1, "started": 1}
        except JiraError as error:
            if str(error) in (retry + ("JIRA_MEJ_BACKLOG_PARTIAL",) if automation is not None else retry) and attempt == 0:
                sleep(15)
                continue
            raise


def run(limit, live, environ=os.environ, client=None, now=None, sleep=time.sleep, auto=False, mej_selective=None, automation=None):
    from lifeos.platform.jira import Jira
    configured = boards(environ)
    client = client or Jira.from_env(environ)
    tz = ZoneInfo(LOCAL_TZ)
    now = now or datetime.now(tz)
    if automation is None and reconcile.enabled(environ):
        from . import writers as jira_writers
        automation = {"writers": jira_writers.live_writers(client, environ), "run_id": str(environ.get("GITHUB_RUN_ID") or "manual"), "roster": None}
    total = {"projects": len(configured), "ok": 0, "failed": 0, "reuse": 0, "would_create": 0, "would_carry": 0, "would_strand": 0,
             "catchup": 0, "not_due": 0, "skipped_readonly": 0, "created": 0, "carried": 0, "stranded": 0, "closed": 0, "started": 0, "writes": 0, "why": {}}
    keys = [entry[0] for entry in configured]
    for position, (project, board_id, _, readonly) in enumerate(configured, 1):
        if readonly:
            total["skipped_readonly"] += 1
            total["ok"] += 1
            continue
        try:
            if automation is not None:
                result = run_project(client, project, board_id, live, now, tz, sleep, keys, auto, mej_selective, automation)
            else:
                result = run_project(client, project, board_id, live, now, tz, sleep, keys, auto, mej_selective) if mej_selective is not None else run_project(client, project, board_id, live, now, tz, sleep, keys, auto)
        except JiraError as error:
            total["failed"] += 1
            code = (str(error) if str(error).startswith("JIRA_") else "JIRA_ERROR") + f"@{position}"
            total["why"][code] = total["why"].get(code, 0) + 1
            continue
        total["ok"] += 1
        for key, value in result.items():
            total[key] = total.get(key, 0) + value
        total["writes"] += result.get("created", 0) + result.get("closed", 0) + result.get("started", 0) if live else 0
    if total["failed"]:
        raise JiraError(f"JIRA_ROLLOVER_FAILED:{total['failed']}of{total['projects']}:" + ",".join(sorted(total["why"])))
    return total
