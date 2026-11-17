"""LIFE-711: writes the Action Inbox view into the blocks of Jim's approved page. The writer owns the blocks below one marker line and nothing else."""
# Jim, Nov 16 2026: build the QA9 mockup as a full-width page inside the callout, live data in the same blocks, no database. The page (PAGE_ID) was laid out once with the QA9 blocks below MARKER (SHAPE: a
# header callout, five tiles in columns, the main table, a note, the six-source health callout, the Followup mapping callout, the Followup table and the Review Needed line). This writer changes only the
# words in those blocks, so every icon, colour and layout stays as it was built. The Truly empty / Cannot say callout is the one block it adds and removes, at the very end, only when it applies.
#
# Safety: it reads the page's block tree first and may PATCH or DELETE only a block id it read below the marker (anything else is `write_outside_page`, nothing written); a page whose blocks no longer
# have the expected shape is refused (`page_shape_changed`) and left alone; it is idempotent (the same words write nothing); the title and every other page are never touched. Counts and fixed codes only.
import os

from lifeos.inbox import page_view
from lifeos.inbox.model import InboxError
from lifeos.platform.notion_client import Client, NotionError

PAGE_ID = "41b3c5a0592680caac4bda7b06b9d767"
MARKER = "— machine-written below this line (V7, LIFE-711): do not hand-edit —"
SHAPE = ("callout", "column_list", "table", "paragraph", "divider", "callout", "divider", "callout", "table", "paragraph")
STATE_ICON = {"Truly empty": ("📭", "gray_bg"), "Cannot say": ("⚠️", "red_bg")}


def _plain(rich):
    return "".join(piece.get("plain_text", "") for piece in rich or ())


def _text(content, bold=False, gray=False):
    return {"type": "text", "text": {"content": content}, "annotations": {"bold": bold, "color": "gray" if gray else "default"}}


def _children(client, block_id):
    out, cursor = [], None
    while True:
        page = client.call("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        out += page.get("results", [])
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")


def _rich(block):
    return (block.get(block.get("type")) or {}).get("rich_text") or []


class Page:
    """The page's tree below the marker, with the guarded write calls."""

    def __init__(self, client, page_id=PAGE_ID):
        self.client, self.page_id, self.allowed, self.writes = client, page_id, {page_id}, 0
        top = _children(client, page_id)
        at = next((i for i, b in enumerate(top) if b.get("type") == "paragraph" and _plain(_rich(b)) == MARKER), None)
        if at is None:
            raise InboxError("marker_missing")
        body = top[at + 1:]
        self.state_block = body[len(SHAPE)] if len(body) == len(SHAPE) + 1 and body[-1].get("type") == "callout" else None
        self.body = body[:len(SHAPE)]
        if tuple(b.get("type") for b in self.body) != SHAPE or len(body) not in (len(SHAPE), len(SHAPE) + 1) or (len(body) == len(SHAPE) + 1 and self.state_block is None):
            raise InboxError("page_shape_changed")
        self.allowed |= {b["id"] for b in body}
        self.tree = {}
        for block in self.body:
            if block.get("has_children"):
                self._load(block)

    def _load(self, block):
        kids = _children(self.client, block["id"])
        self.allowed |= {k["id"] for k in kids}
        self.tree[block["id"]] = kids
        for kid in kids:
            if kid.get("has_children"):
                self._load(kid)

    def _guard(self, block_id):
        if block_id not in self.allowed:
            raise InboxError("write_outside_page")

    def set_text(self, block, pieces):
        """Replace the words of one heading or paragraph block when they differ; `pieces` are (text, bold, gray) tuples."""
        self._guard(block["id"])
        if _plain(_rich(block)) == "".join(piece[0] for piece in pieces):
            return 0
        self.client.call_once("PATCH", f"/blocks/{block['id']}", {block["type"]: {"rich_text": [_text(*piece) for piece in pieces]}})
        self.writes += 1
        return 1

    def replace_rows(self, table, rows):
        """The table's data rows (everything after the header) become `rows`: new rows are appended first, then the old ones removed, so the table is never empty."""
        self._guard(table["id"])
        old = self.tree.get(table["id"], [])[1:]
        have = [tuple(_plain(cell) for cell in (row.get("table_row") or {}).get("cells", ())) for row in old]
        if have == [tuple(r) for r in rows]:
            return 0
        cells = lambda row: [[_text(str(value), bold=(i == 0 and value != "—"))] for i, value in enumerate(row)]
        self.client.call_once("PATCH", f"/blocks/{table['id']}/children", {"children": [{"object": "block", "type": "table_row", "table_row": {"cells": cells(r)}} for r in rows]})
        for row in old:
            self._guard(row["id"])
            self.client.call("DELETE", f"/blocks/{row['id']}")
        self.writes += len(rows) + len(old)
        return len(rows)

    def set_state(self, state):
        """The Truly empty / Cannot say callout at the very end: added when it applies, replaced when it changes, removed when it no longer applies."""
        wanted = None if state is None else f"{state[0]}. {state[1]}"
        have = None if self.state_block is None else _plain(_rich(self.state_block))
        if wanted == have:
            return 0
        if self.state_block is not None:
            self._guard(self.state_block["id"])
            self.client.call("DELETE", f"/blocks/{self.state_block['id']}")
            self.writes += 1
        if state is not None:
            icon, color = STATE_ICON[state[0]]
            self._guard(self.page_id)
            self.client.call_once("PATCH", f"/blocks/{self.page_id}/children", {"children": [{"object": "block", "type": "callout", "callout": {
                "rich_text": [_text(state[0] + ". ", bold=True), _text(state[1])], "icon": {"type": "emoji", "emoji": icon}, "color": color}}]})
            self.writes += 1
        return 1


def _kids(page, block, count):
    kids = page.tree.get(block["id"], [])
    if len(kids) != count:
        raise InboxError("page_shape_changed")
    return kids


def apply(page, view):
    """Write `view` (page_view.render) into the page's blocks. -> counts."""
    header, columns, main, _, _, health, _, _, followup, review = page.body
    text = page.set_text(_kids(page, header, 3)[2], [(view["refreshed"], False, True)])
    cols = _kids(page, columns, 5)
    for column, (number, caption) in zip(cols, [*view["tiles"], view["healthy"]]):
        inner = _kids(page, _kids(page, column, 1)[0], 3)
        text += page.set_text(inner[0], [(number, False, False)]) + page.set_text(inner[2], [(caption, False, True)])
    rows = page.replace_rows(main, view["main"]) + page.replace_rows(followup, view["followup"])
    text += page.set_text(_kids(page, health, 3)[1], page_view.source_pieces(view))
    text += page.set_text(review, [(view["review"], False, True)])
    states = page.set_state(view["state"])
    return {"text_blocks_written": text, "table_rows_written": rows, "state_callouts_changed": states, "unchanged": int(page.writes == 0)}


def _client(env):
    token = (env.get("NOTION_API_TOKEN") or "").strip()
    if not token:
        raise InboxError("view_config_missing")
    return Client({"NOTION_API_TOKEN": token, "NOTION_INBOX_VIEW_SOURCE": "0" * 32}, source_name="NOTION_INBOX_VIEW_SOURCE")


def publish(payloads, now, environ=None, client=None, page_id=PAGE_ID):
    """-> counts only; raises InboxError with a fixed reason word. The one entry point: read the page, render the words from the stored payloads, write what differs."""
    env = os.environ if environ is None else environ
    try:
        return apply(Page(client or _client(env), page_id), page_view.render(payloads, now))
    except NotionError as error:
        raise InboxError(f"view_notion_{str(error).lower()[:40]}") from None


__all__ = ["MARKER", "PAGE_ID", "SHAPE", "Page", "apply", "publish"]
