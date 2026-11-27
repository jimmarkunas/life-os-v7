"""Notion API transport: paced under 3 req/s, retries 429/529 per Retry-After and 409 conflicts, fixed error codes."""

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
    """Fixed codes only - never tokens, ids, or response bodies."""

    detail = ""


_BODY_PATH = re.compile(r"body(?:\.[A-Za-z_]+|\[\d+\])+")


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read() or b"{}")
    except Exception:  # noqa:
        return ""
    if not isinstance(body, dict):
        return ""
    code = body.get("code") if re.fullmatch(r"[a-z_]{1,40}", str(body.get("code") or "")) else ""
    return " ".join(part for part in [code, *_BODY_PATH.findall(str(body.get("message") or ""))[:3]] if part)


def rich_text(content: str) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": {"content": piece}} for piece in _pieces(content, limits.NOTION_MAX_RICH_TEXT_CHARS)
    ][:100]


def _pieces(content: str, limit: int) -> list[str]:
    """Pieces of at most `limit` UTF-16 units (Notion counts units, not Python characters), never splitting a
    character."""
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
    def __init__(
        self,
        environ: Mapping[str, str] = os.environ,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        token_name: str = "NOTION_API_TOKEN",
        source_name: str = "NOTION_JOB_LEDGER_DATA_SOURCE_ID",
    ) -> None:
        self.token = (environ.get(token_name) or "").strip()
        self.source = (environ.get(source_name) or "").strip().replace("collection://", "")
        if not self.token or not self.source:
            raise NotionError("NOTION_CONFIG_MISSING")
        self._clock, self._sleep, self._jitter, self._last = clock, sleep, jitter, 0.0
        self.calls, self._gap = 0, limits.NOTION_GAP_SECONDS

    def call(self, method: str, path: str, body: Any = None) -> Any:
        return self._call(method, path, body, None)

    def call_once(
        self, method: str, path: str, body: Any = None, version: str | None = None, rate_retries: int | None = None
    ) -> Any:
        """Perform one request without retrying an operation with uncertain write outcome (a 5xx on a write is never
        repeated)."""
        if rate_retries is None and method == "POST":
            rate_retries = 0
        return self._call(method, path, body, rate_retries, version)

    def query_data_source(self, source_id: str | None = None, body: dict[str, Any] | None = None) -> Any:
        source = (source_id or self.source).strip().replace("collection://", "")
        if not source or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for ch in source
        ):
            raise NotionError("NOTION_SOURCE_INVALID")
        return self.call("POST", f"/data_sources/{source}/query", body or {"page_size": limits.NOTION_PAGE_SIZE})

    @staticmethod
    def _page_id(page_id: Any) -> str:
        value = str(page_id or "").replace("-", "")
        if len(value) != 32 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
            raise NotionError("NOTION_PAGE_ID_INVALID")
        return value

    def get_page(self, page_id: Any) -> Any:
        return self.call("GET", f"/pages/{self._page_id(page_id)}")

    def update_page_properties(self, page_id: Any, properties: dict[str, Any]) -> Any:
        """Issue one non-retried page-property write; callers must read back before any retry."""
        if not isinstance(properties, dict) or not properties:
            raise NotionError("NOTION_PROPERTIES_INVALID")
        return self.call_once("PATCH", f"/pages/{self._page_id(page_id)}", {"properties": properties})

    def update_block(self, block_id: Any, kind: str, rich: list[dict[str, Any]]) -> Any:
        """Replace the text of one heading, paragraph or callout block (one non-retried write; the caller reads back
        before any repeat)."""
        if kind not in ("heading_1", "heading_2", "heading_3", "paragraph", "callout") or not isinstance(rich, list):
            raise NotionError("NOTION_PROPERTIES_INVALID")
        return self.call_once("PATCH", f"/blocks/{self._page_id(block_id)}", {kind: {"rich_text": rich}})

    def _call(self, method: str, path: str, body: Any, rate_retries: int | None, version: str | None = None) -> Any:
        rate_retries = limits.NOTION_RATE_RETRIES if rate_retries is None else rate_retries
        rate, flaky, slept = 0, 0, 0.0
        for _ in range(1 + rate_retries + len(limits.NOTION_RETRY_BACKOFF)):
            wait = self._gap - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last, self.calls = self._clock(), self.calls + 1
            request = urllib.request.Request(
                API + path,
                method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Notion-Version": version or VERSION,
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read() or b"{}")
            except urllib.error.HTTPError as error:
                if error.code in (429, 529) and rate < rate_retries:
                    try:
                        advised = float(error.headers.get("Retry-After")) if error.headers.get("Retry-After") else None
                    except (TypeError, ValueError):
                        advised = None
                    pause = min(
                        advised if advised is not None else 2.0 * 2**rate, limits.NOTION_RETRY_AFTER_CAP_SECONDS
                    ) * (1 + self._jitter() / 2)
                    if (
                        advised is not None
                        and advised > limits.NOTION_RETRY_AFTER_CAP_SECONDS
                        or slept + pause > limits.NOTION_RATE_WAIT_BUDGET_SECONDS
                    ):
                        raise NotionError(f"NOTION_HTTP_{error.code}") from None
                    self._sleep(pause)
                    slept, rate = slept + pause, rate + 1
                    self._gap = min(self._gap * limits.NOTION_RATE_SLOWDOWN, limits.NOTION_GAP_MAX_SECONDS)
                    continue
                # A 409 was not applied, so any method may retry it; a 5xx may have been applied, so only GET and DELETE repeat.
                if flaky < len(limits.NOTION_RETRY_BACKOFF) and (
                    error.code == 409 or (error.code in (502, 503, 504) and method in limits.NOTION_IDEMPOTENT_METHODS)
                ):
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
        first, rest = blocks[: limits.NOTION_MAX_CHILD_BLOCKS], blocks[limits.NOTION_MAX_CHILD_BLOCKS :]
        page = self.call(
            "POST",
            "/pages",
            {
                "parent": {"type": "data_source_id", "data_source_id": self.source},
                "properties": props,
                "children": first,
            },
        )
        for start in range(0, len(rest), limits.NOTION_MAX_CHILD_BLOCKS):
            self.call(
                "PATCH",
                f"/blocks/{page['id']}/children",
                {"children": rest[start : start + limits.NOTION_MAX_CHILD_BLOCKS]},
            )
        return page["id"]
