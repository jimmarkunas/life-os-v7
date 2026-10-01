"""Notion API transport (platform: free API only, paced under 3 req/s, 429 retry). Fixed error codes, no job knowledge."""
import json
import os
import time
import urllib.error
import urllib.request

from lifeos.platform import limits

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"


class NotionError(RuntimeError):
    """Fixed codes only - never tokens, ids, or response bodies."""



def rich_text(content):
    return [{"type": "text", "text": {"content": content[i:i + limits.NOTION_MAX_RICH_TEXT_CHARS]}}
            for i in range(0, max(len(content), 1), limits.NOTION_MAX_RICH_TEXT_CHARS)][:100]


class Client:
    def __init__(self, environ=os.environ, clock=time.monotonic, sleep=time.sleep):
        self.token = (environ.get("NOTION_API_TOKEN") or "").strip()
        self.source = (environ.get("NOTION_JOB_LEDGER_DATA_SOURCE_ID") or "").strip().replace("collection://", "")
        if not self.token or not self.source:
            raise NotionError("NOTION_CONFIG_MISSING")
        self._clock, self._sleep, self._last = clock, sleep, 0.0
        self.calls = 0

    def call(self, method, path, body=None):
        for attempt in range(4):
            wait = limits.NOTION_GAP_SECONDS - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last, self.calls = self._clock(), self.calls + 1
            request = urllib.request.Request(
                API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                headers={"Authorization": f"Bearer {self.token}", "Notion-Version": VERSION,
                         "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read() or b"{}")
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 3:
                    self._sleep(min(float(error.headers.get("Retry-After") or 2), 30))
                    continue
                raise NotionError(f"NOTION_HTTP_{error.code}") from None
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
