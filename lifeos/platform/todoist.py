"""Minimal READ-ONLY Todoist client (stdlib only). GET requests only; there is no write method."""

# Todoist API v1: a bearer token with data:read, `GET /tasks` paged by `next_cursor`. There is no write call and the
# single request path builds a GET (a test scans the source to keep it so). Errors are fixed codes, never a URL, token
# or body. A listing that cannot be proven complete raises.
import time
import urllib.parse
import urllib.request

from lifeos.platform import rest

API = "https://api.todoist.com/api/v1"
PAGE_SIZE = 200
MAX_PAGES = 20


class TodoistError(RuntimeError):
    """The message is always a fixed code."""


class Todoist:
    def __init__(self, token, timeout=30, sleep=time.sleep):
        if not isinstance(token, str) or not token.strip():
            raise TodoistError("TODOIST_CONFIG_INVALID")
        self._auth, self._timeout, self._sleep = "Bearer " + token.strip(), timeout, sleep

    def get(self, path, params=None):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")

        def build():
            return urllib.request.Request(
                url,
                method="GET",
                headers={"Authorization": self._auth, "Accept": "application/json", "User-Agent": "life-os-v7"},
            )

        return rest.call(
            build, TodoistError, "TODOIST", self._sleep, self._timeout, bad_json="BAD_RESPONSE", honor_retry_after=True
        )

    def tasks(self, limit):
        """Up to `limit` open tasks, following next_cursor. More than `limit` rows come back as limit rows (the caller treats that as truncated)."""
        out, cursor = [], None
        for _ in range(MAX_PAGES):
            page = self.get("/tasks", {"limit": PAGE_SIZE, **({"cursor": cursor} if cursor else {})})
            if (
                not isinstance(page, dict)
                or not isinstance(page.get("results"), list)
                or any(not isinstance(task, dict) for task in page["results"])
            ):
                raise TodoistError("TODOIST_LISTING_INCOMPLETE")
            out += page["results"]
            cursor = page.get("next_cursor")
            if not cursor or len(out) >= limit:
                return out[:limit]
            if not isinstance(cursor, str):
                raise TodoistError("TODOIST_LISTING_INCOMPLETE")
        raise TodoistError("TODOIST_LISTING_INCOMPLETE")


def top_level(rows):
    """The tasks a person counts as open actions: no subtasks (a parent counts once) and no `* ` header items (Todoist's
    non-task lines). Undated and future tasks count; a recurring task is one row. Pure: it never reads or changes the rows."""
    return [
        task
        for task in rows
        if not task.get("parent_id") and not str(task.get("content") or "").lstrip().startswith("* ")
    ]


__all__ = ["Todoist", "TodoistError", "top_level"]
