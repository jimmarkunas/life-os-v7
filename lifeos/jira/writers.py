"""LIFE-918 wiring: the live Jira writers, the read-only contract check and the throwaway-sprint verification (decision E, Jim, Nov 25 2026, comment 14214). UNVERIFIED against the tenant until the check below passes.

`live_writers(client, environ)` gives the daily reconcile and the rollover boundary their writes (create_sprint, rename_sprint, create_clone, sync_fields, move_to_backlog, close_done) ONLY when the Runtime Configuration
row JIRA_WRITERS_VERIFIED is exactly "yes"; otherwise it gives an empty Writers, whose every use fails closed with JIRA_RECONCILE_WRITER_UNVERIFIED. MC sets the row after `verify` passes. Nothing here runs from the hourly tick
except through that gate.

Manual entry points (counts and fixed codes only; the Jira REST settings come from the environment like every Jira stage):
  python -m lifeos.jira.writers contract-check                       read-only GETs: boards are scrum, sprint and backlog reads work, the Cloners link type and the issue fields exist
  python -m lifeos.jira.writers verify --board JFM --issue JFM-123    dry run: what it would do, plus the contract check; writes nothing
  python -m lifeos.jira.writers verify --board JFM --issue JFM-123 --live
        creates a throwaway future sprint named ZZ-LIFE918-TEST ..., renames it, clones the ONE named issue into it, syncs a field, moves the clone to the backlog and back, closes it Done with the auto-closed label and
        one comment, reading every step back, then deletes the clone it created and the throwaway sprint. It refuses a board that is not configured, a sprint whose name lacks the prefix, and any issue it did not create."""
import argparse
import json
import os
import re
import sys
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
            client.link_issues(CLONE_LINK, origin, key)                     # the clone is the OUTWARD side: "<clone> clones <origin>"
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
        attempt(f"board{position}_scrum", lambda: str(client.board(board_id).get("type", "")).lower() == "scrum")
        attempt(f"board{position}_sprints", lambda: isinstance(client.sprints(board_id, "future"), list) and isinstance(client.sprints(board_id, "active"), list))
        attempt(f"board{position}_backlog", lambda: isinstance(client.backlog_issues(board_id, "statusCategory != Done", "summary"), list))
    attempt("cloners_link_type", lambda: any(t.get("name") == CLONE_LINK and t.get("outward") == "clones" for t in client.link_types()))
    names = set()
    attempt("fields", lambda: names.update(f.get("name") for f in client.field_list()) or {"Sprint", "Labels", "Linked Issues", "Due date"} <= names)
    return {"ok": all(checks.values()) and bool(checks), "boards": len(configured), "checks": checks}


def _test_sprint_name(now):
    return f"{TEST_PREFIX} {now.strftime('%Y%m%d-%H%M')}"


def _held(client, sprint_id, key):
    return key in {i.get("key") for i in client.sprint_issues(sprint_id, "", "summary")}


def verify(client, configured, board, issue_key, live, now=None, sleep=None):
    """The throwaway-sprint verification. Dry run (live False): the contract check and the plan, no write. Live: see the module docstring. -> {"ok": bool, "steps": {name: bool}, "cleaned": bool, "mode": ...}.
    A board that is not configured, an issue key that is not a plain key, or a step that does not read back stops at once; the cleanup always runs."""
    chosen = [b for b in configured if b[0] == board and not b[3]]
    if not chosen or not ISSUE_KEY.match(str(issue_key or "")):
        raise JiraError("JIRA_VERIFY_ARGUMENTS_INVALID")
    board_id = chosen[0][1]
    check = contract_check(client, configured)
    if not live:
        return {"mode": "dry_run", "ok": check["ok"], "steps": check["checks"], "cleaned": True}
    if not check["ok"]:
        raise JiraError("JIRA_VERIFY_CONTRACT_FAILED")
    tz = ZoneInfo("America/Chicago")
    now = now or datetime.now(tz)
    start = datetime.combine(now.astimezone(tz).date() + timedelta(days=60), datetime.min.time(), tzinfo=tz)       # far enough ahead to touch no real week
    name = _test_sprint_name(now)
    writers = raw_writers(client)
    steps, sprint_id, clone = {}, None, None
    try:
        sprint_id = int(client.create_sprint(board_id, name, start, start + timedelta(days=6, hours=23, minutes=30))["id"])
        steps["create_sprint"] = any(int(s["id"]) == sprint_id and s.get("name") == name for s in client.sprints(board_id, "future"))
        renamed = name + " RENAMED"
        client.rename_sprint(sprint_id, renamed)
        steps["rename_sprint"] = any(int(s["id"]) == sprint_id and s.get("name") == renamed for s in client.sprints(board_id, "future"))
        origin = client.issue(issue_key, "summary,description,assignee,priority,labels,parent,duedate,issuetype,project,issuelinks")
        clone = writers.create_clone(issue_key, rc.clone_fields(origin, 7), sprint_id, issue_key)
        steps["clone_into_sprint"] = bool(clone) and _held(client, sprint_id, clone)
        fresh = client.issue(clone, "summary,labels,issuelinks,sprint,status")
        found = rc.series([origin, fresh])
        steps["clone_link_identity"] = list(found) == [issue_key] and clone in found[issue_key]["keys"]
        writers.sync_fields(clone, {"summary": f"{TEST_PREFIX} sync"})
        steps["sync_fields"] = (client.issue(clone, "summary").get("fields") or {}).get("summary") == f"{TEST_PREFIX} sync"
        writers.move_to_backlog([clone])
        steps["move_to_backlog"] = clone in {i.get("key") for i in client.backlog_issues(board_id, "statusCategory != Done", "summary")}
        client.move_issues(sprint_id, [clone])
        steps["back_into_sprint"] = _held(client, sprint_id, clone)
        writers.close_done(clone, "verify-" + now.strftime("%Y%m%d%H%M"))
        closed = client.issue(clone, "status,labels")
        steps["close_done_with_label"] = (((closed.get("fields") or {}).get("status") or {}).get("statusCategory") or {}).get("key") == "done" and rc.AUTO_LABEL in ((closed.get("fields") or {}).get("labels") or [])
    finally:
        cleaned = True
        for step in ("clone", "sprint"):
            try:
                if step == "clone" and clone:
                    client.delete_issue(clone)                              # only the issue this run created
                if step == "sprint" and sprint_id is not None:
                    client.delete_sprint(sprint_id)                         # only the throwaway sprint this run created, named with the test prefix
            except JiraError:
                cleaned = False
    return {"mode": "live", "ok": all(steps.values()) and len(steps) == 8 and cleaned, "steps": steps, "cleaned": cleaned}


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
