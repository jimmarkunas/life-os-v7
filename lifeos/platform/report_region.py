"""Write the text of one V7-owned callout on the Daily Report, safely (the mechanics proven by the Calendar and Bills cards).

The caller owns exactly one region (router.OWNERS). This module only: checks the configured block is a callout whose first child is that region's
heading; inserts new text right under the heading; removes the old PLAIN TEXT of that callout (never a table, database, page or any other block, so
Jim's interactive views stay where they are); re-reads the callout; and proves the other V7 regions are byte-for-byte unchanged around every write.
`fail(code)` builds the caller's own error type, so codes stay fixed and carry no content."""
import hashlib
import json
import time
from copy import deepcopy
from urllib.parse import quote

from lifeos.platform import router
from lifeos.platform.notion_client import NotionError

HEADINGS = ("heading_2", "heading_3", "heading_4")
TEXT_KINDS = ("paragraph", "bulleted_list_item", "numbered_list_item", "to_do")
VOLATILE = {"last_edited_time", "last_edited_by", "request_id", "expiry_time"}      # metadata that changes without the content changing


def clean_id(raw):
    """A block id as Notion wants it. Accepts the id itself (with or without dashes) or a copied Notion link, whose `#` fragment is the block id; anything else comes back
    unchanged so the caller's own check rejects it. Never logs or raises."""
    value = (raw or "").strip()
    if "#" in value:
        value = value.rsplit("#", 1)[1].strip()
    bare = value.replace("-", "")
    return bare.lower() if len(bare) == 32 and all(c in "0123456789abcdefABCDEF" for c in bare) else (raw or "").strip()


def plain(block):
    inner = block.get(block.get("type")) or {}
    return "".join((part.get("plain_text") or (part.get("text") or {}).get("content") or "")
                   for part in inner.get("rich_text") or [] if isinstance(part, dict))


def same_id(left, right):
    return isinstance(left, str) and isinstance(right, str) and left.replace("-", "").lower() == right.replace("-", "").lower()


def children(client, block_id, fail):
    results, cursor, seen = [], None, set()
    for _ in range(50):
        path = f"/blocks/{quote(block_id, safe='')}/children?page_size=100"
        if cursor:
            if cursor in seen:
                raise fail("NOTION_PAGINATION_INVALID")
            seen.add(cursor)
            path += f"&start_cursor={quote(cursor, safe='')}"
        page = client.call("GET", path)
        if (not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool)
                or any(not isinstance(block, dict) or not isinstance(block.get("id"), str) for block in page["results"])):
            raise fail("NOTION_PAGE_INCOMPLETE")
        results.extend(page["results"])
        if not page["has_more"]:
            return results
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise fail("NOTION_PAGE_INCOMPLETE")
    raise fail("NOTION_PAGINATION_INCOMPLETE")


def _stable(node):
    if isinstance(node, dict):
        out = {k: _stable(v) for k, v in node.items() if k not in VOLATILE}
        if out.get("type") == "file" or "expiry_time" in node:
            out.pop("url", None)
        return out
    if isinstance(node, list):
        return [_stable(v) for v in node]
    return node


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:16]


def _tree(client, block, fail):
    node = deepcopy(block)
    if block.get("has_children"):
        node["region_children"] = [_tree(client, child, fail) for child in children(client, block["id"], fail)]
    return node


def region_digest(client, block_id, fail):
    """One hash per field of a protected callout and one per child block, so a change can be located (names only)."""
    block_id = clean_id(block_id)
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if isinstance(meta, dict) and meta.get("type") == "heading_3" and same_id(meta.get("id"), block_id):
        return floating_digest(client, meta, fail)                                   # the Hiring Pipeline region is a heading, a paragraph and a table, not a callout
    if not isinstance(meta, dict) or meta.get("type") != "callout" or not same_id(meta.get("id"), block_id):
        raise fail("PROTECTED_REGION_UNAVAILABLE")
    tree = _stable(_tree(client, meta, fail))
    kids = tree.pop("region_children", [])
    return {"fields": {key: _hash(value) for key, value in tree.items()}, "children": [[child.get("type"), _hash(child)] for child in kids]}


def changed(before, after):
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"digest": "missing"}
    fields = sorted(k for k in set(before["fields"]) | set(after["fields"]) if before["fields"].get(k) != after["fields"].get(k))
    kids = [i for i in range(max(len(before["children"]), len(after["children"])))
            if (before["children"][i:i + 1] or [None]) != (after["children"][i:i + 1] or [None])]
    return {"fields": fields, "children": kids}


def protected(client, ids, fail):
    """ids: {region heading: block id}. Every id must be configured; -> {region: digest}."""
    ids = {region: clean_id(block_id) for region, block_id in (ids or {}).items() if block_id or region != router.HIRING_REGION}     # Hiring joins the proof once its anchor is configured
    if not ids or not all(ids.values()):
        raise fail("PROTECTED_REGION_UNAVAILABLE")
    try:
        return {region: region_digest(client, block_id, fail) for region, block_id in ids.items()}
    except NotionError:
        raise
    except Exception:
        raise fail("PROTECTED_REGION_UNAVAILABLE") from None


def replace_text(client, block_id, title, module, blocks, protected_ids, fail, tag, live, headless=False):
    """Dry run: validate the target and return (existing children, [] ). Live: write, read back, verify. -> (existing children, removed count).
    headless (D129): the callout has no heading of its own (it holds only Jim's interactive view, which shows its own title), so the configured block id is the ownership proof;
    it must hold at least one non-text block and no heading, and the text goes in at the top, above that view."""
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(meta, dict) or meta.get("type") != "callout" or not same_id(meta.get("id"), block_id):
        raise fail("CARD_NOT_OWNED")
    existing = children(client, block_id, fail)
    if headless:
        if not existing or any(b.get("type") in HEADINGS for b in existing) or all(b.get("type") in TEXT_KINDS for b in existing):
            raise fail("CARD_NOT_OWNED")
    elif not (existing and existing[0].get("type") in HEADINGS and plain(existing[0]).strip() == title):
        raise fail("CARD_NOT_OWNED")
    try:
        router.check_write(module, [title])
    except router.RouterError:
        raise fail("CARD_NOT_OWNED") from None
    if not live:
        return existing, 0
    body = existing if headless else existing[1:]
    old_text = [b for b in body if b.get("type") in TEXT_KINDS]
    kept = [b["id"] for b in body if b.get("type") not in TEXT_KINDS]                    # tables, databases, pages and anything else stay, in order
    before = protected(client, protected_ids, fail)

    def intact():
        after = protected(client, protected_ids, fail)
        if not router.protected_intact(module, before, after):
            print(f"{tag}: protected region changed", json.dumps({r: changed(before.get(r), after.get(r)) for r in before if before.get(r) != after.get(r)}))
            raise fail("PROTECTED_REGION_CHANGED")

    if headless:
        return existing, _write_headless(client, block_id, title, module, blocks, existing, kept, old_text, intact, fail)
    try:
        placement = {"after": existing[0]["id"]}
        result = client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": blocks, **placement})
    finally:
        intact()
    # Notion's reply to an insert-after can list the following blocks as well as the new ones, so the count is only a floor; the read-back below is the proof.
    if not isinstance(result, dict) or not isinstance(result.get("results"), list) or len(result["results"]) < len(blocks):
        raise fail("CARD_APPEND_MISMATCH")
    removed = 0
    for old in old_text:
        try:
            router.check_write(module, [title])
        except router.RouterError:
            raise fail("CARD_NOT_OWNED") from None
        try:
            client.call("DELETE", f"/blocks/{quote(old['id'], safe='')}")
            removed += 1
        except NotionError as error:
            if str(error) != "NOTION_HTTP_404":
                raise
        finally:
            intact()
    after = children(client, block_id, fail)                                             # authoritative read-back
    intact()
    if (not after or after[0].get("id") != existing[0].get("id") or plain(after[0]).strip() != title or len(after) != 1 + len(blocks) + len(kept)
            or [plain(b) for b in after[1:1 + len(blocks)]] != [plain(b) for b in blocks] or [b.get("id") for b in after[1 + len(blocks):]] != kept):
        raise fail("CARD_VERIFY_FAILED")
    return existing, removed


def _write_headless(client, block_id, title, module, blocks, existing, kept, old_text, intact, fail):
    """D131: the status line of a callout that holds only Jim's view is the callout's OWN text (its rich_text field), set by one PATCH of the callout block. Child paragraphs made the line
    pile up: Notion's child listing did not show earlier lines, so every run inserted another. One field cannot pile up, needs no insert or delete, and is read back from the same block.
    Child text blocks the listing does show are removed. -> removed count."""
    if len(blocks) != 1 or blocks[0].get("type") != "paragraph":
        raise fail("CARD_NOT_OWNED")
    rich = blocks[0]["paragraph"]["rich_text"]
    wanted = plain(blocks[0])
    try:
        router.check_write(module, [title])
    except router.RouterError:
        raise fail("CARD_NOT_OWNED") from None
    try:
        client.call("PATCH", f"/blocks/{quote(block_id, safe='')}", {"callout": {"rich_text": rich}})
    finally:
        intact()
    removed = 0
    for extra in old_text:
        try:
            client.call("DELETE", f"/blocks/{quote(extra['id'], safe='')}")
            removed += 1
        except NotionError as error:
            if str(error) != "NOTION_HTTP_404":
                raise
        finally:
            intact()
    shape = (False, False, False)
    for attempt in range(4):                                                              # authoritative read-back; a short wait first if Notion still shows the pre-write state
        if attempt:
            time.sleep(3)
        meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
        after = children(client, block_id, fail)
        intact()
        own = plain({"type": "callout", "callout": (meta or {}).get("callout") or {}})
        shape = (own == wanted, not any(b.get("type") in TEXT_KINDS for b in after), [b.get("id") for b in after] == kept)
        if all(shape):
            return removed
    raise fail("CARD_VERIFY_FAILED:own=%s:no_child_text=%s:kept=%s:n=%d/%d" % (*shape, len(after), len(kept)))


# ---- The floating region (HIRE-1.2): a heading_3 anchor, one status paragraph and one table, owned by one module and written in place. ----
# Shared primitives: resolve and validate the region, digest the whole page outside it, and rewrite the paragraph and the table rows. Nothing here creates, moves or deletes the heading.
TABLE_KINDS = ("table", "table_row")
LEAVES = ("child_page", "child_database", "link_to_page", "link_preview", "unsupported")      # separate pages and views: never walked, never ours


def cells_of(row):
    return [plain({"type": "x", "x": {"rich_text": cell}}) for cell in (row.get("table_row") or {}).get("cells") or []]


def _links_of(row):
    return [[((part.get("text") or {}).get("link") or {}).get("url") if isinstance(part, dict) else None for part in cell]      # Notion sends "link": null for plain text
            for cell in (row.get("table_row") or {}).get("cells") or []]


def _meta(client, block_id):
    return client.call("GET", f"/blocks/{quote(block_id, safe='')}")


def page_of(client, meta, fail, ancestors=None):
    """The page id a block lives on, walking up its parents (at most 12 levels). `ancestors`, if given, collects every ancestor block's type."""
    node = meta
    for _ in range(12):
        parent = (node or {}).get("parent") or {}
        kind = parent.get("type")
        if kind == "page_id":
            return parent["page_id"]
        if kind != "block_id":
            break
        node = _meta(client, parent["block_id"])
        if not isinstance(node, dict):
            break
        if ancestors is not None:
            ancestors.append(node.get("type"))
    raise fail("FLOATING_PAGE_UNRESOLVED")


def floating_region(client, block_id, title, fail):
    """Validate the one floating region the configured heading anchors and return {"heading", "status", "table", "rows", "page_id", "parent_id", "siblings"}.
    Accepted shape ONLY: a heading_3 whose text is exactly `title`, not inside any callout, followed by exactly one paragraph and one table with a header row of four
    columns; the block after them (if any) is neither a paragraph nor a table. Anything else raises: no write is ever attempted on an ambiguous region."""
    def at(stage, call):
        try:
            return call()
        except NotionError as error:
            if type(error).__name__ == type(fail("X")).__name__:
                raise
            raise fail(f"FLOATING_{stage}_{error}") from None             # the failing call's name and Notion's status, never an id or text

    meta = at("HEADING_GET", lambda: _meta(client, block_id))
    if not isinstance(meta, dict) or meta.get("type") != "heading_3" or not same_id(meta.get("id"), block_id) or plain(meta).strip() != title:
        raise fail("FLOATING_HEADING_INVALID")
    ancestors = []
    page_id = at("PARENT_WALK", lambda: page_of(client, meta, fail, ancestors))
    if "callout" in ancestors:
        raise fail("FLOATING_WRAPPED_IN_CALLOUT")
    parent = meta["parent"]
    parent_id = parent.get("block_id") or parent.get("page_id")
    siblings = at("SIBLINGS", lambda: children(client, parent_id, fail))
    found = [i for i, b in enumerate(siblings) if same_id(b.get("id"), block_id)]
    if len(found) != 1 or sum(1 for b in siblings if b.get("type") == "heading_3" and plain(b).strip() == title) != 1:
        raise fail("FLOATING_HEADING_AMBIGUOUS")
    i = found[0]
    status, table = (siblings[i + 1:i + 2] or [None])[0], (siblings[i + 2:i + 3] or [None])[0]
    after = (siblings[i + 3:i + 4] or [None])[0]
    if not status or status.get("type") != "paragraph" or not table or table.get("type") != "table" or (after and after.get("type") in ("paragraph", "table")):
        raise fail("FLOATING_SHAPE_INVALID")
    rows = at("TABLE_ROWS", lambda: children(client, table["id"], fail))
    spec = table.get("table") or {}
    if spec.get("table_width") != 4 or spec.get("has_column_header") is not True or not rows or any(r.get("type") != "table_row" for r in rows):
        raise fail("FLOATING_TABLE_INVALID")
    return {"heading": siblings[i], "status": status, "table": table, "rows": rows, "page_id": page_id, "parent_id": parent_id, "siblings": siblings}


def floating_digest(client, meta, fail):
    """Digest of a floating region as a protected neighbour: the heading and the next two blocks with everything under them. Lenient about shape (only the owner validates it)."""
    parent = (meta.get("parent") or {})
    parent_id = parent.get("block_id") or parent.get("page_id")
    if not parent_id:
        raise fail("PROTECTED_REGION_UNAVAILABLE")
    siblings = children(client, parent_id, fail)
    at = [i for i, b in enumerate(siblings) if same_id(b.get("id"), meta.get("id"))]
    if len(at) != 1:
        raise fail("PROTECTED_REGION_UNAVAILABLE")
    group = siblings[at[0]:at[0] + 3]
    trees = [_stable(_tree(client, b, fail)) for b in group]
    return {"fields": {f"block{n}": _hash(tree) for n, tree in enumerate(trees)}, "children": [[t.get("type"), _hash(t)] for t in trees]}


def page_digest(client, page_id, own_ids, fail, skip_titles=(), budget=400):
    """A stable digest of every block on the page OUTSIDE the owned blocks: {"nodes": {id: hash of the block's own content}, "layout": {parent: [child ids]}, "h3": {heading text: count}}.
    Owned blocks (and everything under them) appear only as ids, so the owner's own edits never register. A callout whose first child is a heading in `skip_titles`
    (a region another system writes on its own schedule) is recorded by id only. Separate pages and views are never entered."""
    own = {i.replace("-", "").lower() for i in own_ids}
    nodes, layout, titles, left, kinds = {}, {}, {}, [budget], {}

    def walk(parent_id):
        left[0] -= 1
        if left[0] < 0:
            raise fail("PAGE_DIGEST_INCOMPLETE")
        try:
            kids = children(client, parent_id, fail)
        except NotionError as error:
            raise fail(f"PAGE_WALK_{error}:{kinds.get(parent_id, 'page')}") from None             # the kind of block that could not be listed, never its id or text
        layout[parent_id] = [k["id"] for k in kids]
        for position, kid in enumerate(kids):
            key = kid["id"].replace("-", "").lower()
            if key in own:
                nodes[kid["id"]] = "owned"
                continue
            flat = {k: v for k, v in kid.items() if k not in ("has_children",)}
            nodes[kid["id"]] = _hash(_stable(flat))
            kinds[kid["id"]] = kid.get("type")
            if kid.get("type") == "heading_3":
                titles[plain(kid).strip()] = titles.get(plain(kid).strip(), 0) + 1
            if kid.get("has_children") and kid.get("type") not in LEAVES:
                if kid.get("type") == "callout" and skip_titles:
                    first = (children(client, kid["id"], fail) or [None])[0]
                    if first and plain(first).strip() in skip_titles:
                        continue
                walk(kid["id"])

    walk(page_id)
    return {"nodes": nodes, "layout": layout, "h3": titles}


def page_changed(before, after):
    """Counts only: how many blocks differ, appeared or vanished, and whether any parent's child order moved."""
    keys = set(before["nodes"]) | set(after["nodes"])
    return {"blocks": sum(before["nodes"].get(k) != after["nodes"].get(k) for k in keys),
            "layout": sum(before["layout"].get(k) != after["layout"].get(k) for k in set(before["layout"]) | set(after["layout"]))}


def cell_blocks(cells):
    """cells: one row as [(text, link or None)] -> the table_row `cells` field."""
    return [[{"type": "text", "text": {"content": text, **({"link": {"url": link}} if link else {})}}] for text, link in cells]


def write_floating(client, region, status_text, rows, fail):
    """Rewrite only the status paragraph and the table rows of a validated region. rows: [[(text, link)] x 4] or None to leave the table alone.
    The header row (row 0) is never edited. Existing data rows are PATCHed in place, extras appended, surplus deleted. -> {"rows_changed", "rows_added", "rows_removed"}."""
    counts = {"rows_changed": 0, "rows_added": 0, "rows_removed": 0}
    table_id = region["table"]["id"]
    if rows is not None:
        have = region["rows"][1:]
        for row, want in zip(have, rows):
            if cells_of(row) != [t for t, _ in want] or [x[0] if x else None for x in _links_of(row)] != [link for _, link in want]:
                client.call("PATCH", f"/blocks/{quote(row['id'], safe='')}", {"table_row": {"cells": cell_blocks(want)}})
                counts["rows_changed"] += 1
        extra = rows[len(have):]
        if extra:
            client.call_once("PATCH", f"/blocks/{quote(table_id, safe='')}/children",
                             {"children": [{"object": "block", "type": "table_row", "table_row": {"cells": cell_blocks(r)}} for r in extra]})
            counts["rows_added"] = len(extra)
        for row in have[len(rows):]:
            client.call("DELETE", f"/blocks/{quote(row['id'], safe='')}")
            counts["rows_removed"] += 1
    if plain(region["status"]) != status_text:
        client.call("PATCH", f"/blocks/{quote(region['status']['id'], safe='')}", {"paragraph": {"rich_text": [{"type": "text", "text": {"content": status_text}}]}})
    return counts
