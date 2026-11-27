"""Read-only Jira, Calendar, Todoist and Asana sources for the Action Inbox.

A source has `.source .workspace .account .identity` and `.fetch(credential_ref) -> native dicts`, fed by ONE injected
read callable (never a client), so no write is reachable; a test scans this file to keep it so. `open_reader` refuses
while `LIVE_READS_ENABLED` is False.
"""

from datetime import timedelta

from lifeos.inbox.model import InboxError

LIVE_READS_ENABLED = False
# Sources readable live, each only by its own reader (concord: concord_reader.py); the rest refuse.
LIVE_READ_SOURCES = frozenset({"concord"})
JIRA_FIELDS = "summary,status,assignee,duedate,updated"
JIRA_JQL = 'status = "Human UAT" OR (assignee = currentUser() AND statusCategory != Done)'
WINDOW_DAYS = 14


def live_allowed(source):
    return source in LIVE_READ_SOURCES


def refuse_live(credential_ref):
    raise InboxError("live_reader_missing" if LIVE_READS_ENABLED else "live_reads_not_authorized")


class _Source:
    def __init__(self, workspace, account, identity, open_reader=refuse_live, tz="America/Chicago"):
        self.workspace, self.account, self.identity, self._open, self._tz = (
            workspace,
            account,
            identity,
            open_reader,
            tz,
        )

    def fetch(self, credential_ref):
        out = []
        for item in self._read(self._open(credential_ref)):
            try:
                native = self._native(item)
            except (KeyError, TypeError, AttributeError):
                native = {}  # the Inbox rejects it and shows this source as degraded; the good rows stay
            if native is not None:
                out.append(native)
        return out


class JiraSource(_Source):
    source = "jira"

    def __init__(self, workspace, account, identity, board_id, base_url, **kwargs):
        super().__init__(workspace, account, identity, **kwargs)
        self._board, self._base = board_id, base_url.rstrip("/")

    def _read(self, read):
        issues = read(self._board, JIRA_JQL, JIRA_FIELDS)
        if not isinstance(issues, list):
            raise InboxError("listing_incomplete")
        return issues

    def _native(self, issue):
        fields = issue["fields"]
        who, url, status = fields.get("assignee") or {}, f"{self._base}/browse/{issue['key']}", fields["status"]["name"]
        return {
            "key": issue["key"],
            "url": url,
            "summary": fields.get("summary"),
            "description": "",
            "assignee": who.get("accountId", ""),
            "status": status,
            "updated": fields["updated"],
            "tz": self._tz,
            "due": fields.get("duedate"),
            "evidence": [url] if status == "Human UAT" else [],
        }


class CalendarSource(_Source):
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
        return {
            "id": event["id"],
            "url": event["htmlLink"],
            "summary": event.get("summary"),
            "description": "",
            "status": event["status"],
            "updated": event["updated"],
            "tz": start.get("timeZone") or self._tz,
            "start": start.get("dateTime") or start["date"],
        }


class TodoistSource(_Source):
    """Personal tasks from the approved projects only; `open_reader(credential_ref)` returns `() -> tasks`. The
    revision follows status and creation (no update time).
    """

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
        return {
            "id": str(task["id"]),
            "url": task["url"],
            "content": task["content"],
            "description": "",
            "assignee": self.identity,
            "status": "completed" if task.get("is_completed") else "open",
            "updated": task["created_at"],
            "tz": due.get("timezone") or self._tz,
            "due": due.get("datetime") or due.get("date"),
            "project_scope": "personal",
        }


class AsanaSource(_Source):
    """Bytalos programme work only; `open_reader(credential_ref)` returns `(project_gids) -> tasks`. A task counts
    only when every project it sits in is approved; an approved plus any other project (or none) is ambiguous and
    rejected.
    """

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
            raise KeyError("projects")
        if not sitting & self._projects:
            return None
        if not sitting <= self._projects:
            raise KeyError("projects")
        if task.get("completed"):
            return None
        who = task.get("assignee") or {}
        return {
            "gid": str(task["gid"]),
            "url": task["permalink_url"],
            "name": task["name"],
            "notes": "",
            "assignee": who.get("gid", ""),
            "status": "open",
            "updated": task["modified_at"],
            "tz": self._tz,
            "due": task.get("due_at") or task.get("due_on"),
        }
