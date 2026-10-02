"""Minimal Jira Cloud REST client (stdlib only, platform: no product knowledge). Every error is a fixed code: never a URL,
a response body or an issue field. Reads retry transient failures; a write retries only when the caller says it is idempotent."""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

TRANSIENT = (429, 500, 502, 503, 504)
MAX_PAGES = 40
PAGE_SIZE = 50
REQUIRED = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")


class JiraError(RuntimeError):
    """The message is always a fixed code."""


class Jira:
    def __init__(self, base_url, email, token, timeout=30, sleep=time.sleep):
        base = (base_url or "").strip().rstrip("/")
        if not base.startswith("https://") or not email or not token:
            raise JiraError("JIRA_CONFIG_INVALID")
        self._base, self._timeout, self._sleep = base, timeout, sleep
        self._auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    @classmethod
    def from_env(cls, environ=os.environ, **kwargs):
        missing = [name for name in REQUIRED if not (environ.get(name) or "").strip()]
        if missing:
            raise JiraError("JIRA_CONFIG_MISSING:" + ",".join(missing))      # setting names only, never values
        return cls(environ["JIRA_BASE_URL"], environ["JIRA_EMAIL"], environ["JIRA_API_TOKEN"], **kwargs)

    def request(self, method, path, params=None, payload=None, retry=None):
        retry = (method == "GET") if retry is None else retry
        url = self._base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Authorization": self._auth, "Accept": "application/json", "User-Agent": "life-os-v7"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        attempts = 4 if retry else 1
        for attempt in range(attempts):
            try:
                request = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    raw = response.read()
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as error:
                if error.code in TRANSIENT and attempt < attempts - 1:
                    self._sleep(2 ** (attempt + 1))
                    continue
                raise JiraError(f"JIRA_HTTP_{error.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt < attempts - 1:
                    self._sleep(2 ** (attempt + 1))
                    continue
                raise JiraError("JIRA_NETWORK") from None
            except ValueError:
                raise JiraError("JIRA_BAD_JSON") from None
        raise JiraError("JIRA_NETWORK")

    def get(self, path, params=None):
        return self.request("GET", path, params)

    def post(self, path, payload, retry=False):
        return self.request("POST", path, payload=payload, retry=retry)

    def pages(self, path, params=None, key="values"):
        """Every item of a paged Agile endpoint; bounded, and a page that makes no progress is an error."""
        out, start = [], 0
        for _ in range(MAX_PAGES):
            data = self.get(path, {**(params or {}), "startAt": start, "maxResults": PAGE_SIZE})
            items = data.get(key)
            if not isinstance(items, list):
                raise JiraError("JIRA_BAD_PAGE")
            out.extend(items)
            done = data.get("isLast", True) if key == "values" else len(out) >= int(data.get("total", len(out)))
            if done or not items:
                return out
            start += len(items)
        raise JiraError("JIRA_TOO_MANY_PAGES")

    # The small surface the Jira OS uses.
    def board(self, board_id):
        return self.get(f"/rest/agile/1.0/board/{int(board_id)}")

    def sprints(self, board_id, state):
        return self.pages(f"/rest/agile/1.0/board/{int(board_id)}/sprint", {"state": state})

    def sprint(self, sprint_id):
        return self.get(f"/rest/agile/1.0/sprint/{int(sprint_id)}")

    def sprint_issues(self, sprint_id, jql, fields):
        return self.pages(f"/rest/agile/1.0/sprint/{int(sprint_id)}/issue", {"jql": jql, "fields": fields}, "issues")

    def board_issues(self, board_id, jql, fields):
        return self.pages(f"/rest/agile/1.0/board/{int(board_id)}/issue", {"jql": jql, "fields": fields}, "issues")

    def set_sprint_state(self, sprint_id, state):
        return self.post(f"/rest/agile/1.0/sprint/{int(sprint_id)}", {"state": state}, retry=True)   # idempotent

    def create_sprint(self, board_id, name, start, end):
        return self.post("/rest/agile/1.0/sprint", {"name": name, "originBoardId": int(board_id),
                                                    "startDate": start.isoformat(), "endDate": end.isoformat()})

    def move_issues(self, sprint_id, keys):
        for index in range(0, len(keys), 50):
            self.post(f"/rest/agile/1.0/sprint/{int(sprint_id)}/issue", {"issues": keys[index:index + 50]}, retry=True)
