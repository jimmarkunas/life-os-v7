"""Scheduled 7/14-day Jira sprint rollover: read-only unless live, fails closed on ambiguity, verifies every step it takes.

Never closes a sprint before its end, and never closes it before the carry-forward is verified. A sub-task stranded under a finished parent is left out of the move (D181).
Output: counts and fixed codes only; sprint names, issue keys and summaries are private."""
import os
import time
from datetime import datetime, time as clock, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform.jira import JiraError
from .boards import boards, owned

LOCAL_TZ = "America/Chicago"
UNFINISHED_FIELDS = "summary,status,issuetype,parent"
AUTO_AFTER_HOUR = 0                      # scheduled rollover runs on the first hourly tick after midnight Monday local; manual runs only need the sprint to have ended


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


def sprint_days(sprint, tz):
    """Use the Jira schedule as authority: exactly 1 or 2 Monday-Sunday weeks."""
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
    # Local dates, not elapsed UTC hours, preserve Monday/Sunday across DST.
    return end_local, start, start + timedelta(days=days - 1, hours=23, minutes=30)


def sprint_name(project, start, end):
    if start.year == end.year:
        return f"{project} Sprint — {start.strftime('%b %-d')}–{end.strftime('%b %-d, %Y')}"
    return f"{project} Sprint — {start.strftime('%b %-d, %Y')}–{end.strftime('%b %-d, %Y')}"


DST_TOLERANCE = timedelta(hours=2)       # a sprint stamped before a clock change is off by exactly the DST shift (1 hour); never reach a different day


def _matches(sprint, start, end, tz):
    """True when the sprint is the target week. Compared as instants within the DST shift, not as local dates: a legacy sprint stamped
    05:00Z (midnight CDT) reads as 11 PM the previous day in CST, and the local-date test missed it and created a duplicate (Nov 2, 2026)."""
    s, e = _when(sprint.get("startDate")), _when(sprint.get("endDate"))
    return bool(s and e and abs(s - start) <= DST_TOLERANCE and abs(e - end) <= DST_TOLERANCE)


def _state(client, sprint_id, expected, code):
    if client.sprint(sprint_id).get("state") != expected:
        raise JiraError(code)


def split_unfinished(issues):
    """-> (keys to carry, keys stranded). Jira keeps a sub-task in its parent's sprint, so a sub-task moves with its parent. When the parent is unfinished too, the sub-task follows it and is carried and
    verified with it. When the parent is Done it stays behind in the closed sprint and can never appear in the next one: carrying it would fail the verification every hour (Oct 12: one such sub-task kept a
    board on its old sprint), so it is left out and counted as stranded."""
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
    """LIFE-915: pure preflight for a Matt-approved Task commitment roster.

    Not connected to run()/execute() until Matt's exact roster and Jira's
    backlog-placement/readback contract are live-proven. It never calls Jira.
    An unresolved unfinished Task cannot be silently re-added, abandoned or
    presumed in backlog. Existing JFM rollover remains untouched.

    LIFE-915 gated path: with backlog_verified=True (an injected, read-back-proven backlog placement exists) the unapproved standard work and its
    children are returned as a third list, to be placed in the backlog, instead of blocking close; `already_carried` are roster keys a previous
    interrupted run already moved into the target sprint (restart recovery), so they need not be in the source inventory. The default call is unchanged.
    """
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
    # Without a verified backlog API/readback, an unfinished standard item
    # outside the approved roster must block close, not vanish into Jira's
    # provider-defined sprint-closure behavior.
    for key, (name, subtask, _) in found.items():
        if not subtask and key not in keys and not backlog_verified:
            raise JiraError("JIRA_MEJ_BACKLOG_UNVERIFIED")
    stranded, backlog = [], [key for key, (_, subtask, _) in found.items() if not subtask and key not in keys]
    carry = [key for key in keys if key in found]
    for key, (_, subtask, parent) in found.items():
        if not subtask:
            continue
        if backlog_verified and not parent:
            raise JiraError("JIRA_MEJ_INVENTORY_INVALID")          # a sub-task with no parent at all: its destination cannot be decided
        if parent in keys:
            carry.append(key)                     # the child follows a carried parent
        elif backlog_verified and parent in backlog:
            backlog.append(key)                   # the child follows its unapproved parent into the backlog
        else:
            stranded.append(key)                  # D181: parent Done/outside sprint
    if backlog_verified:
        return carry, stranded, backlog
    return carry, stranded


MEJ_PROJECT = "MEJ"
MAX_ROSTER = 50


def selective_config(config):
    """LIFE-915 gate (off by default; no env var or setting: the caller passes it in code, LIFE-918 will). -> the validated config: {"roster": approved Task/Story keys, "finite": finite weekly occurrence keys,
    "consent": the reference to the approved roster, "backlog": an object with move_to_backlog(keys) and backlog_issues(board_id, jql, fields), or None while the tenant contract is UNVERIFIED}."""
    if not isinstance(config, dict):
        raise JiraError("JIRA_MEJ_ROSTER_INVALID")
    roster, finite, consent = config.get("roster"), config.get("finite", ()), config.get("consent")
    if not isinstance(roster, (list, tuple)) or len(roster) > MAX_ROSTER or not isinstance(finite, (list, tuple)):
        raise JiraError("JIRA_MEJ_ROSTER_INVALID")
    if not isinstance(consent, str) or not consent.strip():
        raise JiraError("JIRA_MEJ_ROSTER_INVALID")                       # no approved roster without its consent reference
    return {"roster": list(roster), "finite": list(finite), "consent": consent.strip(), "backlog": config.get("backlog")}


def _members(client, sprint_id):
    return {i["key"] for i in client.sprint_issues(sprint_id, "statusCategory != Done", "summary") if i.get("key")}


def _backlog_keys(backlog, board_id):
    """The keys the backlog currently holds. Any failure (403, 429, timeout, malformed) is one fixed code: nothing has been closed."""
    try:
        return {i["key"] for i in backlog.backlog_issues(board_id, "statusCategory != Done", "summary") if i.get("key")}
    except Exception as error:                                           # noqa: BLE001 - provider errors, timeouts and bad shapes are all the same refusal
        raise JiraError("JIRA_MEJ_BACKLOG_FAILED") from error


def _orphans(raw):
    """A sub-task whose parent is not among the unfinished work is only D181-stranded when that parent is verifiably Done. A child left behind by a parent that is still unfinished (moved to the backlog or
    another sprint without it) is a destination mismatch: closing would leave the pair apart. The parent's status comes from the child's own `parent` field; unknown is not Done."""
    present = {i["key"] for i in raw if i.get("key")}
    for issue in raw:
        fields = issue.get("fields") or {}
        parent = fields.get("parent") or {}
        if (fields.get("issuetype") or {}).get("subtask") and parent.get("key") and parent["key"] not in present:
            status = ((parent.get("fields") or {}).get("status") or {})
            if status.get("name") != "Done" and (status.get("statusCategory") or {}).get("key") != "done":
                raise JiraError("JIRA_MEJ_DESTINATION_MISMATCH")


def _selective_partition(client, sprint_id, raw, target_id, selective):
    """The gated MEJ partition of the source sprint's unfinished work: carry (approved Tasks/Stories and their children), backlog (the rest and their children), stranded (D181). Never calls a write."""
    if client.sprint(sprint_id).get("state") not in ("active", "future"):
        raise JiraError("JIRA_MEJ_SPRINT_STALE")                         # the sprint read is stale: it is already closed
    for issue in raw:
        current = ((issue.get("fields") or {}).get("sprint") or {}).get("id")
        if current is not None and int(current) != sprint_id:
            raise JiraError("JIRA_MEJ_SPRINT_STALE")                     # an issue the read says is in another sprint
    _orphans(raw)
    backlog = selective["backlog"]
    already = _members(client, target_id) & set(selective["roster"]) if target_id else set()
    result = preview_mej_selective_carry(raw, selective["roster"], selective["finite"], backlog_verified=backlog is not None, already_carried=already)
    carry, stranded, to_backlog = result if backlog is not None else (*result, [])
    finite = set(selective["finite"])
    return {"unfinished": [k for k in carry if k not in already], "already": sorted(already), "stranded": len(stranded), "backlog": to_backlog, "selective": selective,
            "finite_missed": len([k for k in to_backlog if k in finite])}


def _place_in_backlog(client, board_id, p, target_id):
    """Move the excluded work to the backlog and read it back before anything is closed; every ambiguity is a fixed code and the sprint stays open."""
    selective, keys = p["selective"], p["backlog"]
    backlog = selective["backlog"]
    try:
        backlog.move_to_backlog(keys)
    except Exception as error:                                           # noqa: BLE001
        raise JiraError("JIRA_MEJ_BACKLOG_FAILED") from error
    held = _backlog_keys(backlog, board_id)
    if any(key not in held for key in keys):
        raise JiraError("JIRA_MEJ_BACKLOG_PARTIAL")                      # Jira's reads can lag: run_project re-plans once
    carried = _members(client, target_id)
    if any(key in held for key in p["unfinished"] + p["already"]) or any(key in carried for key in keys):
        raise JiraError("JIRA_MEJ_DESTINATION_MISMATCH")                 # a parent and its child (or a carried and a backlogged key) ended up apart


def _closable(client, sprint_id, p):
    """Immediately before close: nothing but the declared stranded sub-tasks may still be unfinished in the source sprint."""
    raw = client.sprint_issues(sprint_id, "statusCategory != Done", UNFINISHED_FIELDS)
    _orphans(raw)
    _, stranded = split_unfinished(raw)
    left = {i["key"] for i in client.sprint_issues(sprint_id, "statusCategory != Done", "summary") if i.get("key")}
    if left != set(stranded):
        raise JiraError("JIRA_MEJ_INVENTORY_INVALID")


def plan(client, project, board_id, now, tz, keys=(), auto=False, selective=None):
    if str(client.board(board_id).get("type", "")).lower() != "scrum":
        raise JiraError("JIRA_BOARD_NOT_SCRUM")
    active = owned(client.sprints(board_id, "active"), project, keys)
    future = owned(client.sprints(board_id, "future"), project, keys)
    # First activation: open an unambiguous currently scheduled sprint without
    # inventing a preceding sprint. If an older future sprint has already
    # expired, STOP: its unfinished work requires an explicit recovery decision.
    # In particular, never silently combine a stale sprint with a pre-planned
    # customer roadmap sprint.
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
    if auto:                                                  # scheduled: do nothing until the sprint has ended and it is Monday morning
        end = _when(sprint.get("endDate"))
        if not end or now < datetime.combine(end.astimezone(tz).date() + timedelta(days=1), clock(AUTO_AFTER_HOUR), tzinfo=tz):
            return {"not_due": True}
    # every unfinished item in the sprint moves, whatever its project: cross-assigned work must not be stranded in a closed sprint
    raw = client.sprint_issues(sprint_id, "statusCategory != Done", UNFINISHED_FIELDS + (",sprint" if selective else ""))
    unfinished, stranded = split_unfinished(raw)
    end_local, start, end = target_week(sprint, tz)
    matching = [s for s in future if int(s.get("id", -1)) != sprint_id and _matches(s, start, end, tz)]
    if len(matching) > 1:
        raise JiraError("JIRA_TARGET_AMBIGUOUS")
    # A two-week roadmap is finite. Do not manufacture Sprint 9 after its
    # planned final date, or reinterpret a different future sprint as the next.
    if sprint_days(sprint, tz) == 14 and not matching:
        raise JiraError("JIRA_TARGET_NOT_STAGED")
    out = {"sprint_id": sprint_id, "catchup": catchup, "unfinished": unfinished, "stranded": len(stranded), "end_local": end_local,
           "start": start, "end": end, "target_id": int(matching[0]["id"]) if matching else None,
           "name": sprint_name(project, start, end)}
    if selective:
        out.update(_selective_partition(client, sprint_id, raw, out["target_id"], selective))
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
            raise JiraError("JIRA_CARRY_VERIFY_FAILED")           # Jira's sprint reads can lag; nothing has been closed yet
    if selective:
        have = _members(client, target_id)
        if any(key not in have for key in p["already"]):
            raise JiraError("JIRA_CARRY_VERIFY_FAILED")           # restart recovery: what an interrupted run carried must still be there
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


def run_project(client, project, board_id, live, now, tz, sleep, keys=(), auto=False, mej_selective=None):
    """`mej_selective` is the LIFE-915 gate: None (the default, and what every caller passes today) leaves this exactly as it was for every board; a config (see selective_config) applies only to MEJ."""
    selective = selective_config(mej_selective) if mej_selective is not None and str(project).upper() == MEJ_PROJECT else None
    retry = ("JIRA_CARRY_VERIFY_FAILED", "JIRA_MEJ_BACKLOG_PARTIAL") if selective else ("JIRA_CARRY_VERIFY_FAILED",)
    for attempt in range(2):
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
            return {**preview, "would_backlog": len(p["backlog"]), "selective": 1} if selective else preview
        try:
            done = execute(client, project, board_id, p, now, sleep, keys, selective) if selective else execute(client, project, board_id, p, now, sleep, keys)
            return {**done, "closed": 1, "started": 1}
        except JiraError as error:
            if str(error) in retry and attempt == 0:
                sleep(15)                                         # known Jira consistency lag: one re-plan, then give up
                continue
            raise


def run(limit, live, environ=os.environ, client=None, now=None, sleep=time.sleep, auto=False, mej_selective=None):
    from lifeos.platform.jira import Jira
    configured = boards(environ)
    client = client or Jira.from_env(environ)
    tz = ZoneInfo(LOCAL_TZ)
    now = now or datetime.now(tz)
    total = {"projects": len(configured), "ok": 0, "failed": 0, "reuse": 0, "would_create": 0, "would_carry": 0, "would_strand": 0,
             "catchup": 0, "not_due": 0, "skipped_readonly": 0, "created": 0, "carried": 0, "stranded": 0, "closed": 0, "started": 0, "writes": 0, "why": {}}
    keys = [entry[0] for entry in configured]
    for position, (project, board_id, _, readonly) in enumerate(configured, 1):
        if readonly:
            total["skipped_readonly"] += 1
            total["ok"] += 1
            continue
        try:
            result = run_project(client, project, board_id, live, now, tz, sleep, keys, auto, mej_selective) if mej_selective is not None else run_project(client, project, board_id, live, now, tz, sleep, keys, auto)
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
