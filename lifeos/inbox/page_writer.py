"""LIFE-711: writes the Action Inbox view into Jim's approved page: the five tiles, the list's owned rows, the Followup table, the Source health table and the last-refreshed line."""
# Jim, Nov 16 2026: a status dashboard with the list under it, as a full-width page inside the callout, QA9 look, live data. The whole page body belongs to this writer and carries no marker, header or
# explanatory text: SHAPE is the one expected block order (the five tiles, the inline list database, the Followup heading and table, the Review Needed line, the Source health heading and table, the
# last-refreshed line, and at the very end the Truly empty / Cannot say callout only while it applies). The writer changes the words of those blocks and the owned rows of the list; it never recreates the list,
# its views, or any block, and a page whose blocks are not exactly that shape is refused (`page_shape_changed`) and left alone, so a failed run leaves the previous page.
#
# Safety: it reads the page's block tree first and may PATCH or DELETE only a block id it read from that page (anything else is `write_outside_page`, nothing written); the page is found by its title and
# its shape, never by a committed id (`page_not_found`, `page_ambiguous`); it is idempotent (the same words write nothing). Counts and fixed codes only.
import os

from lifeos.inbox import page_rows, page_view
from lifeos.inbox.model import InboxError
from lifeos.platform.notion_client import Client, NotionError

TITLE = "Agent Inbox"
SHAPE = ("column_list", "child_database", "heading_3", "table", "paragraph", "heading_3", "table", "paragraph")
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

    def __init__(self, client, page_id):
        self.client, self.page_id, self.allowed, self.writes = client, page_id, {page_id}, 0
        body = _children(client, page_id)
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
    """Write `view` (page_view.render) into the page's blocks and the list's rows. -> counts. Every tile is the same two blocks (the number, the label): only the number is ever written."""
    columns, listing, _, followup, review, _, health, refreshed = page.body
    text = 0
    for column, number in zip(_kids(page, columns, 5), [*view["tiles"], view["healthy"]]):
        text += page.set_text(_kids(page, _kids(page, column, 1)[0], 2)[0], [(number, False, False)])
    rows = page.replace_rows(health, view["health"]) + page.replace_rows(followup, view["followup"])
    text += page.set_text(review, [(view["review"], False, True)]) + page.set_text(refreshed, [(view["refreshed"], False, True)])
    page._guard(listing["id"])
    listed = page_rows.reconcile(page.client, listing["id"], view["rows"], {w.lower() for w in view["clean"]})
    page.writes += sum(listed.values())
    states = page.set_state(view["state"])
    return {"text_blocks_written": text, "table_rows_written": rows, "list_rows_created": listed["created"], "list_rows_updated": listed["updated"], "list_rows_archived": listed["archived"],
            "state_callouts_changed": states, "unchanged": int(page.writes == 0)}


def locate(client):
    """The page's id: the one page titled `Agent Inbox` whose blocks are exactly SHAPE (plus the optional state callout). None or several is a refusal; no id is ever committed."""
    found = client.call("POST", "/search", {"query": TITLE, "filter": {"property": "object", "value": "page"}, "page_size": 20}).get("results", [])
    named = [r["id"] for r in found if "".join(t.get("plain_text", "") for t in (r.get("properties", {}).get("title") or {}).get("title", [])) == TITLE]
    holders = []
    for page_id in named:
        kinds = tuple(b.get("type") for b in _children(client, page_id))
        if kinds[:len(SHAPE)] == SHAPE and len(kinds) in (len(SHAPE), len(SHAPE) + 1):
            holders.append(page_id)
    if len(holders) != 1:
        raise InboxError("page_not_found" if not holders else "page_ambiguous")
    return holders[0]


def _client(env):
    token = (env.get("NOTION_API_TOKEN") or "").strip()
    if not token:
        raise InboxError("view_config_missing")
    return Client({"NOTION_API_TOKEN": token, "NOTION_INBOX_VIEW_SOURCE": "0" * 32}, source_name="NOTION_INBOX_VIEW_SOURCE")


def publish(payloads, now, environ=None, client=None, page_id=None):
    """-> counts only; raises InboxError with a fixed reason word. The one entry point: read the page, render the words from the stored payloads, write what differs."""
    env = os.environ if environ is None else environ
    try:
        client = client or _client(env)
        return apply(Page(client, page_id or locate(client)), page_view.render(payloads, now))
    except NotionError as error:
        raise InboxError(f"view_notion_{str(error).lower()[:40]}") from None


__all__ = ["SHAPE", "TITLE", "Page", "apply", "locate", "publish"]
