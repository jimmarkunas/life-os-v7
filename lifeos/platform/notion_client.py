"""Notion API transport (platform: free API only, paced under 3 req/s, 429 retry, 409 conflict retry). Fixed error codes, no job knowledge."""
from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from lifeos.platform import limits

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"


class NotionError(RuntimeError):
    """Fixed codes only - never tokens, ids, or response bodies. `detail` (when Notion refused a request) is Notion's own error code plus the
    request-body paths it names, e.g. "validation_error body.filter.and[4].or[1]": words from a fixed vocabulary, never free text from the message."""
    detail = ""


_BODY_PATH = re.compile(r"body(?:\.[A-Za-z_]+|\[\d+\])+")


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read() or b"{}")
    except Exception:                                                                        # noqa: BLE001 - diagnostics must never hide the real error
        return ""
    if not isinstance(body, dict):
        return ""
    code = body.get("code") if re.fullmatch(r"[a-z_]{1,40}", str(body.get("code") or "")) else ""
    return " ".join(part for part in [code, *_BODY_PATH.findall(str(body.get("message") or ""))[:3]] if part)



def rich_text(content: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": piece}} for piece in _pieces(content, limits.NOTION_MAX_RICH_TEXT_CHARS)][:100]


def _pieces(content: str, limit: int) -> list[str]:
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
    def __init__(self, environ: Mapping[str, str] = os.environ, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 jitter: Callable[[], float] = random.random, token_name: str = "NOTION_API_TOKEN", source_name: str = "NOTION_JOB_LEDGER_DATA_SOURCE_ID") -> None:
        self.token = (environ.get(token_name) or "").strip()
        self.source = (environ.get(source_name) or "").strip().replace("collection://", "")
        if not self.token or not self.source:
            raise NotionError("NOTION_CONFIG_MISSING")
        self._clock, self._sleep, self._jitter, self._last = clock, sleep, jitter, 0.0
        self.calls = 0

    def call(self, method: str, path: str, body: Any = None) -> Any:
        return self._call(method, path, body, 3)

    def call_once(self, method: str, path: str, body: Any = None, version: str | None = None, rate_retries: int = 0) -> Any:
        """Perform one request without retrying an operation with uncertain write outcome. `version` overrides the pinned API version for this one call.
        `rate_retries` (LIFE-661): how many times to repeat it after a 429, which Notion answers before applying anything, so it is safe for any write; 0 (the default) never repeats it."""
        return self._call(method, path, body, rate_retries, version)

    def query_data_source(self, source_id: str | None = None, body: dict[str, Any] | None = None) -> Any:
        """Read one data-source page through the shared pacing and retry path."""
        source = (source_id or self.source).strip().replace("collection://", "")
        if not source or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for ch in source):
            raise NotionError("NOTION_SOURCE_INVALID")
        return self.call("POST", f"/data_sources/{source}/query", body or {"page_size": limits.NOTION_PAGE_SIZE})

    @staticmethod
    def _page_id(page_id: Any) -> str:
        value = str(page_id or "").replace("-", "")
        if len(value) != 32 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
            raise NotionError("NOTION_PAGE_ID_INVALID")
        return value

    def get_page(self, page_id: Any) -> Any:
        """Read one exact page through the paced Notion client."""
        return self.call("GET", f"/pages/{self._page_id(page_id)}")

    def update_page_properties(self, page_id: Any, properties: dict[str, Any]) -> Any:
        """Issue one non-retried page-property write; callers must read back before any retry."""
        if not isinstance(properties, dict) or not properties:
            raise NotionError("NOTION_PROPERTIES_INVALID")
        return self.call_once("PATCH", f"/pages/{self._page_id(page_id)}", {"properties": properties})

    def _call(self, method: str, path: str, body: Any, rate_retries: int, version: str | None = None) -> Any:
        rate = flaky = 0
        for _ in range(1 + rate_retries + len(limits.NOTION_RETRY_BACKOFF)):
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
                if error.code == 429 and rate < rate_retries:
                    rate += 1                                                                  # jitter (LIFE-661): the parallel domain jobs share one integration, so they must not retry in step
                    self._sleep(min(float(error.headers.get("Retry-After") or 2), 30) * (1 + self._jitter() / 2))
                    continue
                if flaky < len(limits.NOTION_RETRY_BACKOFF) and (error.code == 409 or (error.code in (502, 503, 504) and method == "GET")):
                    # LIFE-654: a 409 means another request was editing the same page and this one was NOT applied, so any method may retry it, even `call_once`.
                    # A 5xx may have been applied, so only a read repeats it; every write that reaches here is read back by its caller before any repeat.
                    self._sleep(limits.NOTION_RETRY_BACKOFF[flaky] * (1 + self._jitter()))
                    flaky += 1
                    continue
                refused = NotionError(f"NOTION_HTTP_{error.code}")
                refused.detail = _detail(error)
                raise refused from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                raise NotionError("NOTION_NETWORK") from None
        raise NotionError("NOTION_RATE_LIMITED")

    def create(self, props: dict[str, Any], blocks: list[dict[str, Any]]) -> str:
        first, rest = blocks[:limits.NOTION_MAX_CHILD_BLOCKS], blocks[limits.NOTION_MAX_CHILD_BLOCKS:]
        page = self.call("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": self.source},
                                            "properties": props, "children": first})
        for start in range(0, len(rest), limits.NOTION_MAX_CHILD_BLOCKS):
            self.call("PATCH", f"/blocks/{page['id']}/children",
                      {"children": rest[start:start + limits.NOTION_MAX_CHILD_BLOCKS]})
        return page["id"]
