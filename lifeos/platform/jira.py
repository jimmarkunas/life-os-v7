"""Minimal Jira Cloud REST client (stdlib only; platform: no product knowledge).

Every error is a fixed code: never a URL, a response body or an issue field. Reads retry transient failures; a write retries only when the caller says it is idempotent."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from lifeos.platform import rest

MAX_PAGES = 40
PAGE_SIZE = 50
REQUIRED = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")


class JiraError(RuntimeError):
    """The message is always a fixed code."""


class Jira:
    def __init__(self, base_url: str, email: str, token: str, timeout: float = 30, sleep: Callable[[float], None] = time.sleep) -> None:
        base = (base_url or "").strip().rstrip("/")
        if not base.startswith("https://") or not email or not token:
            raise JiraError("JIRA_CONFIG_INVALID")
        self._base, self._timeout, self._sleep = base, timeout, sleep
        self._auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ, **kwargs: Any) -> Jira:
        missing = [name for name in REQUIRED if not (environ.get(name) or "").strip()]
        if missing:
            raise JiraError("JIRA_CONFIG_MISSING:" + ",".join(missing))      # setting names only, never values
        return cls(environ["JIRA_BASE_URL"], environ["JIRA_EMAIL"], environ["JIRA_API_TOKEN"], **kwargs)

    def request(self, method: str, path: str, params: dict[str, Any] | None = None, payload: Any = None, retry: bool | None = None) -> Any:
        retry = (method == "GET") if retry is None else retry
        url = self._base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Authorization": self._auth, "Accept": "application/json", "User-Agent": "life-os-v7"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        build = lambda: urllib.request.Request(url, data=data, headers=headers, method=method)
        return rest.call(build, JiraError, "JIRA", self._sleep, self._timeout, attempts=4 if retry else 1)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, payload: Any, retry: bool = False) -> Any:
        return self.request("POST", path, payload=payload, retry=retry)

    def pages(self, path: str, params: dict[str, Any] | None = None, key: str = "values", meta: dict[str, Any] | None = None) -> list[Any]:
        """Every item of a paged Agile endpoint; bounded, and a page that makes no progress is an error. `meta`, when given, receives {"pages", "items", "total", "complete"} (counts and booleans only; LIFE-918 diagnostics)."""
        out, start, count = [], 0, 0
        for _ in range(MAX_PAGES):
            data = self.get(path, {**(params or {}), "startAt": start, "maxResults": PAGE_SIZE})
            items = data.get(key)
            if not isinstance(items, list):
                raise JiraError("JIRA_BAD_PAGE")
            out.extend(items)
            count += 1
            done = data.get("isLast", True) if key == "values" else len(out) >= int(data.get("total", len(out)))
            if meta is not None:
                total = data.get("total")
                meta.update({"pages": count, "items": len(out), "total": total if type(total) is int else None, "complete": bool(done or not items)})
            if done or not items:
                return out
            start += len(items)
        raise JiraError("JIRA_TOO_MANY_PAGES")

    # The small surface the Jira OS uses.
    def board(self, board_id: int | str) -> Any:
        return self.get(f"/rest/agile/1.0/board/{int(board_id)}")

    def sprints(self, board_id: int | str, state: str) -> list[Any]:
        return self.pages(f"/rest/agile/1.0/board/{int(board_id)}/sprint", {"state": state})

    def sprint(self, sprint_id: int | str) -> Any:
        return self.get(f"/rest/agile/1.0/sprint/{int(sprint_id)}")

    def sprint_issues(self, sprint_id: int | str, jql: str, fields: str, meta: dict[str, Any] | None = None) -> list[Any]:
        return self.pages(f"/rest/agile/1.0/sprint/{int(sprint_id)}/issue", {**({"jql": jql} if jql else {}), "fields": fields}, "issues", meta)

    def board_issues(self, board_id: int | str, jql: str, fields: str) -> list[Any]:
        return self.pages(f"/rest/agile/1.0/board/{int(board_id)}/issue", {"jql": jql, "fields": fields}, "issues")

    def set_sprint_state(self, sprint_id: int | str, state: str) -> Any:
        return self.post(f"/rest/agile/1.0/sprint/{int(sprint_id)}", {"state": state}, retry=True)   # idempotent

    def create_sprint(self, board_id: int | str, name: str, start: datetime, end: datetime) -> Any:
        return self.post("/rest/agile/1.0/sprint", {"name": name, "originBoardId": int(board_id),
                                                    "startDate": start.isoformat(), "endDate": end.isoformat()})

    # LIFE-918: partial sprint update (name only). UNVERIFIED against the live tenant; nothing calls it live. The daily reconciliation takes its writes as injected objects and refuses a live run without them.
    def rename_sprint(self, sprint_id: int | str, name: str) -> Any:
        return self.post(f"/rest/agile/1.0/sprint/{int(sprint_id)}", {"name": name}, retry=True)   # idempotent

    def move_issues(self, sprint_id: int | str, keys: list[str]) -> None:
        for index in range(0, len(keys), 50):
            reply = self.post(f"/rest/agile/1.0/sprint/{int(sprint_id)}/issue", {"issues": keys[index:index + 50]}, retry=True)
            self.last_write = {"ok": True, "body_empty": not reply}               # a non-2xx raises; LIFE-918 diagnostics read this, counts and booleans only

    # LIFE-915: the Jira Agile backlog primitive. UNVERIFIED against the live tenant (permissions, board semantics, returned shape): nothing calls it live. run() never passes it; the gated MEJ
    # rollover only uses an object like this one that a test injects, and refuses to close a sprint with unapproved work and no such object. LIFE-918 proves the live contract before any activation.
    def move_to_backlog(self, keys: list[str]) -> None:
        for index in range(0, len(keys), 50):
            reply = self.post("/rest/agile/1.0/backlog/issue", {"issues": keys[index:index + 50]}, retry=True)
            self.last_write = {"ok": True, "body_empty": not reply}

    def backlog_issues(self, board_id: int | str, jql: str, fields: str, meta: dict[str, Any] | None = None) -> list[Any]:
        return self.pages(f"/rest/agile/1.0/board/{int(board_id)}/backlog", {"jql": jql, "fields": fields}, "issues", meta)

    # LIFE-918 (live writers; every one UNVERIFIED against the tenant until the throwaway-sprint check passes and JIRA_WRITERS_VERIFIED is "yes"). Nothing here runs without lifeos.jira.writers.
    def put(self, path: str, payload: Any, retry: bool = False) -> Any:
        return self.request("PUT", path, payload=payload, retry=retry)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path, retry=False)

    def issue(self, key: str, fields: str) -> Any:
        return self.get(f"/rest/api/3/issue/{urllib.parse.quote(str(key), safe='')}", {"fields": fields})

    def create_issue(self, fields: dict[str, Any]) -> Any:
        return self.post("/rest/api/3/issue", {"fields": fields})

    def edit_issue(self, key: str, fields: dict[str, Any] | None = None, update: dict[str, Any] | None = None) -> Any:
        return self.put(f"/rest/api/3/issue/{urllib.parse.quote(str(key), safe='')}", {**({"fields": fields} if fields else {}), **({"update": update} if update else {})}, retry=True)   # idempotent

    def add_comment(self, key: str, text: str) -> Any:
        body = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}
        return self.post(f"/rest/api/3/issue/{urllib.parse.quote(str(key), safe='')}/comment", {"body": body})

    def link_issues(self, link_type: str, inward_key: str, outward_key: str) -> Any:
        return self.post("/rest/api/3/issueLink", {"type": {"name": link_type}, "inwardIssue": {"key": inward_key}, "outwardIssue": {"key": outward_key}})

    def link_types(self) -> list[Any]:
        return (self.get("/rest/api/3/issueLinkType") or {}).get("issueLinkTypes") or []

    def my_permissions(self, project_key: str, names: Any) -> dict[str, Any]:
        """GET only: which of `names` the calling user holds in one project. -> {name: {"havePermission": bool, ...}}."""
        return (self.get("/rest/api/3/mypermissions", {"projectKey": project_key, "permissions": ",".join(names)}) or {}).get("permissions") or {}

    def field_list(self) -> list[Any]:
        return self.get("/rest/api/3/field") or []

    def transitions(self, key: str) -> list[Any]:
        return (self.get(f"/rest/api/3/issue/{urllib.parse.quote(str(key), safe='')}/transitions") or {}).get("transitions") or []

    def transition(self, key: str, transition_id: str) -> Any:
        return self.post(f"/rest/api/3/issue/{urllib.parse.quote(str(key), safe='')}/transitions", {"transition": {"id": str(transition_id)}})

    def delete_sprint(self, sprint_id: int | str) -> Any:
        return self.delete(f"/rest/agile/1.0/sprint/{int(sprint_id)}")

    def delete_issue(self, key: str) -> Any:
        return self.delete(f"/rest/api/3/issue/{urllib.parse.quote(str(key), safe='')}")

