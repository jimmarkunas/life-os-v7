"""Notion evidence and reusable protected read-back primitives."""
import hashlib
import json
import os
import re
import time
from urllib.parse import quote
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.runtime import DeadlineExceeded
from .models import Child, Ownership, Parent, Scan

MARKERS = {kind: f"v7-interview:1;owner=machine;kind={kind}" for kind in ("opportunity", "round")}
PROTECTED = ("Live Notes", "Raw Notes")
# LIFE-46 (accepted UI 3b): Jim types ADOPT as the exact first line of his own opportunity page; adoption swaps it for the
# opportunity marker plus ";adopted=<date>". Everything from the JIM_NOTES heading to the end of the page is his.
ADOPT = "LIFE OS: adopt"
JIM_NOTES = "Jim’s Notes"                               # LIFE-88: the approved UI 3c heading (typographic apostrophe)
LEGACY_NOTES = "🔒 Jim's notes"                         # UI 3b pages (D235-D237) keep theirs until migrated
NOTES = (JIM_NOTES, LEGACY_NOTES)
# D236: the marker (and a round's identity line) are folded into one collapsed toggle at the top of the page. Pages written
# before D236 carry them as the first two plain paragraphs and are still read. UI 4 (LIFE-46 10875) titles it with Source Health.
FOLD = "⚙️ LIFE OS"
ROUND_FOLD = "⚙️ LIFE OS · Source Health"
ADOPTED = re.compile(re.escape(MARKERS["opportunity"]) + r";adopted=(\d{4}-\d{2}-\d{2})")
MAX_CALLS, MAX_DEPTH = 40, 4


def same_notion_id(a, b):
    """Notion references may use compact, dashed, or uppercase UUID spelling."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    left, right = a.replace("-", "").lower(), b.replace("-", "").lower()
    return bool(left) and bool(right) and left == right


def environment(environ):
    return {"NOTION_API_TOKEN": (environ.get("NOTION_INTERVIEW_TOKEN") or "").strip(),
            "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "interview-unused"}


def make_client(environ=os.environ):
    return Client(environment(environ))


def is_marker(value, kind):
    """The exact machine marker, or for an opportunity the adopted form (LIFE-46). Deleting the line hands the page back."""
    if value == MARKERS.get(kind):
        return True
    from .identity import valid_date
    found = ADOPTED.fullmatch(value) if kind == "opportunity" else None
    return bool(found) and valid_date(found.group(1))


def is_fold(block):
    return block.get("type") == "toggle" and text(block) in (FOLD, ROUND_FOLD)


def machine_lines(client, page_id, size):
    """(lines, fold): the first `size` machine lines, read from inside the folded toggle when the page's first block is one."""
    blocks = client.call("GET", f"/blocks/{page_id}/children?page_size={size}")["results"]
    if blocks and is_fold(blocks[0]):
        return client.call("GET", f"/blocks/{blocks[0]['id']}/children?page_size={size}")["results"], blocks[0]
    return blocks, None


def text(block):
    parts = (block.get(block.get("type")) or {}).get("rich_text") or []
    return "".join(p.get("plain_text", (p.get("text") or {}).get("content", "")) for p in parts)


def target_check(client, environ):
    root = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    if not root or not environment(environ)["NOTION_API_TOKEN"]:
        return "target_config_missing"
    try:
        page = client.call("GET", f"/pages/{root}")
        titles = [p.get("title") for p in (page.get("properties") or {}).values() if p.get("type") == "title"]
        title = "".join(p.get("plain_text", (p.get("text") or {}).get("content", "")) for p in titles[0]) if len(titles) == 1 else ""
        return "target_ok" if title == "Hiring Pipeline" and not page.get("archived") and not page.get("in_trash") else "target_mismatch"
    except (NotionError, KeyError, TypeError, ValueError):
        return "target_unreadable"


def ownership(client, page_id, kind):
    try:
        blocks, _ = machine_lines(client, page_id, 1)
        first = text(blocks[0]) if blocks else ""
        if is_marker(first, kind):
            return Ownership.MACHINE
        if kind == "opportunity" and first == ADOPT and blocks[0].get("type") == "paragraph":
            return Ownership.ADOPT
        return Ownership.UNKNOWN if "v7-interview" in first else Ownership.HUMAN
    except (NotionError, KeyError, TypeError, ValueError):
        return Ownership.UNKNOWN


def children(client, page_id, budget, context=None):
    out, cursor, seen = [], None, set()
    while budget[0] > 0:
        if context:
            context.require_time()
        budget[0] -= 1
        path = f"/blocks/{page_id}/children?page_size=100"
        data = client.call("GET", path + (f"&start_cursor={quote(cursor, safe='')}" if cursor else ""))
        if not isinstance(data.get("results"), list) or type(data.get("has_more")) is not bool:
            raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
        out.extend(data["results"])
        if not data["has_more"]:
            return out
        cursor = data.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
        seen.add(cursor)
    raise NotionError("INTERVIEW_SCAN_INCOMPLETE")


def parent_scan(client, root, context=None):
    found, budget = [], [MAX_CALLS]

    def walk(page_id, active, depth):
        if depth > MAX_DEPTH:
            raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
        current = active
        blocks = children(client, page_id, budget, context)
        containers = [b for b in blocks if b.get("type") == "child_page"
                      and (b.get("child_page") or {}).get("title") == "Active Opportunities"]
        if same_notion_id(page_id, root) and len(containers) > 1:
            raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
        for block in blocks:
            kind = block.get("type", "")
            title = (block.get("child_page") or {}).get("title", "") if kind == "child_page" else text(block)
            if kind == "child_page" and title in ("Active Opportunities", "Retired Opportunities"):
                if title == "Active Opportunities":
                    if not same_notion_id(page_id, root):
                        continue
                    container = client.call("GET", f"/pages/{block['id']}")
                    if not same_notion_id((container.get("parent") or {}).get("page_id"), root) or container.get("archived") or container.get("in_trash"):
                        raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
                # A container scopes its descendants, never its following root siblings.
                walk(block["id"], title == "Active Opportunities", depth + 1)
            elif title in ("Active Opportunities", "Retired Opportunities") and (kind.startswith("heading") or kind == "toggle"):
                current = title == "Active Opportunities"
                if block.get("has_children"):
                    walk(block["id"], current, depth + 1)
            elif kind == "child_page":
                if current is None:
                    continue
                found.append(Parent(block["id"], title, current))
            elif block.get("has_children") and kind in ("toggle", "column", "column_list"):
                walk(block["id"], current, depth + 1)

    try:
        walk(root, None, 0)
        return Scan(tuple(found), True)
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return Scan(tuple(found), False)


def child_scan(client, parent_id, context=None):
    try:
        blocks = children(client, parent_id, [MAX_CALLS], context)
        # B1 reads verified identity properties only; never guesses identities from legacy titles.
        found = []
        for block in blocks:
            if block.get("type") != "child_page":
                continue
            if context:
                context.require_time()
            if len(found) >= MAX_CALLS:
                raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
            child = explicit_child(client, block["id"])
            if child is None or not same_notion_id(child.parent_id, parent_id):
                raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
            found.append(child)
        return Scan(tuple(found), True)
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return Scan((), False)


def explicit_child(client, page_id):
    page = client.call("GET", f"/pages/{page_id}")
    if page.get("archived") or page.get("in_trash"):
        return None
    parent = page.get("parent") or {}
    from .create import parse_identity
    blocks, _ = machine_lines(client, page_id, 2)
    first = text(blocks[0]) if blocks else ""
    machine = first == MARKERS["round"]
    if not machine:
        return Child(page_id, parent.get("page_id", ""), identity_valid=False if "interview:" in first else None)
    data = parse_identity(text(blocks[1])) if len(blocks) > 1 and blocks[1].get("type") == "paragraph" else None
    if data is None:
        return Child(page_id, parent.get("page_id", ""), identity_valid=False)
    return Child(page_id, parent.get("page_id", ""), **data, identity_valid=True)


def _subtree(client, block, budget, depth=0):
    if depth > MAX_DEPTH:
        raise NotionError("INTERVIEW_PROTECTED_INCOMPLETE")
    value = {k: v for k, v in block.items() if k not in ("last_edited_time", "last_edited_by")}
    if block.get("has_children"):
        value["protected_children"] = [_subtree(client, b, budget, depth + 1) for b in children(client, block["id"], budget)]
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def protected_snapshot(client, page_id, kind="round"):
    """Call only around an authorized mutation. Hash ordered anchors and all descendant evidence privately.
    An opportunity parent may instead carry Jim's notes (NOTES; LIFE-46/LIFE-88): that region runs to the end of the page,
    through any headings Jim writes. Child pages are left out of it because Notion appends every new round page at the end of
    its parent; their contents are never read here. A UI 4 round (LIFE-46 10875) keeps them in its left column instead."""
    budget, regions = [MAX_CALLS], {}
    labels = PROTECTED + (NOTES if kind == "opportunity" else ())
    current = None
    order = []
    tops = children(client, page_id, budget)
    for block in tops:
        label = text(block)
        if current in NOTES:
            if block.get("type") != "child_page":
                regions[current].append(_subtree(client, block, budget))
            continue
        if label in labels and (block.get("type", "").startswith("heading") or block.get("type") == "toggle"):
            if label in regions:
                raise NotionError("INTERVIEW_PROTECTED_INCOMPLETE")
            current = label
            order.append((label, block.get("id")))
            regions[label] = []
        elif block.get("type", "").startswith("heading"):
            current = None
        if current:
            regions[current].append(_subtree(client, block, budget))
    if kind == "round" and not regions:
        return _round_regions(client, tops, budget)
    if not (all(label in regions for label in PROTECTED) or any(label in regions for label in NOTES)):
        return None
    return _digest([order, regions])


RAW_HEADING, RAW_TOGGLE, LIVE_PANE = "Protected Raw Notes", "Raw notes · open to write", "Live Notes"


def _round_regions(client, tops, budget):
    """UI 4: the Live Notes tab pane and the toggle under "Protected Raw Notes", both in the left of the one column list."""
    lists = [b for b in tops if b.get("type") == "column_list"]
    columns = children(client, lists[0]["id"], budget) if len(lists) == 1 else []
    left = children(client, columns[0]["id"], budget) if columns else []
    tabs = [b for b in left if b.get("type") == "tab"]
    heads = [i for i, b in enumerate(left) if b.get("type") == "heading_3" and text(b) == RAW_HEADING]
    if len(tabs) != 1 or len(heads) != 1 or len(left) <= heads[0] + 1 or left[heads[0] + 1].get("type") != "toggle":
        return None
    live = [b for b in children(client, tabs[0]["id"], budget) if text(b) == LIVE_PANE]
    if len(live) != 1:
        return None
    return _digest([_subtree(client, live[0], budget), _subtree(client, left[heads[0] + 1], budget)])


def human_tail(client, page_id, blocks=None):
    """Private digest of Jim's own content on an adoption page: every top-level block after the first line before adoption
    (blocks given), or every block after the Jim's notes heading and its one notes callout after it. Child pages excluded."""
    budget = [MAX_CALLS]
    if blocks is None:
        blocks = children(client, page_id, budget)
        heads = [i for i, b in enumerate(blocks) if text(b) in NOTES and b.get("type", "").startswith("heading")]
        if len(heads) != 1 or len(blocks) <= heads[0] + 1 or blocks[heads[0] + 1].get("type") != "callout":
            raise NotionError("INTERVIEW_PROTECTED_INCOMPLETE")
        blocks = blocks[heads[0] + 2:]
    else:
        blocks = blocks[1:]
    return _digest([_subtree(client, b, budget) for b in blocks if b.get("type") != "child_page"])


# LIFE-88: the approved UI 3c / UI 4 trees nest deeper than the two levels one Notion request may carry, and a column list,
# column, table or tab must be created with its children. write_tree sends each block with the leading children that fit and
# appends the rest to the created blocks in order; a column whose first child cannot fit starts with an empty placeholder
# paragraph that is deleted once its children are written.
MUST, TREE_MAX_CALLS = ("column_list", "column", "table", "tab"), 200
# D16.1 (Jim verified Oct 21): a page created from Jim's blank "Full Width Page" (create-page `template`) is Full width. Notion applies
# the template after the create returns, so the page's blocks are written once it has had time to.
FULL_WIDTH, TEMPLATE_WAIT = "4003c5a059268094bab5f904e1dd9c42", 8
PLACEHOLDER = {"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}


def kids(block):
    return (block.get(block.get("type")) or {}).get("children") or []


def _shell(block, depth):
    kind, inner, kept = block["type"], kids(block), []
    if depth < 2:
        for child in inner:
            plan = _shell(child, depth + 1)
            if plan is None:
                break
            kept.append((child, plan))
    payload = {**block, kind: {k: v for k, v in block[kind].items() if k != "children"}}
    placeholder = False
    if kept:
        payload[kind]["children"] = [plan[0] for _, plan in kept]
    elif inner and kind in MUST:
        if kind != "column" or depth >= 2:
            return None
        payload[kind]["children"], placeholder = [PLACEHOLDER], True
    return payload, kept, inner[len(kept):], placeholder


def _deferred(plan):
    _, kept, rest, placeholder = plan
    return bool(rest) or placeholder or any(_deferred(p) for _, p in kept)


def _fill(client, block_id, plan, budget):
    _, kept, rest, placeholder = plan
    made = children(client, block_id, budget) if placeholder or any(_deferred(p) for _, p in kept) else []
    for (_, inner), item in zip(kept, made):
        if _deferred(inner):
            _fill(client, item["id"], inner, budget)
    if rest:
        write_tree(client, block_id, rest, budget=budget)
    if placeholder:
        budget[0] -= 1
        delete_block(client, made[0]["id"])


def write_tree(client, parent_id, blocks, after=None, title=None, budget=None, icon=None, template=None):
    """Write `blocks` (nested `children` at any depth) under parent_id, or as a new page titled `title` under it; returns the
    id written into. Fails closed (NotionError) on an unwritable shape or an exhausted call budget."""
    budget = budget if budget is not None else [TREE_MAX_CALLS]
    plans = [_shell(block, 0) for block in blocks]
    if any(plan is None for plan in plans):
        raise NotionError("INTERVIEW_TREE_UNWRITABLE")
    budget[0] -= 1
    if budget[0] < 0:
        raise NotionError("INTERVIEW_TREE_BUDGET")
    body = {"children": [plan[0] for plan in plans]}
    if title is not None:
        from lifeos.platform.notion_client import rich_text
        page = {"parent": {"type": "page_id", "page_id": parent_id}, "properties": {"title": {"type": "title", "title": rich_text(title)}},
                **({"icon": icon} if icon else {})}
        if template:                                                    # a template page takes no children in the same request
            target = client.call("POST", "/pages", {**page, "template": {"type": "template_id", "template_id": template}})["id"]
            time.sleep(TEMPLATE_WAIT)
            return write_tree(client, target, blocks, budget=budget)
        target = client.call("POST", "/pages", {**page, **body})["id"]
        made = children(client, target, budget)
    else:
        target = parent_id
        made = client.call("PATCH", f"/blocks/{parent_id}/children", {**body, **({"after": after} if after else {})})["results"]
    if len(made) < len(plans):
        raise NotionError("INTERVIEW_TREE_INCOMPLETE")
    for plan, item in zip(plans, made):
        if _deferred(plan):
            _fill(client, item["id"], plan, budget)
    return target


def _plain(runs):
    """Text of rich-text runs; a mention counts by its target, never its title, so renaming a linked page changes nothing."""
    return "".join(f"@{((r.get('mention') or {}).get('page') or {}).get('id', '').replace('-', '')}" if r.get("type") == "mention"
                   else r.get("plain_text", (r.get("text") or {}).get("content", "")) for r in runs or [])


def _node(kind, body, value):
    node = {"type": kind, "text": value if kind == "child_page" else _plain(body.get("rich_text"))}
    for key in ("color", "width_ratio", "table_width", "has_column_header", "has_row_header", "is_toggleable"):
        if body.get(key) not in (None, False, "default"):
            node[key] = round(body[key], 4) if key == "width_ratio" else body[key]
    icon = body.get("icon") or {}
    if icon:
        node["icon"] = icon.get("emoji") or (icon.get("external") or {}).get("url") or icon.get("type")
    if kind == "table_row":
        node["cells"] = [_plain(cell) for cell in body.get("cells", [])]
    return node


def as_nodes(blocks):
    """Normalized nodes of a block payload as written (compare with read_tree)."""
    return [{**_node(b["type"], b[b["type"]], text(b)), **({"children": as_nodes(kids(b))} if kids(b) else {})} for b in blocks]


def region_nodes(client, blocks, budget, skip=(), depth=0):
    """Normalized nodes of `blocks` and everything under them (child pages by title only), in order; ids in `skip` left out."""
    out = []
    for block in blocks:
        if block.get("id") in skip:
            continue
        kind = block.get("type", "")
        body = block.get(kind) or {}
        node = _node(kind, body, body.get("title", "") if kind == "child_page" else text(block))
        if block.get("has_children") and kind != "child_page":
            if depth >= 8:
                raise NotionError("INTERVIEW_TREE_INCOMPLETE")
            node["children"] = region_nodes(client, children(client, block["id"], budget), budget, skip, depth + 1)
        out.append(node)
    return out


def read_tree(client, block_id, budget, skip=()):
    return region_nodes(client, children(client, block_id, budget), budget, skip)


CONTENT = ("paragraph", "bulleted_list_item", "numbered_list_item", "to_do", "quote", "table_row", "link_to_page")


def outline(nodes, depth=0, parent="", icons=True):
    """The structural outline compared with the approved pages: block types, nesting, order, column ratios, callout colours
    and icons, table shapes, heading / toggle / tab labels. Text content and table rows are data, not structure."""
    out = []
    for node in nodes:
        kind = node["type"]
        if kind in CONTENT and parent != "tab":
            continue
        label = node["text"] if kind.startswith("heading") or kind == "toggle" or parent == "tab" else ""
        extra = [f"{k}={node[k]}" for k in ("width_ratio", "color", "icon", "table_width", "has_column_header", "has_row_header")
                 if k in node and (icons or k != "icon")]
        out.append(f"{depth}:{kind}" + (f" {label}" if label else "") + (f" [{' '.join(extra)}]" if extra else ""))
        out += outline(node.get("children", []), depth + 1, kind, icons)
    return out


def tree_digest(nodes):
    return _digest(nodes)[:16]


def set_text(client, block_id, kind, rich):
    return client.call("PATCH", f"/blocks/{block_id}", {kind: {"rich_text": rich}})


def append_children(client, page_id, heading_id, blocks):
    return client.call("PATCH", f"/blocks/{page_id}/children", {"children": blocks, "after": heading_id})


def delete_block(client, block_id):
    return client.call("DELETE", f"/blocks/{block_id}")


DERIVED_MAX_CALLS = 100


def derived_blocks(client, page_id):
    """(heading, payload): the one Derived heading and the blocks it bounds.
    Notion cannot page from a heading, so the API returns the whole top-level list; blocks before the heading are
    dropped as each page arrives and only headings are compared by text. A separate call budget keeps large human
    notes from exhausting the scan budget; running out still fails closed."""
    heading, payload, ended, cursor, seen = None, [], False, None, set()
    for _ in range(DERIVED_MAX_CALLS):
        path = f"/blocks/{page_id}/children?page_size=100"
        data = client.call("GET", path + (f"&start_cursor={quote(cursor, safe='')}" if cursor else ""))
        if not isinstance(data.get("results"), list) or type(data.get("has_more")) is not bool:
            raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
        for block in data["results"]:
            kind = block.get("type")
            major = kind in ("heading_1", "heading_2")
            if major and text(block) == "Derived":
                if heading is not None:
                    raise NotionError("INTERVIEW_DERIVED_MISSING")
                heading = block
            elif heading is not None and not ended:
                if major:
                    ended = True
                else:
                    payload.append(block)
        if not data["has_more"]:
            if heading is None:
                raise NotionError("INTERVIEW_DERIVED_MISSING")
            return heading, payload
        cursor = data.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
        seen.add(cursor)
    raise NotionError("INTERVIEW_SCAN_INCOMPLETE")


def readback(client, page_id, parent_id, kind, before=None):
    try:
        page = client.call("GET", f"/pages/{page_id}")
        if page.get("archived") or page.get("in_trash"):
            return "readback_page_gone"
        if not same_notion_id((page.get("parent") or {}).get("page_id"), parent_id):
            return "readback_parent_mismatch"
        blocks, _ = machine_lines(client, page_id, 1)
        if not blocks or not is_marker(text(blocks[0]), kind):
            return "readback_marker_missing"
        after = protected_snapshot(client, page_id, kind)
        if after is None:
            return "readback_protected_missing"
        return "readback_protected_changed" if before is not None and before != after else "readback_ok"
    except NotionError as error:
        return "readback_page_gone" if str(error) == "NOTION_HTTP_404" else "readback_unreadable"
    except (KeyError, TypeError, ValueError):
        return "readback_unreadable"


def active_target(client, root, context):
    """Exactly one verified direct child page; legacy headings are not infrastructure."""
    try:
        blocks = children(client, root, [MAX_CALLS], context)
        targets = [b["id"] for b in blocks if b.get("type") == "child_page"
                   and (b.get("child_page") or {}).get("title") == "Active Opportunities"]
        if len(targets) != 1 or same_notion_id(targets[0], root):
            return None
        context.require_time()
        page = client.call("GET", f"/pages/{targets[0]}")
        titles = [p["title"] for p in page.get("properties", {}).values() if p.get("type") == "title"]
        title = "".join(p.get("plain_text", (p.get("text") or {}).get("content", "")) for p in titles[0]) if len(titles) == 1 else ""
        parent = page.get("parent") or {}
        valid = same_notion_id(parent.get("page_id"), root) and parent.get("type", "page_id") == "page_id"
        return targets[0] if valid and title == "Active Opportunities" and not page.get("archived") and not page.get("in_trash") else None
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return None


def active_shape(client, root, context):
    """GET-only probe of direct insertion infrastructure and untouched legacy headings."""
    out = {"active_heading_count": 0, "active_container_count": 0,
           "active_container_parent_verified": False, "insertion_supported": False,
           "code": "active_shape_incomplete"}
    budget = [MAX_CALLS]
    def headings(blocks, depth):
        if depth > MAX_DEPTH:
            raise DeadlineExceeded("bounded shape exhausted")
        for block in blocks:
            kind = block.get("type", "")
            if kind.startswith("heading") and text(block) == "Active Opportunities":
                out["active_heading_count"] += 1
            elif block.get("has_children") and kind in ("column_list", "column", "toggle"):
                headings(children(client, block["id"], budget, context), depth + 1)
    try:
        blocks = children(client, root, budget, context)
        out["active_container_count"] = sum(b.get("type") == "child_page" and
            (b.get("child_page") or {}).get("title") == "Active Opportunities" for b in blocks)
        headings(blocks, 0)
        if out["active_container_count"] > 1:
            out["code"] = "active_shape_ambiguous"
        elif out["active_container_count"] == 1:
            valid = active_target(client, root, context) is not None
            out["active_container_parent_verified"] = out["insertion_supported"] = valid
            out["code"] = "active_shape_supported" if valid else "active_shape_incomplete"
    except DeadlineExceeded:
        out["code"] = "active_shape_incomplete"
    except NotionError as error:
        out["code"] = "active_shape_incomplete" if str(error) == "INTERVIEW_SCAN_INCOMPLETE" else "active_shape_unreadable"
    except (KeyError, TypeError, ValueError):
        out["code"] = "active_shape_incomplete"
    return out
