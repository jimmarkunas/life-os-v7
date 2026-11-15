"""Read-only Jira, Calendar, Todoist and Asana sources for the Action Inbox (LIFE-708, LIFE-709).

A source is what `inbox.build_inbox` expects: `.source .workspace .account .identity` and `.fetch(credential_ref) -> native dicts`. These get their data from ONE read callable each, never from a client
object: Jira's `board_issues` and Calendar's `list_events` (both GET-only); Todoist and Asana take a read function the same way and have no V7 client yet. No write, transition, comment, insert, update or delete can be reached from here, and a test scans this file to keep it so.

Nothing is fetched live: `open_reader` defaults to a refusal while `LIVE_READS_ENABLED` is False. Turning it on needs Jim's recorded approval of the account, scope and credential per workspace
(docs/platform/inbox-access-contract.md, section F). Output is native dicts only: no shadow task store, no description text mined, no exception message kept."""
from datetime import timedelta

from lifeos.inbox.model import InboxError

LIVE_READS_ENABLED = False                  # no real Jira or Calendar read until Jim approves the exact account, scope and credential per workspace and a stage is released
JIRA_FIELDS = "summary,status,assignee,duedate,updated"
JIRA_JQL = 'status = "Human UAT" OR (assignee = currentUser() AND statusCategory != Done)'
WINDOW_DAYS = 14


def refuse_live(credential_ref):
    """The default `open_reader`: a fixed refusal, and no client or credential is ever built. Even with the switch on, no live reader exists yet."""
    raise InboxError("live_reader_missing" if LIVE_READS_ENABLED else "live_reads_not_authorized")


class _Source:
    def __init__(self, workspace, account, identity, open_reader=refuse_live, tz="America/Chicago"):
        self.workspace, self.account, self.identity, self._open, self._tz = workspace, account, identity, open_reader, tz

    def fetch(self, credential_ref):
        out = []
        for item in self._read(self._open(credential_ref)):
            try:
                native = self._native(item)
            except (KeyError, TypeError, AttributeError):
                native = {}                                                                 # the Inbox rejects it and shows this source as degraded; the good rows stay
            if native is not None:                                                          # None: out of scope for this account, left out without a trace
                out.append(native)
        return out


class JiraSource(_Source):
    """`open_reader(credential_ref)` returns a callable `(board_id, jql, fields) -> issues`; in production only `Jira.board_issues` (a GET)."""
    source = "jira"

    def __init__(self, workspace, account, identity, board_id, base_url, **kwargs):
        super().__init__(workspace, account, identity, **kwargs)
        self._board, self._base = board_id, base_url.rstrip("/")

    def _read(self, read):
        issues = read(self._board, JIRA_JQL, JIRA_FIELDS)
        if not isinstance(issues, list):
            raise InboxError("listing_incomplete")                                           # a partial answer is an error, never a smaller list
        return issues

    def _native(self, issue):
        fields = issue["fields"]
        who, url, status = fields.get("assignee") or {}, f"{self._base}/browse/{issue['key']}", fields["status"]["name"]
        return {"key": issue["key"], "url": url, "summary": fields.get("summary"), "description": "", "assignee": who.get("accountId", ""), "status": status,
                "updated": fields["updated"], "tz": self._tz, "due": fields.get("duedate"), "evidence": [url] if status == "Human UAT" else []}      # the receipt lives on the issue itself


class CalendarSource(_Source):
    """`open_reader(credential_ref)` returns a callable `(time_min, time_max) -> events`; in production only `GoogleCalendar.list_events` (a GET)."""
    source = "calendar"

    def __init__(self, workspace, account, identity, now, **kwargs):
        super().__init__(workspace, account, identity, **kwargs)
        self._now = now

    def _read(self, read):
        events = read(self._now.isoformat(), (self._now + timedelta(days=WINDOW_DAYS)).isoformat())
        if not isinstance(events, list):
            raise InboxError("listing_incomplete")
        return events

    def _native(self, event):
        start = event["start"]
        return {"id": event["id"], "url": event["htmlLink"], "summary": event.get("summary"), "description": "", "status": event["status"], "updated": event["updated"],
                "tz": start.get("timeZone") or self._tz, "start": start.get("dateTime") or start["date"]}


class TodoistSource(_Source):
    """Personal tasks from the approved projects only. `open_reader(credential_ref)` returns `() -> tasks` (the active-task listing). Todoist is Personal-only in this release.
    Todoist's task listing carries no update time, so the revision follows status and creation until the real shape is verified (documented shape, not a live read)."""
    source = "todoist"

    def __init__(self, workspace, account, identity, projects, **kwargs):
        super().__init__(workspace, account, identity, **kwargs)
        self._projects = frozenset(str(p) for p in projects)

    def fetch(self, credential_ref):
        if self.workspace != "personal":
            raise InboxError("todoist_personal_only")
        return super().fetch(credential_ref)

    def _read(self, read):
        tasks = read()
        if not isinstance(tasks, list):
            raise InboxError("listing_incomplete")
        return tasks

    def _native(self, task):
        if str(task["project_id"]) not in self._projects:
            return None
        due = task.get("due") or {}
        return {"id": str(task["id"]), "url": task["url"], "content": task["content"], "description": "", "assignee": self.identity, "status": "completed" if task.get("is_completed") else "open",
                "updated": task["created_at"], "tz": due.get("timezone") or self._tz, "due": due.get("datetime") or due.get("date"), "project_scope": "personal"}


class AsanaSource(_Source):
    """Bytalos programme work only. `open_reader(credential_ref)` returns `(project_gids) -> tasks`. A task counts only when every project it sits in is on the approved list; a task in none of
    them is out of scope, and a task in an approved project AND any other (or in no project) is ambiguous, so it is rejected and the source shows degraded. Never TGP or client projects."""
    source = "asana"

    def __init__(self, workspace, account, identity, projects, **kwargs):
        super().__init__(workspace, account, identity, **kwargs)
        self._projects = frozenset(str(p) for p in projects)

    def fetch(self, credential_ref):
        if self.workspace != "bytalos":
            raise InboxError("asana_bytalos_only")
        return super().fetch(credential_ref)

    def _read(self, read):
        tasks = read(sorted(self._projects))
        if not isinstance(tasks, list):
            raise InboxError("listing_incomplete")
        return tasks

    def _native(self, task):
        sitting = {str(p["gid"]) for p in task["projects"]}
        if not sitting:
            raise KeyError("projects")                                                     # in no project: ambiguous
        if not sitting & self._projects:
            return None
        if not sitting <= self._projects:
            raise KeyError("projects")                                                     # also in a project that is not approved (possibly TGP or client): ambiguous
        if task.get("completed"):
            return None
        who = task.get("assignee") or {}
        return {"gid": str(task["gid"]), "url": task["permalink_url"], "name": task["name"], "notes": "", "assignee": who.get("gid", ""), "status": "open", "updated": task["modified_at"],
                "tz": self._tz, "due": task.get("due_at") or task.get("due_on")}
