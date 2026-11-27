"""Write one V7-owned callout on the Daily Report safely.

The caller owns one region (router.OWNERS): this module checks the block is a callout led by that region's heading,
inserts new text under it, removes only the old plain text and re-reads. `fail(code)` builds the caller's error type.
"""
import hashlib
import json
import re
import time
from copy import deepcopy
from itertools import groupby
from urllib.parse import quote, unquote

from lifeos.platform import readback, router
from lifeos.platform.notion_client import NotionError

HEADINGS = ("heading_2", "heading_3", "heading_4")
TEXT_KINDS = ("paragraph", "bulleted_list_item", "numbered_list_item", "to_do")
# another writer can rewrite blocks mid-walk: walk once more, then fail
WALK_ATTEMPTS, WALK_RETRY_SECONDS = 2, 2
VOLATILE = {"last_edited_time", "last_edited_by", "request_id", "expiry_time"}


def clean_id(raw):
    """A Notion block id from a bare id or a copied link; anything else is returned unchanged."""
    value = (raw or "").strip()
    if "#" in value:
        value = value.rsplit("#", 1)[1].strip()
    bare = value.replace("-", "")
    return bare.lower() if len(bare) == 32 and all(c in "0123456789abcdefABCDEF" for c in bare) else (raw or "").strip()


def plain(block):
    inner = block.get(block.get("type")) or {}
    return "".join((part.get("plain_text") or (part.get("text") or {}).get("content") or "")
                   for part in inner.get("rich_text") or [] if isinstance(part, dict))


def titled(text, title):
    text = (text or "").strip()
    return text == title or (text.startswith(title + " (") and text.endswith(")"))


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
    """One hash per protected-callout field and per child block."""
    block_id = clean_id(block_id)
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if isinstance(meta, dict) and meta.get("type") == "heading_3" and same_id(meta.get("id"), block_id):
        return floating_digest(client, meta, fail)
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
    ids = {region: clean_id(block_id) for region, block_id in (ids or {}).items() if block_id or region != router.HIRING_REGION}
    if not ids or not all(ids.values()):
        raise fail("PROTECTED_REGION_UNAVAILABLE")
    try:
        return {region: region_digest(client, block_id, fail) for region, block_id in ids.items()}
    except NotionError:
        raise
    except Exception:
        raise fail("PROTECTED_REGION_UNAVAILABLE") from None


def _norm(raw):
    return clean_id(unquote(str(raw or "")))


class ScopedClient:
    """Guard: a card writes only blocks it owns, checked BEFORE every write; else WRITE_OUT_OF_SCOPE, unsent."""
    WRITES = ("PATCH", "POST", "DELETE", "PUT")

    def __init__(self, client, root, fail):
        self._client, self._fail, self.owned = client, fail, {_norm(root)}

    def adopt(self, *block_ids):
        self.owned.update(_norm(i) for i in block_ids)
        return self

    def __getattr__(self, name):
        return getattr(self._client, name)

    def _target(self, path):
        found = re.match(r"/blocks/([^/?]+)", path or "")
        return _norm(found.group(1)) if found else None

    def _check(self, method, path):
        if str(method).upper() in self.WRITES and self._target(path) not in self.owned:
            raise self._fail("WRITE_OUT_OF_SCOPE")

    def _learn(self, path, result):
        parent = re.match(r"/blocks/([^/?]+)/children", path or "")
        if parent and _norm(parent.group(1)) in self.owned and isinstance(result, dict):
            for block in result.get("results") or []:
                if isinstance(block, dict) and isinstance(block.get("id"), str):
                    self.owned.add(_norm(block["id"]))

    def call(self, method, path, body=None):
        self._check(method, path)
        result = self._client.call(method, path, body)
        self._learn(path, result)
        return result

    def call_once(self, method, path, body=None, *args, **kwargs):
        self._check(method, path)
        result = self._client.call_once(method, path, body, *args, **kwargs)
        self._learn(path, result)
        return result


def require_configured(protected_ids, fail):
    ids = {region: clean_id(block_id) for region, block_id in (protected_ids or {}).items() if block_id or region != router.HIRING_REGION}
    if not ids or not all(ids.values()):
        raise fail("PROTECTED_REGION_UNAVAILABLE")


def _prove(client, module, before, protected_ids, tag, fail):
    after = protected(client, protected_ids, fail)
    if not router.protected_intact(module, before, after):
        print(f"{tag}: protected region changed", json.dumps({r: changed(before.get(r), after.get(r)) for r in before if before.get(r) != after.get(r)}))
        raise fail("PROTECTED_REGION_CHANGED")


def replace_text(client, block_id, title, module, blocks, protected_ids, fail, tag, live, headless=False, heading=None):
    """Dry run -> (existing children, []). Live: write, read back, verify -> (existing children, removed count).
    `headless`: no heading, only an interactive view. `heading`: text to show (default `title (stamp)`; None: `title`)."""
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(meta, dict) or meta.get("type") != "callout" or not same_id(meta.get("id"), block_id):
        raise fail("CARD_NOT_OWNED")
    client = ScopedClient(client, block_id, fail)
    existing = children(client, block_id, fail)
    if headless:
        if not existing or any(b.get("type") in HEADINGS for b in existing) or all(b.get("type") in TEXT_KINDS for b in existing):
            raise fail("CARD_NOT_OWNED")
    elif not (existing and existing[0].get("type") in HEADINGS and (plain(existing[0]).strip() == title if heading is None else titled(plain(existing[0]), title) and titled(heading, title))):
        raise fail("CARD_NOT_OWNED")
    try:
        router.check_write(module, [title])
    except router.RouterError:
        raise fail("CARD_NOT_OWNED") from None
    if not live:
        return existing, 0
    body = existing if headless else existing[1:]
    old_text = [b for b in body if b.get("type") in TEXT_KINDS]
    kept = [b["id"] for b in body if b.get("type") not in TEXT_KINDS]
    require_configured(protected_ids, fail)
    shown, was = title if heading is None else heading, plain(existing[0]).strip()
    stamped = shown != was
    if stamped:
        client.call("PATCH", f"/blocks/{quote(existing[0]['id'], safe='')}", {existing[0]["type"]: {"rich_text": [{"type": "text", "text": {"content": shown}}]}})
    try:  # a failure from here on must not leave the NEW stamp over the OLD content
        def intact():
            """No-op: the scope guard acts before each write."""

        if headless:
            return existing, _write_headless(client, block_id, title, module, blocks, existing, kept, old_text, intact, fail)
        if _in_place_ok(body, old_text, blocks):
            return existing, _write_in_place(client, block_id, title, module, blocks, existing, kept, old_text, intact, fail, shown)
        try:
            placement = {"after": existing[0]["id"]}
            result = client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": blocks, **placement})
        finally:
            intact()
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
        after = children(client, block_id, fail)
        intact()
        if (not after or after[0].get("id") != existing[0].get("id") or plain(after[0]).strip() != shown or len(after) != 1 + len(blocks) + len(kept)
                or [plain(b) for b in after[1:1 + len(blocks)]] != [plain(b) for b in blocks] or [b.get("id") for b in after[1 + len(blocks):]] != kept):
            raise fail("CARD_VERIFY_FAILED")
        return existing, removed
    except Exception:
        if stamped:
            _restore_stamp(client, existing[0], was)
        raise


def _restore_stamp(client, heading_block, text):
    try:
        client.call("PATCH", f"/blocks/{quote(heading_block['id'], safe='')}", {heading_block["type"]: {"rich_text": [{"type": "text", "text": {"content": text}}]}})
    except Exception:  # noqa: BLE001
        pass


def _in_place_ok(body, old_text, blocks):
    if not old_text or body[:len(old_text)] != old_text or any(b.get("has_children") for b in old_text):
        return False
    return all(old.get("type") == new.get("type") for old, new in zip(old_text, blocks))


def _write_in_place(client, block_id, title, module, blocks, existing, kept, old_text, intact, fail, shown=None):
    def owned():
        try:
            router.check_write(module, [title])
        except router.RouterError:
            raise fail("CARD_NOT_OWNED") from None

    pairs = list(zip(old_text, blocks))
    for old, new in pairs:
        owned()
        kind = new["type"]
        try:
            client.call("PATCH", f"/blocks/{quote(old['id'], safe='')}", {kind: {k: v for k, v in new[kind].items() if k in ("rich_text", "color")}})
        finally:
            intact()
    extra = blocks[len(pairs):]
    if extra:
        owned()
        anchor = (old_text[len(pairs) - 1] if pairs else existing[0])["id"]
        try:
            result = client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": extra, "after": anchor})
        finally:
            intact()
        if not isinstance(result, dict) or not isinstance(result.get("results"), list) or len(result["results"]) < len(extra):
            raise fail("CARD_APPEND_MISMATCH")
    removed = 0
    for old in old_text[len(pairs):]:
        owned()
        try:
            client.call("DELETE", f"/blocks/{quote(old['id'], safe='')}")
            removed += 1
        except NotionError as error:
            if str(error) != "NOTION_HTTP_404":
                raise
        finally:
            intact()
    after = children(client, block_id, fail)
    intact()
    rewritten = [b["id"] for b, _ in pairs]
    if (not after or after[0].get("id") != existing[0].get("id") or plain(after[0]).strip() != (title if shown is None else shown) or len(after) != 1 + len(blocks) + len(kept)
            or [plain(b) for b in after[1:1 + len(blocks)]] != [plain(b) for b in blocks] or [b.get("id") for b in after[1:1 + len(rewritten)]] != rewritten
            or [b.get("id") for b in after[1 + len(blocks):]] != kept):
        raise fail("CARD_VERIFY_FAILED")
    return removed


def _write_headless(client, block_id, title, module, blocks, existing, kept, old_text, intact, fail):
    """Set the status line as the callout's own rich_text (one PATCH), remove listed child text blocks -> removed count."""
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
    def read():
        meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
        after = children(client, block_id, fail)
        intact()
        own = plain({"type": "callout", "callout": (meta or {}).get("callout") or {}})
        return (own == wanted, not any(b.get("type") in TEXT_KINDS for b in after), [b.get("id") for b in after] == kept), len(after)

    ok, (shape, n_after) = readback.confirmed(read, lambda seen: all(seen[0]))
    if ok:
        return removed
    raise fail("CARD_VERIFY_FAILED:own=%s:no_child_text=%s:kept=%s:n=%d/%d" % (*shape, n_after, len(kept)))


def _shell(block, depth=2):
    kind = block["type"]
    body = {k: v for k, v in block[kind].items() if k != "children"}
    if depth and block[kind].get("children"):
        body["children"] = [_shell(child, depth - 1) for child in block[kind]["children"]]
    return {"object": "block", "type": kind, kind: body}


def _sig(rich):
    return [((p.get("text") or {}).get("content", p.get("plain_text", "")), bool((p.get("annotations") or {}).get("bold")), (p.get("annotations") or {}).get("color", "default"),
             ((p.get("text") or {}).get("link") or {}).get("url")) for p in rich or []]


def _runs(blocks):
    return [(kind, list(run)) for kind, run in groupby(blocks, lambda b: b.get("type"))]


def _sync(client, parent, old, new, anchor, fail, live):
    """-> number of changes. Blocks match run by run (same type, in order): text is patched, missing blocks appended, only a numbered list
    may grow or shrink; anything else fails before any write."""
    count, olds, news = 0, _runs(old), _runs(new)
    if len(olds) > len(news) or any(o[0] != w[0] for o, w in zip(olds, news)):
        raise fail("CARD_STRUCTURE_CHANGED")
    for n, (kind, wants) in enumerate(news):
        haves = olds[n][1] if n < len(olds) else []
        if len(haves) > len(wants) and kind != "numbered_list_item":
            raise fail("CARD_STRUCTURE_CHANGED")
        for i, want in enumerate(wants):
            if i < len(haves):
                have = haves[i]
                if "rich_text" in want[kind] and _sig(have[kind].get("rich_text")) != _sig(want[kind]["rich_text"]):
                    count += 1
                    if live:
                        client.call("PATCH", f"/blocks/{quote(have['id'], safe='')}", {kind: {"rich_text": want[kind]["rich_text"]}})
            else:
                count += 1
                if not live:
                    continue
                made = client.call_once("PATCH", f"/blocks/{quote(parent, safe='')}/children", {"children": [_shell(want)], **({"after": anchor} if anchor else {})})
                if not isinstance(made, dict) or not made.get("results"):
                    raise fail("CARD_APPEND_MISMATCH")
                have = made["results"][0]
            if want[kind].get("children"):
                count += _sync(client, have["id"], children(client, have["id"], fail), want[kind]["children"], None, fail, live)
            anchor = have["id"]
        for extra in haves[len(wants):]:
            count += 1
            if live:
                client.call("DELETE", f"/blocks/{quote(extra['id'], safe='')}")
    return count


def stamped_by_dot(text, title):
    text = (text or "").strip()
    return text == title or text.startswith(title + " · ")


def replace_tree(client, block_id, title, module, spec, protected_ids, fail, tag, live, skip=frozenset(), heading=None):
    """Keep the owner's callout heading and make it hold exactly `spec` -> (changes, removed). `skip`: ids of blocks inside
    it owned by another writer. `heading`: as in replace_text."""
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(meta, dict) or meta.get("type") != "callout" or not same_id(meta.get("id"), block_id):
        raise fail("CARD_NOT_OWNED")
    client = ScopedClient(client, block_id, fail)
    existing = children(client, block_id, fail)
    if not (existing and existing[0].get("type") in HEADINGS and (plain(existing[0]).strip() == title if heading is None else stamped_by_dot(plain(existing[0]), title) and stamped_by_dot(heading, title))):
        raise fail("CARD_NOT_OWNED")
    existing = existing[:1] + [b for b in existing[1:] if b.get("id") not in skip]
    old_text, ours = [b for b in existing[1:] if b.get("type") in TEXT_KINDS], [b for b in existing[1:] if b.get("type") not in TEXT_KINDS]
    try:
        router.check_write(module, [title])
    except router.RouterError:
        raise fail("CARD_NOT_OWNED") from None
    owned_types = {b.get("type") for b in spec}
    if any(b.get("type") not in owned_types for b in ours):
        raise fail("CARD_NOT_OWNED")
    if not live:
        return _sync(client, block_id, ours, spec, existing[0]["id"], fail, False), 0
    _sync(client, block_id, ours, spec, existing[0]["id"], fail, False)
    require_configured(protected_ids, fail)
    changes = _sync(client, block_id, ours, spec, existing[0]["id"], fail, True)
    if _sync(client, block_id, [b for b in children(client, block_id, fail)[1:] if b.get("type") not in TEXT_KINDS and b.get("id") not in skip], spec, None, fail, False):
        raise fail("CARD_VERIFY_FAILED")
    if heading is not None and plain(existing[0]).strip() != heading:
        client.call("PATCH", f"/blocks/{quote(existing[0]['id'], safe='')}", {existing[0]["type"]: {"rich_text": [{"type": "text", "text": {"content": heading}}]}})
        changes += 1
    removed = 0
    for old in old_text:
        try:
            client.call("DELETE", f"/blocks/{quote(old['id'], safe='')}")
            removed += 1
        except NotionError as error:
            if str(error) != "NOTION_HTTP_404":
                raise
    after = children(client, block_id, fail)
    after = after[:1] + [b for b in after[1:] if b.get("id") not in skip]
    if [b.get("id") for b in after[:1]] != [existing[0]["id"]] or [b.get("type") for b in after[1:]] != [b.get("type") for b in spec] or (heading is not None and plain(after[0]).strip() != heading):
        raise fail("CARD_VERIFY_FAILED")
    return changes, removed


TABLE_KINDS = ("table", "table_row")
LEAVES = ("child_page", "child_database", "link_to_page", "link_preview", "unsupported")


def cells_of(row):
    return [plain({"type": "x", "x": {"rich_text": cell}}) for cell in (row.get("table_row") or {}).get("cells") or []]


STYLES = {"bold": {"bold": True}, "gray": {"color": "gray"}, "bold+gray": {"bold": True, "color": "gray"}}


def _segs(cell):
    return [(s + (None,))[:3] for s in ([cell] if isinstance(cell, tuple) else cell)]


def _owned(spans):
    return [("".join(t for t, _, _ in group), link, style) for (link, style), group in groupby(spans, key=lambda span: (span[1], span[2]))]


def want_view(row):
    return [_owned(_segs(c)) for c in row]


def _style(part):
    notes = part.get("annotations") or {}
    style = "+".join(n for n, on in (("bold", notes.get("bold")), ("gray", notes.get("color") == "gray")) if on)
    return style or None


def live_view(row):
    return [_owned([(plain({"type": "x", "x": {"rich_text": [part]}}), ((part.get("text") or {}).get("link") or {}).get("url"), _style(part)) for part in cell if isinstance(part, dict)])
            for cell in (row.get("table_row") or {}).get("cells") or []]


def _meta(client, block_id):
    return client.call("GET", f"/blocks/{quote(block_id, safe='')}")


def page_of(client, meta, fail, ancestors=None):
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


def floating_region(client, block_id, title, fail, with_status=True):
    """Validate the floating region a heading anchors -> {"heading", "status", "table", "rows", "page_id", "parent_id",
    "siblings"}. Shape: a heading_3 `title` or `title (stamp)` outside any callout, one paragraph (none without `with_status`)
    and one four-column table. Anything else raises; an ambiguous region is never written."""
    def at(stage, call):
        try:
            return call()
        except NotionError as error:
            if type(error).__name__ == type(fail("X")).__name__:
                raise
            raise fail(f"FLOATING_{stage}_{error}") from None

    meta = at("HEADING_GET", lambda: _meta(client, block_id))
    if not isinstance(meta, dict) or meta.get("type") != "heading_3" or not same_id(meta.get("id"), block_id) or not titled(plain(meta), title):
        raise fail("FLOATING_HEADING_INVALID")
    ancestors = []
    page_id = at("PARENT_WALK", lambda: page_of(client, meta, fail, ancestors))
    if "callout" in ancestors:
        raise fail("FLOATING_WRAPPED_IN_CALLOUT")
    parent = meta["parent"]
    parent_id = parent.get("block_id") or parent.get("page_id")
    siblings = at("SIBLINGS", lambda: children(client, parent_id, fail))
    found = [i for i, b in enumerate(siblings) if same_id(b.get("id"), block_id)]
    if len(found) != 1 or sum(1 for b in siblings if b.get("type") == "heading_3" and titled(plain(b), title)) != 1:
        raise fail("FLOATING_HEADING_AMBIGUOUS")
    i = found[0]
    ahead = siblings[i + 1:i + 4] + [None] * 3
    status, table, after = ahead[:3] if with_status else [None] + ahead[:2]
    if (with_status and (not status or status.get("type") != "paragraph")) or not table or table.get("type") != "table" or (after and after.get("type") in ("paragraph", "table")):
        raise fail("FLOATING_SHAPE_INVALID")
    rows = at("TABLE_ROWS", lambda: children(client, table["id"], fail))
    spec = table.get("table") or {}
    if spec.get("table_width") != 4 or spec.get("has_column_header") is not True or not rows or any(r.get("type") != "table_row" for r in rows):
        raise fail("FLOATING_TABLE_INVALID")
    return {"heading": siblings[i], "status": status, "table": table, "rows": rows, "page_id": page_id, "parent_id": parent_id, "siblings": siblings}


def floating_digest(client, meta, fail):
    parent = (meta.get("parent") or {})
    parent_id = parent.get("block_id") or parent.get("page_id")
    if not parent_id:
        raise fail("PROTECTED_REGION_UNAVAILABLE")
    siblings = children(client, parent_id, fail)
    at = [i for i, b in enumerate(siblings) if same_id(b.get("id"), meta.get("id"))]
    if len(at) != 1:
        raise fail("PROTECTED_REGION_UNAVAILABLE")
    group = siblings[at[0]:at[0] + 2]
    trees = [_stable(_tree(client, b, fail)) for b in group]
    return {"fields": {f"block{n}": _hash(tree) for n, tree in enumerate(trees)}, "children": [[t.get("type"), _hash(t)] for t in trees]}


def page_digest(client, page_id, own_ids, fail, skip_titles=(), budget=400):
    """Digest of every block OUTSIDE the owned blocks: {"nodes": {id: hash}, "layout": {parent: [child ids]}, "h3": {text: count}}."""
    own = {i.replace("-", "").lower() for i in own_ids}
    for attempt in range(WALK_ATTEMPTS):
        nodes, layout, titles, left, kinds = {}, {}, {}, [budget], {}
        try:
            return _walk_page(client, page_id, own, fail, skip_titles, nodes, layout, titles, left, kinds)
        except _PageChanged as moved:
            if attempt == WALK_ATTEMPTS - 1:
                raise fail(f"PAGE_WALK_{moved}") from None
            time.sleep(WALK_RETRY_SECONDS)


class _PageChanged(Exception):
    """A listed block is gone (Notion 404): another writer rewrote the page mid-walk."""


def _walk_page(client, page_id, own, fail, skip_titles, nodes, layout, titles, left, kinds):
    def walk(parent_id):
        left[0] -= 1
        if left[0] < 0:
            raise fail("PAGE_DIGEST_INCOMPLETE")
        try:
            kids = children(client, parent_id, fail)
        except NotionError as error:
            if str(error) == "NOTION_HTTP_404" and parent_id != page_id:
                raise _PageChanged(f"{error}:{kinds.get(parent_id, 'page')}") from None
            raise fail(f"PAGE_WALK_{error}:{kinds.get(parent_id, 'page')}") from None
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
                    try:
                        first = (children(client, kid["id"], fail) or [None])[0]
                    except NotionError as error:
                        if str(error) == "NOTION_HTTP_404":
                            raise _PageChanged(f"{error}:callout") from None
                        raise
                    if first and plain(first).strip() in skip_titles:
                        continue
                walk(kid["id"])

    walk(page_id)
    return {"nodes": nodes, "layout": layout, "h3": titles}


def page_changed(before, after):
    keys = set(before["nodes"]) | set(after["nodes"])
    return {"blocks": sum(before["nodes"].get(k) != after["nodes"].get(k) for k in keys),
            "layout": sum(before["layout"].get(k) != after["layout"].get(k) for k in set(before["layout"]) | set(after["layout"]))}


def cell_blocks(cells):
    return [[{"type": "text", "text": {"content": text, **({"link": {"url": link}} if link else {})}, **({"annotations": STYLES[style]} if style else {})}
             for text, link, style in _segs(cell)] for cell in cells]


def write_floating(client, region, status_text, rows, fail):
    """Rewrite the status paragraph and table rows of a validated region. rows: [[(text, link)] x 4] or None -> {"rows_changed", "rows_added", "rows_removed"}."""
    counts = {"rows_changed": 0, "rows_added": 0, "rows_removed": 0}
    table_id = region["table"]["id"]
    if rows is not None:
        have = region["rows"][1:]
        for row, want in zip(have, rows):
            if live_view(row) != want_view(want):
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
    if region["status"] is not None and plain(region["status"]) != status_text:
        client.call("PATCH", f"/blocks/{quote(region['status']['id'], safe='')}", {"paragraph": {"rich_text": [{"type": "text", "text": {"content": status_text}}]}})
    return counts
