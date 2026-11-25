"""LIFE-918 wiring: the live Jira writers behind JIRA_WRITERS_VERIFIED, the read-only contract check and the throwaway-sprint verification (decision E).

Without the row exactly `yes` every live write fails closed. Entry points: `python -m lifeos.jira.writers contract-check`, and `verify --board JFM --issue JFM-123 [--live]` (a dry run unless --live).
The live verification creates a ZZ-LIFE918-TEST sprint, renames it, clones the one named issue into it, syncs, backlogs, closes it Done with the label, reads each back and deletes only what it made."""
# UNVERIFIED against the tenant until `verify --live` passes; MC then sets JIRA_WRITERS_VERIFIED. It refuses a board that is not configured, a sprint without the test prefix and any issue it did not create.
# Counts and fixed codes only; the Jira REST settings come from the environment like every Jira stage.

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform.jira import Jira, JiraError

from . import reconcile as rc
from .boards import boards

TEST_PREFIX = "ZZ-LIFE918-TEST"
ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}-\d{1,7}$")
CLONE_LINK = "Cloners"
EDITABLE = ("summary", "description", "assignee", "priority", "labels", "parent")


def adf(text):
    """A plain string as the minimal Atlassian document body the v3 API takes; a dict (already a document) passes through."""
    if isinstance(text, dict):
        return text
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": str(text)}]}]}


def payload(fields, project, kind, parent=None):
    """The create-issue fields of one clone (`reconcile.clone_fields` output). Absent values are left out; status and resolution are not fields (a new issue starts in To Do, unresolved)."""
    out = {"project": {"key": project}, "issuetype": {"name": kind}, "summary": fields["summary"]}
    if fields.get("description"):
        out["description"] = adf(fields["description"])
    if fields.get("assignee"):
        out["assignee"] = {"accountId": fields["assignee"]}
    if fields.get("priority"):
        out["priority"] = {"name": fields["priority"]}
    if fields.get("labels"):
        out["labels"] = list(fields["labels"])
    if parent or fields.get("parent"):
        out["parent"] = {"key": parent or fields["parent"]}
    if fields.get("duedate"):
        out["duedate"] = fields["duedate"]
    return out


def edit_fields(drift):
    out = {}
    for name in EDITABLE:
        if name not in drift:
            continue
        value = drift[name]
        out[name] = adf(value) if name == "description" and value else ({"accountId": value} if name == "assignee" and value else ({"name": value} if name == "priority" else ({"key": value} if name == "parent" and value else value)))
    return out


def raw_writers(client):
    """The six writes over a Jira client, ungated: only `live_writers` (behind JIRA_WRITERS_VERIFIED) and the throwaway verification (behind --live and the test prefix) call it."""
    def create_clone(root, fields, sprint_id=None, origin=None):
        origin = origin or root
        meta = (client.issue(origin, "issuetype,project").get("fields") or {})
        project, kind = (meta.get("project") or {}).get("key"), (meta.get("issuetype") or {}).get("name")
        if not project or not kind:
            raise JiraError("JIRA_RECONCILE_IDENTITY_AMBIGUOUS")
        key = client.create_issue(payload(fields, project, kind))["key"]
        try:
            client.link_issues(CLONE_LINK, key, origin)                     # "<clone> clones <origin>": the clone is the INWARD argument (the issue that reads the outward text "clones"), so its own link entry carries outwardIssue=<origin>
                                                                            # (live check Nov 25 2026: the opposite order wrote "origin clones clone", which series() and Jira's own Clone read as reversed)
        except JiraError:
            client.delete_issue(key)                                        # never leave an unlinked clone behind: the next run would create a second occurrence
            raise
        if sprint_id is not None:
            client.move_issues(sprint_id, [key])
        for sub in fields.get("subtasks") or []:
            client.create_issue(payload(sub, project, "Sub-task", parent=key))
        return key

    def sync_fields(key, drift):
        client.edit_issue(key, edit_fields(drift))

    def close_done(key, run_id):
        marker = rc.auto_close_fields(run_id)
        client.edit_issue(key, update={"labels": [{"add": marker["label"]}]})
        client.add_comment(key, marker["comment"])
        done = [t for t in client.transitions(key) if (((t.get("to") or {}).get("statusCategory") or {}).get("key")) == "done"]
        if len(done) != 1:
            raise JiraError("JIRA_RECONCILE_TRANSITION_AMBIGUOUS")
        client.transition(key, done[0]["id"])

    return rc.Writers(create_sprint=client.create_sprint, rename_sprint=client.rename_sprint, create_clone=create_clone, sync_fields=sync_fields,
                      move_to_backlog=client.move_to_backlog, close_done=close_done)


def live_writers(client, environ=None):
    """The writers a live run may use: all six when JIRA_WRITERS_VERIFIED is "yes", else none (every use fails closed with JIRA_RECONCILE_WRITER_UNVERIFIED)."""
    return raw_writers(client) if rc.writers_verified(environ) else rc.Writers()


PERMISSIONS = ("CREATE_ISSUES", "EDIT_ISSUES", "DELETE_ISSUES", "TRANSITION_ISSUES", "MANAGE_SPRINTS_PERMISSION", "SCHEDULE_ISSUES")
ATTEMPTS, WAIT = 4, 5


def _eventually(call, sleep):
    """Jira is eventually consistent: a read that is not yet true is retried (ATTEMPTS tries, WAIT seconds apart) before it counts as a failure. -> the last value."""
    value = call()
    for _ in range(ATTEMPTS - 1):
        if value:
            break
        sleep(WAIT)
        value = call()
    return value


def permissions(client, project):
    """-> {permission: bool} from GET mypermissions for one project; a read that fails gives all False (never an exception text)."""
    try:
        have = client.my_permissions(project, PERMISSIONS)
        return {name: bool((have.get(name) or {}).get("havePermission")) for name in PERMISSIONS}
    except Exception:                                                       # noqa: BLE001
        return {name: False for name in PERMISSIONS}


def contract_check(client, configured):
    """Read-only (GET only). -> {"ok": bool, "boards": n, "checks": {name: bool}}: each board is scrum and answers sprint and backlog reads, the Cloners link type reads "clones" outward, and the Sprint, labels, issuelinks and
    duedate fields exist. Counts and booleans only; a failing read is a False check, never an exception text."""
    checks = {}

    def attempt(name, call):
        try:
            checks[name] = bool(call())
        except Exception:                                                   # noqa: BLE001 - a failed read is a failed check
            checks[name] = False
    for position, (_, board_id, _, readonly) in enumerate(configured, 1):
        attempt(f"board{position}_scrum", lambda b=board_id: str(client.board(b).get("type", "")).lower() == "scrum")
        attempt(f"board{position}_sprints", lambda b=board_id: isinstance(client.sprints(b, "future"), list) and isinstance(client.sprints(b, "active"), list))
        attempt(f"board{position}_backlog", lambda b=board_id: isinstance(client.backlog_issues(b, "statusCategory != Done", "summary"), list))
    attempt("cloners_link_type", lambda: any(t.get("name") == CLONE_LINK and t.get("outward") == "clones" for t in client.link_types()))
    names = set()
    attempt("fields", lambda: names.update(f.get("name") for f in client.field_list()) or {"Sprint", "Labels", "Linked Issues", "Due date"} <= names)
    held = {project: permissions(client, project) for project, _, _, readonly in configured if not readonly}
    return {"ok": all(checks.values()) and bool(checks), "boards": len(configured), "checks": checks, "permissions": held}


def _test_sprint_name(now):
    return f"{TEST_PREFIX} {now.strftime('%Y%m%d-%H%M')}"


def _held(client, sprint_id, key):
    return key in {i.get("key") for i in client.sprint_issues(sprint_id, "", "summary")}


def _code(error):
    """A fixed code for a failed step: the JiraError's own code when it has one, else a generic one. Never message text."""
    text = str(error)
    return text if re.match(r"^JIRA_[A-Z0-9_]{2,60}(:[0-9]{3})?$", text) else "JIRA_ERROR"


def verify(client, configured, board, issue_key, live, now=None, sleep=None):
    """The throwaway-sprint verification. Dry run (live False): the contract check and the plan, no write. Live: see the module docstring. -> {"ok": bool, "steps": {name: bool}, "why": {step: fixed code},
    "cleaned": bool, "cleaned_parts": {"clone": bool, "sprint": bool}, "mode": ...}. A board that is not configured, an issue key that is not a plain key, a failed contract check or a missing DELETE_ISSUES permission
    stops before any write (we never create an issue we cannot remove). A step that fails is recorded with a fixed code and the run goes on to the cleanup; reads after writes are retried (Jira is eventually consistent)."""
    chosen = [b for b in configured if b[0] == board and not b[3]]
    if not chosen or not ISSUE_KEY.match(str(issue_key or "")):
        raise JiraError("JIRA_VERIFY_ARGUMENTS_INVALID")
    board_id = chosen[0][1]
    sleep = sleep or time.sleep
    check = contract_check(client, configured)
    if not live:
        return {"mode": "dry_run", "ok": check["ok"], "steps": check["checks"], "permissions": check["permissions"], "cleaned": True}
    if not check["ok"]:
        raise JiraError("JIRA_VERIFY_CONTRACT_FAILED")
    if not (check["permissions"].get(board) or {}).get("DELETE_ISSUES"):
        raise JiraError("JIRA_VERIFY_NO_DELETE_PERMISSION")
    tz = ZoneInfo("America/Chicago")
    now = now or datetime.now(tz)
    start = datetime.combine(now.astimezone(tz).date() + timedelta(days=60), datetime.min.time(), tzinfo=tz)       # far enough ahead to touch no real week
    name = _test_sprint_name(now)
    writers = raw_writers(client)
    steps, why, sprint_id, clone = {}, {}, None, None
    held = lambda: _eventually(lambda: _held(client, sprint_id, clone), sleep)

    def step(label, call):
        try:
            steps[label] = bool(call())
            if not steps[label]:
                why[label] = "READ_MISMATCH"
        except JiraError as error:
            steps[label], why[label] = False, _code(error)
    try:
        sprint_id = int(client.create_sprint(board_id, name, start, start + timedelta(days=6, hours=23, minutes=30))["id"])
        step("create_sprint", lambda: _eventually(lambda: any(int(s["id"]) == sprint_id and s.get("name") == name for s in client.sprints(board_id, "future")), sleep))
        renamed = name + " RENAMED"
        client.rename_sprint(sprint_id, renamed)
        step("rename_sprint", lambda: _eventually(lambda: any(int(s["id"]) == sprint_id and s.get("name") == renamed for s in client.sprints(board_id, "future")), sleep))
        origin = client.issue(issue_key, "summary,description,assignee,priority,labels,parent,duedate,issuetype,project,issuelinks")
        clone = writers.create_clone(issue_key, rc.clone_fields(origin, 7), sprint_id, issue_key)
        step("clone_into_sprint", lambda: bool(clone) and held())

        def identity():
            def read():
                fresh = client.issue(clone, "summary,labels,issuelinks,sprint,status")
                found = rc.series([origin, fresh], configured=(issue_key,))   # the origin need not carry the `recurring` label (the verification issue is any Done Task); the chain is what is checked
                return list(found) == [issue_key] and clone in found[issue_key]["keys"]
            return _eventually(read, sleep)
        step("clone_link_identity", identity)
        writers.sync_fields(clone, {"summary": f"{TEST_PREFIX} sync"})
        step("sync_fields", lambda: _eventually(lambda: (client.issue(clone, "summary").get("fields") or {}).get("summary") == f"{TEST_PREFIX} sync", sleep))
        writers.move_to_backlog([clone])
        step("move_to_backlog", lambda: _eventually(lambda: clone in {i.get("key") for i in client.backlog_issues(board_id, "statusCategory != Done", "summary")}, sleep))
        client.move_issues(sprint_id, [clone])
        step("back_into_sprint", held)
        writers.close_done(clone, "verify-" + now.strftime("%Y%m%d%H%M"))

        def closed():
            fields = client.issue(clone, "status,labels").get("fields") or {}
            return ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done" and rc.AUTO_LABEL in (fields.get("labels") or [])
        step("close_done_with_label", lambda: _eventually(closed, sleep))
    except JiraError as error:
        why["run"] = _code(error)                                           # a write that raised: the steps after it did not run; the cleanup below always does
    finally:
        parts = {"clone": True, "sprint": True}
        for part in ("clone", "sprint"):
            try:
                if part == "clone" and clone:
                    client.delete_issue(clone)                              # only the issue this run created
                if part == "sprint" and sprint_id is not None:
                    client.delete_sprint(sprint_id)                         # only the throwaway sprint this run created, named with the test prefix
            except JiraError as error:
                parts[part] = False
                why["cleanup_" + part] = _code(error)
        cleaned = all(parts.values())
    return {"mode": "live", "ok": all(steps.values()) and len(steps) == 8 and cleaned and "run" not in why, "steps": steps, "why": why, "cleaned": cleaned, "cleaned_parts": parts}


def main(argv=None, environ=os.environ, client=None):
    parser = argparse.ArgumentParser(prog="python -m lifeos.jira.writers")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("contract-check")
    check = sub.add_parser("verify")
    check.add_argument("--board", required=True)
    check.add_argument("--issue", required=True)
    check.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    configured = boards(environ)
    client = client or Jira.from_env(environ)
    result = contract_check(client, configured) if args.command == "contract-check" else verify(client, configured, args.board, args.issue, args.live)
    print("jira-writers:", json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
