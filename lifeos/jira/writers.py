"""Live Jira writers behind JIRA_WRITERS_VERIFIED, the read-only contract check and the throwaway-sprint verification.

Without the row exactly `yes` every live write fails closed. Entry points: `python -m lifeos.jira.writers
contract-check` and `verify --board JFM --issue JFM-123 [--live]` (dry run unless --live).
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform import jira as jira_module
from lifeos.platform.jira import Jira, JiraError

from . import reconcile as rc
from .boards import boards

TEST_PREFIX = "ZZ-LIFE918-TEST"
ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}-\d{1,7}$")
CLONE_LINK = "Cloners"
EDITABLE = ("summary", "description", "assignee", "priority", "labels", "parent")


def adf(text):
    if isinstance(text, dict):
        return text
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": str(text)}]}]}


def payload(fields, project, kind, parent=None):
    """Create-issue fields of one clone (`reconcile.clone_fields` output); absent values are left out."""
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
    def create_clone(root, fields, sprint_id=None, origin=None):
        origin = origin or root
        meta = (client.issue(origin, "issuetype,project").get("fields") or {})
        project, kind = (meta.get("project") or {}).get("key"), (meta.get("issuetype") or {}).get("name")
        if not project or not kind:
            raise JiraError("JIRA_RECONCILE_IDENTITY_AMBIGUOUS")
        key = client.create_issue(payload(fields, project, kind))["key"]
        try:
            client.link_issues(CLONE_LINK, key, origin)  # the clone is the INWARD argument, so its entry carries outwardIssue=<origin>
        except JiraError:
            client.delete_issue(key)
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
    """The writers a live run may use: all six when JIRA_WRITERS_VERIFIED is "yes", else none."""
    return raw_writers(client) if rc.writers_verified(environ) else rc.Writers()


PERMISSIONS = ("CREATE_ISSUES", "EDIT_ISSUES", "DELETE_ISSUES", "TRANSITION_ISSUES", "MANAGE_SPRINTS_PERMISSION", "SCHEDULE_ISSUES")
ATTEMPTS, WAIT = 4, 5
MEMBER_ATTEMPTS, MEMBER_WAIT = 6, 10


def _eventually(call, sleep, attempts=ATTEMPTS, wait=WAIT):
    value = call()
    for _ in range(attempts - 1):
        if value:
            break
        sleep(wait)
        value = call()
    return value


def _with_meta(read, *args, meta):
    try:
        return read(*args, meta=meta)
    except TypeError:
        return read(*args)


def _sprint_field_id(client):
    try:
        for field in client.field_list():
            if field.get("name") == "Sprint" and field.get("id"):
                return field["id"]
    except JiraError:
        pass
    return None


def _sprint_ids(client, key, field_id):
    if not field_id:
        return None
    try:
        value = ((client.issue(key, field_id) or {}).get("fields") or {}).get(field_id)
    except JiraError:
        return None
    if not isinstance(value, list):
        return None
    return [int(v["id"]) for v in value if isinstance(v, dict) and v.get("id") is not None]


def _write_state(client):
    """The last Agile write's {"write_ok", "body_empty"}, then forgotten."""
    last = getattr(client, "last_write", None)
    try:
        client.last_write = None
    except AttributeError:
        pass
    return None if not last else {"write_ok": bool(last.get("ok")), "body_empty": bool(last.get("body_empty"))}


def permissions(client, project):
    """-> {permission: bool} from GET mypermissions; a failed read gives all False."""
    try:
        have = client.my_permissions(project, PERMISSIONS)
        return {name: bool((have.get(name) or {}).get("havePermission")) for name in PERMISSIONS}
    except Exception:  # noqa: BLE001
        return {name: False for name in PERMISSIONS}


def contract_check(client, configured):
    """Read-only. -> {"ok", "boards", "checks": {name: bool}}: each board is scrum and answers sprint and backlog
    reads, the Cloners link type reads "clones" outward, and the needed fields exist. A failing read is a False check.
    """
    checks = {}

    def attempt(name, call):
        try:
            checks[name] = bool(call())
        except Exception:  # noqa: BLE001
            checks[name] = False
    for position, (_, board_id, _, readonly) in enumerate(configured, 1):
        attempt(f"board{position}_scrum", lambda b=board_id: str(client.board(b).get("type", "")).lower() == "scrum")
        attempt(f"board{position}_sprints", lambda b=board_id: isinstance(client.sprints(b, "future"), list) and isinstance(client.sprints(b, "active"), list))
        attempt(f"board{position}_backlog", lambda b=board_id: isinstance(client.backlog_issues(b, "statusCategory != Done", "summary"), list))
    attempt("cloners_link_type", lambda: any(t.get("name") == CLONE_LINK and t.get("outward") == "clones" for t in client.link_types()))
    names = set()
    attempt("fields", lambda: names.update(f.get("name") for f in client.field_list()) or {"Sprint", "Labels", "Linked Issues", "Due date"} <= names)
    held = {project: permissions(client, project) for project, _, _, readonly in configured if not readonly}
    listed = sprint_list_check(client, configured)
    return {"ok": all(checks.values()) and bool(checks), "boards": len(configured), "checks": checks, "permissions": held,
            "sprint_list": listed, "sprint_list_ok": bool(listed) and all(row["equal"] for row in listed)}


def sprint_list_check(client, configured):
    """Read-only cross-check of the Agile sprint list against JQL counts. -> rows with an `equal` flag."""
    from .search import search
    rows = []
    for position, (_, board_id, _, _) in enumerate(configured, 1):
        for state in ("active", "future"):
            try:
                sprints = client.sprints(board_id, state)
            except Exception:  # noqa: BLE001
                rows.append({"board": position, "sprint": None, "state": state, "list_open": None, "list_all": None, "jql_open": None, "jql_all": None, "equal": False})
                continue
            for sprint in sprints:
                row = {"board": position, "sprint": int(sprint["id"]), "state": state}
                try:
                    row["list_open"] = len(client.sprint_issues(row["sprint"], "statusCategory != Done", "summary"))
                    row["list_all"] = len(client.sprint_issues(row["sprint"], "", "summary"))
                    row["jql_open"] = len(search(client, f"sprint = {row['sprint']} AND statusCategory != Done", "summary"))
                    row["jql_all"] = len(search(client, f"sprint = {row['sprint']}", "summary"))
                    row["equal"] = row["list_open"] == row["jql_open"] and row["list_all"] == row["jql_all"]
                except Exception:  # noqa: BLE001
                    row.update({"list_open": None, "list_all": None, "jql_open": None, "jql_all": None, "equal": False})
                rows.append(row)
    return rows


def _test_sprint_name(now):
    return f"{TEST_PREFIX} {now.strftime('%Y%m%d-%H%M')}"


def _held(client, sprint_id, key):
    return key in {i.get("key") for i in client.sprint_issues(sprint_id, "", "summary")}


def _code(error):
    text = str(error)
    return text if re.match(r"^JIRA_[A-Z0-9_]{2,60}(:[0-9]{3})?$", text) else "JIRA_ERROR"


def verify(client, configured, board, issue_key, live, now=None, sleep=None, keep=False):
    """The throwaway-sprint verification; dry run (live False) is the contract check and plan only. -> {"ok", "steps",
    "why", "cleaned", "mode"}.

    A bad board, issue key or contract check, or a missing DELETE_ISSUES permission, stops before any write. `keep`
    (for plans that cannot delete issues) keeps the test clone under "kept"; `keep` without `live` is refused.
    """
    if keep and not live:
        raise JiraError("JIRA_VERIFY_ARGUMENTS_INVALID")
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
    if not keep and not (check["permissions"].get(board) or {}).get("DELETE_ISSUES"):
        raise JiraError("JIRA_VERIFY_NO_DELETE_PERMISSION")
    tz = ZoneInfo("America/Chicago")
    now = now or datetime.now(tz)
    start = datetime.combine(now.astimezone(tz).date() + timedelta(days=60), datetime.min.time(), tzinfo=tz)
    name = _test_sprint_name(now)
    writers = raw_writers(client)
    steps, why, diag, sprint_id, clone = {}, {}, {}, None, None
    field_id = _sprint_field_id(client)

    def member(label, kind, write):
        began, tries = time.monotonic(), {"n": 0}

        def probe():
            tries["n"] += 1
            meta = {}
            if kind == "sprint":
                items = _with_meta(client.sprint_issues, sprint_id, "", "summary", meta=meta)
            else:
                items = _with_meta(client.backlog_issues, board_id, "statusCategory != Done", "summary", meta=meta)
            in_list = clone in {i.get("key") for i in items}
            ids = _sprint_ids(client, clone, field_id)
            by_field = False if ids is None else ((sprint_id in ids) if kind == "sprint" else (sprint_id not in ids))
            diag[label] = {"write": write, "reads": tries["n"], "list_items": meta.get("items", len(items)), "list_total": meta.get("total"), "list_pages": meta.get("pages"),
                           "list_complete": meta.get("complete"), "page_cap": jira_module.MAX_PAGES * jira_module.PAGE_SIZE, "in_list": in_list,
                           "field_sprints": None if ids is None else len(ids), "in_sprint_field": None if ids is None else (sprint_id in ids),
                           "passed_by": "both" if in_list and by_field else "list" if in_list else "field" if by_field else "none"}
            return in_list or by_field
        ok = _eventually(probe, sleep, MEMBER_ATTEMPTS, MEMBER_WAIT)
        diag[label]["seconds"] = int(time.monotonic() - began)
        return ok

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
        step("clone_into_sprint", lambda: bool(clone) and member("clone_into_sprint", "sprint", _write_state(client)))

        def identity():
            def read():
                fresh = client.issue(clone, "summary,labels,issuelinks,sprint,status")
                found = rc.series([origin, fresh], configured=(issue_key,))  # the origin need not carry `recurring`; the chain is what is checked
                return list(found) == [issue_key] and clone in found[issue_key]["keys"]
            return _eventually(read, sleep)
        step("clone_link_identity", identity)
        writers.sync_fields(clone, {"summary": f"{TEST_PREFIX} sync"})
        step("sync_fields", lambda: _eventually(lambda: (client.issue(clone, "summary").get("fields") or {}).get("summary") == f"{TEST_PREFIX} sync", sleep))
        _write_state(client)
        writers.move_to_backlog([clone])
        step("move_to_backlog", lambda: member("move_to_backlog", "backlog", _write_state(client)))
        client.move_issues(sprint_id, [clone])
        step("back_into_sprint", lambda: member("back_into_sprint", "sprint", _write_state(client)))
        writers.close_done(clone, "verify-" + now.strftime("%Y%m%d%H%M"))

        def closed():
            fields = client.issue(clone, "status,labels").get("fields") or {}
            return ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done" and rc.AUTO_LABEL in (fields.get("labels") or [])
        step("close_done_with_label", lambda: _eventually(closed, sleep))
    except JiraError as error:
        why["run"] = _code(error)
    finally:
        parts = {"clone": True, "sprint": True}
        for part in ("clone", "sprint"):
            try:
                if part == "clone" and clone and not keep:
                    client.delete_issue(clone)
                if part == "sprint" and sprint_id is not None:
                    client.delete_sprint(sprint_id)
            except JiraError as error:
                parts[part] = False
                why["cleanup_" + part] = _code(error)
        cleaned = all(parts.values())
    out = {"mode": "live", "ok": all(steps.values()) and len(steps) == 8 and cleaned and "run" not in why, "steps": steps, "why": why, "diag": diag, "cleaned": cleaned, "cleaned_parts": parts}
    if keep:
        out["kept"] = [clone] if clone else []
    return out


def main(argv=None, environ=os.environ, client=None):
    parser = argparse.ArgumentParser(prog="python -m lifeos.jira.writers")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("contract-check")
    check = sub.add_parser("verify")
    check.add_argument("--board", required=True)
    check.add_argument("--issue", required=True)
    check.add_argument("--live", action="store_true")
    check.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)
    configured = boards(environ)
    client = client or Jira.from_env(environ)
    result = contract_check(client, configured) if args.command == "contract-check" else verify(client, configured, args.board, args.issue, args.live, keep=args.keep)
    print("jira-writers:", json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
