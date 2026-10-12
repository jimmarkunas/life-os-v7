"""The date and time line under the Daily Report's title (D183), written by V7.

The line reads `Monday, Oct 12 · Updated 2:15 PM CT` and is refreshed by the Command Center job, which runs last in every tick. V7 finds the page from the Command Center callout's
own block id (no new secret), looks for its line among the page's top-level blocks (a paragraph that matches the fixed shape), updates that one block, and creates it at the very
top of the page the first time. It is created only when no line of that shape exists anywhere on the page, and a line that is somewhere other than the top, or two of them, stop
the write (a fixed code, nothing created), so a Notion that ignored the position can never fill the page with copies. Every write is read back. Counts and fixed codes only."""
import re
from urllib.parse import quote

from lifeos.platform import report_region
from lifeos.platform.notion_client import NotionError, rich_text

LINE = re.compile(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [A-Z][a-z]{2} \d{1,2} · Updated \d{1,2}:\d{2} [AP]M CT$")
NEAR_TOP = 3                                              # the line is the first block under the title; a little room for a block a person adds above it


def text(now):
    return f"{now.strftime('%A, %b')} {now.day} · Updated {now.strftime('%-I:%M %p')} CT"


def _paragraph(content):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(content)}}


def run(client, block_id, now, live, fail):
    """-> "created" | "updated" | "unchanged" (or "would_create" | "would_update" in a dry run). Raises fail(code) with a fixed code."""
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    page_id = report_region.page_of(client, meta, fail)
    top = report_region.children(client, page_id, fail)
    mine = [(index, block) for index, block in enumerate(top) if block.get("type") == "paragraph" and LINE.match(report_region.plain(block).strip())]
    wanted = text(now)
    if len(mine) > 1:
        raise fail("HEADER_AMBIGUOUS")
    if mine:
        index, block = mine[0]
        if index >= NEAR_TOP:
            raise fail("HEADER_MISPLACED")
        if report_region.plain(block).strip() == wanted:
            return "unchanged"
        if not live:
            return "would_update"
        client.call_once("PATCH", f"/blocks/{quote(block['id'], safe='')}", {"paragraph": {"rich_text": rich_text(wanted)}})
        after = client.call("GET", f"/blocks/{quote(block['id'], safe='')}")
        if not isinstance(after, dict) or after.get("type") != "paragraph" or report_region.plain(after).strip() != wanted:
            raise fail("HEADER_VERIFY_FAILED")
        return "updated"
    if not live:
        return "would_create"
    result = client.call_once("PATCH", f"/blocks/{quote(page_id, safe='')}/children", {"children": [_paragraph(wanted)], "position": {"type": "start"}})
    made = [b for b in (result.get("results") if isinstance(result, dict) else None) or [] if isinstance(b, dict) and b.get("type") == "paragraph" and report_region.plain(b).strip() == wanted]
    after = report_region.children(client, page_id, fail)
    if not (after and after[0].get("type") == "paragraph" and report_region.plain(after[0]).strip() == wanted and len(after) == len(top) + 1):
        for stray in made[:1]:                            # the position was not honoured: take the new block back out rather than leave a copy below
            try:
                client.call("DELETE", f"/blocks/{quote(stray['id'], safe='')}")
            except NotionError:
                pass
        raise fail("HEADER_VERIFY_FAILED")
    return "created"
