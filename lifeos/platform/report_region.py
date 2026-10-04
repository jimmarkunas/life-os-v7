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
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
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
