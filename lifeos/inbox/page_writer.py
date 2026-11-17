"""LIFE-711: writes the Action Inbox view into Jim's approved page: the five tiles, the list's owned rows, the System health rows and the last-refreshed line."""
# Jim, Nov 16 2026: a status dashboard with the list under it, as a full-width page inside the callout, QA9 look, live data. The page body is, in order: the five tiles, a divider, the list (an inline database,
# possibly wrapped in Jim's callout), a divider, the System health database (same), one last-refreshed line, and at the very end the Truly empty / Cannot say callout only while it applies. The writer
# changes only the tiles' numbers, the owned rows of the two databases and the last-refreshed words. It never creates, recreates or removes the databases, their views, Jim's callouts or the dividers.
#
# Safety: it reads the page's block tree first and may PATCH or DELETE only a block id it read from that page (anything else is `write_outside_page`, nothing written); a page whose blocks are not in
# that order is refused (`page_shape_changed`) and left alone, so a failed run leaves the previous page; the page is found by its title and shape, never by a committed id (`page_not_found`,
# `page_ambiguous`); a payload that is marked synthetic or links to an example host is refused before anything is read (`synthetic_payload_refused`); it is idempotent. Counts and fixed codes only.
import os

from lifeos.inbox import page_rows, page_view
from lifeos.inbox.model import InboxError
from lifeos.platform.notion_client import Client, NotionError

TITLE = "Agent Inbox"
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


def _database_id(client, block):
    """The database id of a top-level block that is an inline database, or a callout that wraps one (Jim's own edit); None for any other block."""
    if block.get("type") == "child_database":
        return block["id"]
    if block.get("type") == "callout" and block.get("has_children"):
        inner = [k for k in _children(client, block["id"]) if k.get("type") == "child_database"]
        return inner[0]["id"] if len(inner) == 1 else None
    return None


def parse(body, client):
    """The page's blocks as (tiles, list database id, System health database id, last-refreshed paragraph, state callout or None), or InboxError page_shape_changed."""
    kinds = [b.get("type") for b in body]
    if len(body) not in (6, 7) or kinds[:2] != ["column_list", "divider"] or kinds[3] != "divider" or kinds[5] != "paragraph":
        raise InboxError("page_shape_changed")
    if len(body) == 7 and kinds[6] != "callout":
        raise InboxError("page_shape_changed")
    listing, health = _database_id(client, body[2]), _database_id(client, body[4])
    if not listing or not health:
        raise InboxError("page_shape_changed")
    return body[0], listing, health, body[5], (body[6] if len(body) == 7 else None)


class Page:
    """The page's tree, with the guarded write calls."""

    def __init__(self, client, page_id):
        self.client, self.page_id, self.writes = client, page_id, 0
        body = _children(client, page_id)
        self.columns, self.listing, self.health, self.refreshed, self.state_block = parse(body, client)
        self.body = body
        self.allowed = {page_id} | {b["id"] for b in body}
        self.tree = {}
        for block in body:
            if block.get("has_children") and block.get("type") in ("column_list", "paragraph"):
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
    """Write `view` (page_view.render) into the tiles, the two databases' owned rows and the last-refreshed line. -> counts. Every tile is the same two blocks (the number, the label): only the number is written."""
    text = 0
    for column, number in zip(_kids(page, page.columns, 5), [*view["tiles"], view["healthy"]]):
        text += page.set_text(_kids(page, _kids(page, column, 1)[0], 2)[0], [(number, False, False)])
    text += page.set_text(page.refreshed, [(view["refreshed"], False, True)])
    listed = page_rows.reconcile(page.client, page.listing, view["rows"], {w.lower() for w in view["clean"]})
    healthy = page_rows.reconcile(page.client, page.health, view["health"], set(), page_rows.HEALTH, archive=False)
    page.writes += sum(listed.values()) + sum(healthy.values())
    states = page.set_state(view["state"])
    return {"text_blocks_written": text, "list_rows_created": listed["created"], "list_rows_updated": listed["updated"], "list_rows_archived": listed["archived"],
            "health_rows_created": healthy["created"], "health_rows_updated": healthy["updated"], "state_callouts_changed": states, "unchanged": int(page.writes == 0)}


def locate(client):
    """The page's id: the one page titled `Agent Inbox` whose blocks parse as the dashboard. None or several is a refusal; no id is ever committed."""
    found = client.call("POST", "/search", {"query": TITLE, "filter": {"property": "object", "value": "page"}, "page_size": 20}).get("results", [])
    named = [r["id"] for r in found if "".join(t.get("plain_text", "") for t in (r.get("properties", {}).get("title") or {}).get("title", [])) == TITLE]
    holders = []
    for page_id in named:
        try:
            parse(_children(client, page_id), client)
        except InboxError:
            continue
        holders.append(page_id)
    if len(holders) != 1:
        raise InboxError("page_not_found" if not holders else "page_ambiguous")
    return holders[0]


def _client(env):
    token = (env.get("NOTION_API_TOKEN") or "").strip()
    if not token:
        raise InboxError("view_config_missing")
    return Client({"NOTION_API_TOKEN": token, "NOTION_INBOX_VIEW_SOURCE": "0" * 32}, source_name="NOTION_INBOX_VIEW_SOURCE")


def publish(payloads, now, environ=None, client=None, page_id=None, items=None):
    """-> counts only; raises InboxError with a fixed reason word. The one entry point: read the page, render the words from the stored payloads, write what differs."""
    env = os.environ if environ is None else environ
    if any(page_view.is_synthetic(p) for p in payloads):
        raise InboxError("synthetic_payload_refused")
    try:
        client = client or _client(env)
        return apply(Page(client, page_id or locate(client)), page_view.render(payloads, now, items))
    except NotionError as error:
        raise InboxError(f"view_notion_{str(error).lower()[:40]}") from None


__all__ = ["TITLE", "Page", "apply", "locate", "parse", "publish"]
