"""Creates a page under a page from Jim's Full Width template (D16.1): the API cannot set a page width.

Jim verified the rule on Oct 21. Notion applies the template after the create returns, so the caller writes its blocks once `WAIT` seconds have passed and the template's empty filler paragraph is gone."""
import time
from urllib.parse import quote

from lifeos.platform.notion_client import rich_text
from lifeos.platform.notion_ids import configured_id, notion_id

WAIT, FILLER_MAX = 8, 3
NAME = "FULL_WIDTH_TEMPLATE"


class FullWidthError(RuntimeError):
    """The fixed code is the message."""


def template_id():
    return notion_id(NAME)


def create(client, parent_id, title, emoji=None, wait=None):
    """Create the empty page `title` under the page `parent_id` from the Full Width template. -> the page id. Fails closed (no page made) when the template's id is not configured."""
    if not configured_id(NAME):
        raise FullWidthError("FULL_WIDTH_TEMPLATE_MISSING")
    body = {"parent": {"type": "page_id", "page_id": parent_id}, "properties": {"title": {"type": "title", "title": rich_text(title)}},
            "template": {"type": "template_id", "template_id": template_id()}}
    if emoji:
        body["icon"] = {"type": "emoji", "emoji": emoji}
    page = client.call_once("POST", "/pages", body)["id"]
    time.sleep(WAIT if wait is None else wait)
    clear_filler(client, page)
    return page


def clear_filler(client, page_id):
    """Delete the empty paragraphs a template leaves at the TOP of a page seconds old and machine made: no text, no children, never the last block. -> how many were removed."""
    top = (client.call("GET", f"/blocks/{quote(page_id, safe='')}/children?page_size=100") or {}).get("results") or []
    gone = 0
    while gone < FILLER_MAX and len(top) - gone > 1:
        block = top[gone]
        runs = (block.get("paragraph") or {}).get("rich_text") or []
        if block.get("type") != "paragraph" or block.get("has_children") or any((r.get("plain_text") or (r.get("text") or {}).get("content") or "").strip() for r in runs):
            break
        client.call("DELETE", f"/blocks/{quote(block['id'], safe='')}")
        gone += 1
    return gone
