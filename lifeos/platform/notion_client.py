"""Notion API transport (platform: free API only, paced under 3 req/s, 429 retry). Fixed error codes, no job knowledge."""
import json
import os
import re
import time
import urllib.error
import urllib.request

from lifeos.platform import limits

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"


class NotionError(RuntimeError):
    """Fixed codes only - never tokens, ids, or response bodies. `detail` (when Notion refused a request) is Notion's own error code plus the
    request-body paths it names, e.g. "validation_error body.filter.and[4].or[1]": words from a fixed vocabulary, never free text from the message."""
    detail = ""


_BODY_PATH = re.compile(r"body(?:\.[A-Za-z_]+|\[\d+\])+")


def _detail(error):
    try:
        body = json.loads(error.read() or b"{}")
    except Exception:                                                                        # noqa: BLE001 - diagnostics must never hide the real error
        return ""
    if not isinstance(body, dict):
        return ""
    code = body.get("code") if re.fullmatch(r"[a-z_]{1,40}", str(body.get("code") or "")) else ""
    return " ".join(part for part in [code, *_BODY_PATH.findall(str(body.get("message") or ""))[:3]] if part)



def rich_text(content):
    return [{"type": "text", "text": {"content": piece}} for piece in _pieces(content, limits.NOTION_MAX_RICH_TEXT_CHARS)][:100]


def _pieces(content, limit):
    """Pieces of at most `limit` UTF-16 units (Notion counts units, not Python characters: a character outside the basic plane is two), never splitting a character. An empty text is one empty piece."""
    pieces, start, units = [], 0, 0
    for index, char in enumerate(content):
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > limit:
            pieces.append(content[start:index])
            start, units = index, 0
        units += width
    pieces.append(content[start:])
    return pieces


class Client:
    def __init__(self, environ=os.environ, clock=time.monotonic, sleep=time.sleep,
                 token_name="NOTION_API_TOKEN", source_name="NOTION_JOB_LEDGER_DATA_SOURCE_ID"):
        self.token = (environ.get(token_name) or "").strip()
        self.source = (environ.get(source_name) or "").strip().replace("collection://", "")
        if not self.token or not self.source:
            raise NotionError("NOTION_CONFIG_MISSING")
        self._clock, self._sleep, self._last = clock, sleep, 0.0
        self.calls = 0

    def call(self, method, path, body=None):
        return self._call(method, path, body, 4)

    def call_once(self, method, path, body=None, version=None):
        """Perform one request without retrying an operation with uncertain write outcome. `version` overrides the pinned API version for this one call."""
        return self._call(method, path, body, 1, version)

    def query_data_source(self, source_id=None, body=None):
        """Read one data-source page through the shared pacing and retry path."""
        source = (source_id or self.source).strip().replace("collection://", "")
        if not source or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for ch in source):
            raise NotionError("NOTION_SOURCE_INVALID")
        return self.call("POST", f"/data_sources/{source}/query", body or {"page_size": limits.NOTION_PAGE_SIZE})

    @staticmethod
    def _page_id(page_id):
        value = str(page_id or "").replace("-", "")
        if len(value) != 32 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
            raise NotionError("NOTION_PAGE_ID_INVALID")
        return value

    def get_page(self, page_id):
        """Read one exact page through the paced Notion client."""
        return self.call("GET", f"/pages/{self._page_id(page_id)}")

    def update_page_properties(self, page_id, properties):
        """Issue one non-retried page-property write; callers must read back before any retry."""
        if not isinstance(properties, dict) or not properties:
            raise NotionError("NOTION_PROPERTIES_INVALID")
        return self.call_once("PATCH", f"/pages/{self._page_id(page_id)}", {"properties": properties})

    def _call(self, method, path, body, attempts, version=None):
        for attempt in range(attempts):
            wait = limits.NOTION_GAP_SECONDS - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last, self.calls = self._clock(), self.calls + 1
            request = urllib.request.Request(
                API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                headers={"Authorization": f"Bearer {self.token}", "Notion-Version": version or VERSION,
                         "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read() or b"{}")
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < attempts - 1:
                    self._sleep(min(float(error.headers.get("Retry-After") or 2), 30))
                    continue
                refused = NotionError(f"NOTION_HTTP_{error.code}")
                refused.detail = _detail(error)
                raise refused from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                raise NotionError("NOTION_NETWORK") from None
        raise NotionError("NOTION_RATE_LIMITED")

    def create(self, props, blocks):
        first, rest = blocks[:limits.NOTION_MAX_CHILD_BLOCKS], blocks[limits.NOTION_MAX_CHILD_BLOCKS:]
        page = self.call("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": self.source},
                                            "properties": props, "children": first})
        for start in range(0, len(rest), limits.NOTION_MAX_CHILD_BLOCKS):
            self.call("PATCH", f"/blocks/{page['id']}/children",
                      {"children": rest[start:start + limits.NOTION_MAX_CHILD_BLOCKS]})
        return page["id"]
