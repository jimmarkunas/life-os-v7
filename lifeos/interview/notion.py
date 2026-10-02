"""Notion evidence and reusable protected read-back primitives."""
import hashlib
import json
import os
from urllib.parse import quote
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.runtime import DeadlineExceeded
from .models import Child, Ownership, Parent, Scan

MARKERS = {kind: f"v7-interview:1;owner=machine;kind={kind}" for kind in ("opportunity", "round")}
PROTECTED = ("Live Notes", "Raw Notes")
MAX_CALLS, MAX_DEPTH = 40, 4


def environment(environ):
    return {"NOTION_API_TOKEN": (environ.get("NOTION_INTERVIEW_TOKEN") or "").strip(),
            "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "interview-unused"}


def make_client(environ=os.environ):
    return Client(environment(environ))


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
        data = client.call("GET", f"/blocks/{page_id}/children?page_size=1")
        blocks = data["results"]
        first = text(blocks[0]) if blocks else ""
        if first == MARKERS.get(kind):
            return Ownership.MACHINE
        return Ownership.UNKNOWN if "interview:" in first else Ownership.HUMAN
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
        if page_id == root and len(containers) > 1:
            raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
        for block in blocks:
            kind = block.get("type", "")
            title = (block.get("child_page") or {}).get("title", "") if kind == "child_page" else text(block)
            if kind == "child_page" and title in ("Active Opportunities", "Retired Opportunities"):
                if title == "Active Opportunities":
                    if page_id != root:
                        continue
                    container = client.call("GET", f"/pages/{block['id']}")
                    if (container.get("parent") or {}).get("page_id") != root or container.get("archived") or container.get("in_trash"):
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
            if child is None or child.parent_id != parent_id:
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
    blocks = client.call("GET", f"/blocks/{page_id}/children?page_size=2")["results"]
    first = text(blocks[0]) if blocks else ""
    machine = first == MARKERS["round"]
    if not machine:
        return Child(page_id, parent.get("page_id", ""), identity_valid=False if "interview:" in first else None)
    data = parse_identity(text(blocks[1])) if len(blocks) > 1 and blocks[1].get("type") == "paragraph" else None
    if data is None:
        return Child(page_id, parent.get("page_id", ""), identity_valid=False)
    return Child(page_id, parent.get("page_id", ""), **data, identity_valid=True)


def protected_snapshot(client, page_id):
    """Call only around an authorized mutation. Hash ordered anchors and all descendant evidence privately."""
    budget, regions = [MAX_CALLS], {}

    def subtree(block, depth):
        if depth > MAX_DEPTH:
            raise NotionError("INTERVIEW_PROTECTED_INCOMPLETE")
        value = {k: v for k, v in block.items() if k not in ("last_edited_time", "last_edited_by")}
        if block.get("has_children"):
            value["protected_children"] = [subtree(b, depth + 1) for b in children(client, block["id"], budget)]
        return value

    current = None
    order = []
    for block in children(client, page_id, budget):
        label = text(block)
        if label in PROTECTED and (block.get("type", "").startswith("heading") or block.get("type") == "toggle"):
            if label in regions:
                raise NotionError("INTERVIEW_PROTECTED_INCOMPLETE")
            current = label
            order.append((label, block.get("id")))
            regions[label] = []
        elif block.get("type", "").startswith("heading"):
            current = None
        if current:
            regions[current].append(subtree(block, 0))
    if any(label not in regions for label in PROTECTED):
        return None
    return hashlib.sha256(json.dumps([order, regions], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def readback(client, page_id, parent_id, kind, before=None):
    try:
        page = client.call("GET", f"/pages/{page_id}")
        if page.get("archived") or page.get("in_trash"):
            return "readback_page_gone"
        if (page.get("parent") or {}).get("page_id") != parent_id:
            return "readback_parent_mismatch"
        blocks = client.call("GET", f"/blocks/{page_id}/children?page_size=1")["results"]
        if not blocks or text(blocks[0]) != MARKERS.get(kind):
            return "readback_marker_missing"
        after = protected_snapshot(client, page_id)
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
        if len(targets) != 1 or targets[0] == root:
            return None
        context.require_time()
        page = client.call("GET", f"/pages/{targets[0]}")
        titles = [p["title"] for p in page.get("properties", {}).values() if p.get("type") == "title"]
        title = "".join(p.get("plain_text", (p.get("text") or {}).get("content", "")) for p in titles[0]) if len(titles) == 1 else ""
        parent = page.get("parent") or {}
        valid = parent.get("page_id") == root and parent.get("type", "page_id") == "page_id"
        return targets[0] if valid and title == "Active Opportunities" and not page.get("archived") and not page.get("in_trash") else None
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return None


def insert_page(client, parent_id, title, blocks):
    from lifeos.platform.notion_client import rich_text
    return client.call("POST", "/pages", {"parent": {"type": "page_id", "page_id": parent_id},
                      "properties": {"title": {"type": "title", "title": rich_text(title)}}, "children": blocks})


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
