"""LIFE-88 first gate: can the V7 Notion REST transport build and maintain the accepted UI 3c block tree? Manual only (diag.yml, stage
interview-ui-probe, typed confirmation QA); never on a tick.

Uses the Interview token (LIFE OS Interview OS) and the shared paced client. Reads the accepted UI 3c QA preview (block types and page keys
only) and writes only inside ONE probe page under the existing QA Interview OS / Opportunity Parent, found by its exact title or created
once. Each run empties that probe page (it holds nothing else), rebuilds the UI 3c structures in staged writes, updates two blocks in
place and reads everything back. Prints fixed check codes, block-type names and counts only; never a title, id, token or content."""
import os
from lifeos.platform.notion_client import Client, NotionError, rich_text

QA_PARENT = "3d43c5a0592681fdad10d75d83852fa7"        # QA — Interview OS / Opportunity Parent (LIFE OS QA Home)
UI3C = "3ff3c5a0592681aeba23e00675f097f1"             # accepted UI 3c native QA preview (LIFE-88 comment 10869); read only
TITLE = "UI 3c — REST capability probe (QA, machine-written)"
STANDARD = {"paragraph", "heading_1", "heading_2", "heading_3", "heading_4", "callout", "toggle", "column_list", "column", "table", "table_row",
            "bulleted_list_item", "numbered_list_item", "to_do", "divider", "child_page", "link_to_page", "quote", "code"}


def _code(error):
    return " ".join(part for part in (str(error)[:40], getattr(error, "detail", "")) if part)


def _text(kind, value, **extra):
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(value), **extra}}


def _row(*cells):
    return {"object": "block", "type": "table_row", "table_row": {"cells": cells}}


def _cell(value, page=None):
    return [{"type": "mention", "mention": {"page": {"id": page}}}] if page else rich_text(value)


def _table(header, rows):
    return {"object": "block", "type": "table", "table": {"table_width": len(header), "has_column_header": True, "has_row_header": False,
                                                          "children": [_row(*map(_cell, header))] + rows}}


def _children(client, block_id, budget):
    out, cursor = [], None
    while True:
        budget[0] -= 1
        if budget[0] < 0:
            raise NotionError("UI_PROBE_BUDGET")
        data = client.call("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        out += data.get("results") or []
        if not data.get("has_more"):
            return out
        cursor = data.get("next_cursor")


def _tree(client, block_id, budget, depth=0, shapes=None):
    """[(depth, type, has_children)] in document order, descending through every block that has children. `shapes` collects, per block
    type outside STANDARD, the sorted payload key names of its first block (names only, never values)."""
    out = []
    for block in _children(client, block_id, budget):
        kind = block.get("type", "")
        out.append((depth, kind, bool(block.get("has_children"))))
        if shapes is not None and kind not in STANDARD and kind not in shapes:
            payload = block.get(kind)
            shapes[kind] = sorted(payload) if isinstance(payload, dict) else [type(payload).__name__]
        if block.get("has_children") and kind != "child_page" and depth < 6:
            out += _tree(client, block["id"], budget, depth + 1, shapes)
    return out


def _append(client, parent, blocks):
    return client.call_once("PATCH", f"/blocks/{parent}/children", {"children": blocks})["results"]


def _probe_page(client, budget):
    pages = [b for b in _children(client, QA_PARENT, budget) if b.get("type") == "child_page" and (b.get("child_page") or {}).get("title") == TITLE]
    if len(pages) > 1:
        raise NotionError("UI_PROBE_DUPLICATE_PAGE")
    if pages:
        return pages[0]["id"], "reused"
    page = client.call_once("POST", "/pages", {"parent": {"type": "page_id", "page_id": QA_PARENT},
                                               "properties": {"title": {"type": "title", "title": rich_text(TITLE)}}})
    return page["id"], "created"


def _check(counts, name, action):
    try:
        result = action()
        counts["checks"][name] = "ok"
        return result
    except NotionError as error:
        counts["checks"][name] = _code(error)
        counts["failed"] += 1
        return None


def run(limit, live, environ=os.environ, client=None):
    if not live or (environ.get("PROBE_CONFIRM") or "").strip() != "QA":
        raise NotionError("UI_PROBE_CONFIRM_REQUIRED")
    client = client or Client({"NOTION_API_TOKEN": environ.get("NOTION_INTERVIEW_TOKEN") or "", "UNUSED": "interview-unused"}, source_name="UNUSED")
    counts = {"interview_ui_probe": "live", "page": "", "checks": {}, "ui3c_types": [], "ui3c_page_keys": [], "nonstandard_types": [],
              "nonstandard_shapes": {}, "probe_tree": [], "failed": 0}
    budget = [120]
    tree = _check(counts, "read_ui3c_tree", lambda: _tree(client, UI3C, budget, shapes=counts["nonstandard_shapes"]))
    if tree:
        counts["ui3c_types"] = sorted({kind for _, kind, _ in tree})
        counts["nonstandard_types"] = sorted({kind for _, kind, _ in tree} - STANDARD)
    page = _check(counts, "read_ui3c_page", lambda: client.get_page(UI3C))
    if page:
        counts["ui3c_page_keys"] = sorted(page)
    found = _check(counts, "probe_page", lambda: _probe_page(client, budget))
    if not found:
        return counts
    probe, counts["page"] = found
    for block in _check(counts, "clear_probe_page", lambda: _children(client, probe, budget)) or []:
        _check(counts, "clear_probe_page", lambda b=block: client.call_once("DELETE", f"/blocks/{b['id']}"))

    action = _check(counts, "action_center_callout", lambda: _append(client, probe, [_text("callout", "Action Center", color="blue_background")]))
    if action:
        _check(counts, "action_center_columns_67_33", lambda: _append(client, action[0]["id"], [{"object": "block", "type": "column_list", "column_list": {"children": [
            {"object": "block", "type": "column", "column": {"width_ratio": 0.67, "children": [_text("paragraph", "Primary action")]}},
            {"object": "block", "type": "column", "column": {"width_ratio": 0.33, "children": [_text("paragraph", "Secondary actions")]}}]}}]))
    main = _check(counts, "main_columns_63_37", lambda: _append(client, probe, [{"object": "block", "type": "column_list", "column_list": {"children": [
        {"object": "block", "type": "column", "column": {"width_ratio": 0.63, "children": [_text("paragraph", "Left")]}},
        {"object": "block", "type": "column", "column": {"width_ratio": 0.37, "children": [_text("paragraph", "Right")]}}]}}]))
    columns = _check(counts, "read_columns", lambda: _children(client, main[0]["id"], budget)) if main else None
    if columns and len(columns) == 2:
        left, right = columns[0]["id"], columns[1]["id"]
        for kind in counts["nonstandard_types"] or ["tab"]:
            _check(counts, f"create_{kind}", lambda kind=kind: _append(client, left, [{"object": "block", "type": kind, kind: {}}]))
        callout = _check(counts, "interviews_callout", lambda: _append(client, right, [_text("callout", "Interviews", color="gray_background")]))
        if callout:
            table = _check(counts, "table_in_callout", lambda: _append(client, callout[0]["id"], [_table(
                ("Person", "Interview Date", "Interview"), [_row(_cell("Probe person"), _cell("Not verified"), _cell("", page=QA_PARENT))])]))
            if table:
                rows = _check(counts, "read_table_rows", lambda: _children(client, table[0]["id"], budget)) or []
                if len(rows) == 2:
                    _check(counts, "update_table_row", lambda: client.call_once("PATCH", f"/blocks/{rows[1]['id']}", {"table_row": {"cells": [
                        _cell("Probe person (updated)"), _cell("Not verified"), _cell("", page=QA_PARENT)]}}))
            _check(counts, "update_callout", lambda: client.call_once("PATCH", f"/blocks/{callout[0]['id']}", {"callout": {"rich_text": rich_text("Interviews (updated)")}}))
        _check(counts, "link_to_page", lambda: _append(client, right, [{"object": "block", "type": "link_to_page", "link_to_page": {"type": "page_id", "page_id": QA_PARENT}}]))
    after = _check(counts, "read_probe_tree", lambda: _tree(client, probe, budget))
    if after:
        counts["probe_tree"] = [f"{depth}:{kind}" for depth, kind, _ in after]
    return counts
