"""LIFE-88 first gate: can the V7 Notion REST transport build and maintain the accepted UI 3c block tree? Manual only (diag.yml, stage
interview-ui-probe, typed confirmation QA); never on a tick.

Uses the Interview token (LIFE OS Interview OS) and the shared paced client. Reads the accepted UI 3c QA preview (block types and page keys
only) and writes only inside ONE probe page under the existing QA Interview OS / Opportunity Parent, found by its exact title or created
once. Each run empties that probe page (it holds nothing else), rebuilds the UI 3c structures in staged writes, updates two blocks in
place and reads everything back. Full-width gate: when Jim's blank full-width shell page exists under the same parent, it also
creates one template probe page from it there, once. Prints fixed check codes, block-type names and
counts only; never a title, id, token or content."""
import difflib
import os
import time
from lifeos.platform.notion_client import Client, NotionError, rich_text
from . import create, notion, template, view
from .models import ParentQuery, RoundQuery

QA_PARENT = "3d43c5a0592681fdad10d75d83852fa7"        # QA — Interview OS / Opportunity Parent (LIFE OS QA Home)
UI3C = "3ff3c5a0592681aeba23e00675f097f1"             # accepted UI 3c native QA preview (LIFE-88 comment 10869); read only
TITLE = "UI 3c — REST capability probe (QA, machine-written)"
SHELL_TITLE = "UI 3c — page shell (full width)"                    # Jim creates it under QA_PARENT, empty, with Full width on
TEMPLATE_TITLE = "UI 3c — template probe (QA, machine-written)"
TEMPLATE_WAIT = 8                                                    # seconds; the template is applied asynchronously
# LIFE-88 QA acceptance: real canonical evidence, read only, rendered only into QA pages under QA_PARENT.
UI4 = "3fa3c5a059268118ac0bd448ee12c199"              # approved UI 4 interview page (LIFE-46 10875); read only
LEDGER_ROW = "3ff3c5a059268172a7e6f28a539b6d99"       # Walmart's Job Ledger row (LIFE-42); read only
ROUND_PAGE = "3ff3c5a059268184a537f7678492ade8"       # Walmart Interview 1 in the Hiring Pipeline; its identity is read only
RENDER_TITLE = "UI 3c — V7 render · Walmart (QA, machine-written)"
CONTROL_TITLE = "UI 3c — V7 render · wrong-job control (QA, machine-written)"
SEED = "QA synthetic note — written once by the QA probe; every refresh must keep it."
TEXT = {"paragraph", "heading_1", "heading_2", "heading_3", "heading_4", "toggle", "callout", "quote", "bulleted_list_item", "numbered_list_item"}
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


def _subtree(tree, kind):
    """The relative "depth:type" outline under the first `kind` block of a _tree() result (types only)."""
    for index, (depth, name, _) in enumerate(tree):
        if name == kind:
            out = []
            for inner, inner_kind, _ in tree[index + 1:]:
                if inner <= depth:
                    break
                out.append(f"{inner - depth}:{inner_kind}")
            return out
    return []


def _tab(outline):
    """A tab block whose panes mirror the first-level children of the accepted preview's tab (text types only), each with one paragraph."""
    panes = [kind.split(":", 1)[1] for kind in outline if kind.startswith("1:")] or ["paragraph", "paragraph"]
    children = [_text(kind if kind in TEXT else "paragraph", f"Pane {n}", children=[_text("paragraph", f"Pane {n} content")])
                for n, kind in enumerate(panes, 1)]
    return {"object": "block", "type": "tab", "tab": {"children": children}}


def _probe_page(client, budget):
    pages = [b for b in _children(client, QA_PARENT, budget) if b.get("type") == "child_page" and (b.get("child_page") or {}).get("title") == TITLE]
    if len(pages) > 1:
        raise NotionError("UI_PROBE_DUPLICATE_PAGE")
    if pages:
        return pages[0]["id"], "reused"
    page = client.call_once("POST", "/pages", {"parent": {"type": "page_id", "page_id": QA_PARENT},
                                               "properties": {"title": {"type": "title", "title": rich_text(TITLE)}}})
    return page["id"], "created"


def _template_page(client, budget, sleep):
    """Full-width gate: create TEMPLATE_TITLE from Jim's blank full-width SHELL_TITLE page (the create-page `template` parameter),
    wait for Notion to apply it, then append one paragraph and read it back. Created once: a template applies only when a page is
    created, and this probe never trashes a page, so an existing template probe page stops the step. The API cannot read full
    width; Jim checks it by eye."""
    pages = [b for b in _children(client, QA_PARENT, budget) if b.get("type") == "child_page"]
    shells = [b["id"] for b in pages if (b.get("child_page") or {}).get("title") == SHELL_TITLE]
    olds = [b["id"] for b in pages if (b.get("child_page") or {}).get("title") == TEMPLATE_TITLE]
    if len(shells) != 1:
        raise NotionError("UI_PROBE_SHELL_MISSING" if not shells else "UI_PROBE_SHELL_DUPLICATE")
    if olds:
        raise NotionError("UI_PROBE_TEMPLATE_PAGE_EXISTS")
    page = client.call_once("POST", "/pages", {"parent": {"type": "page_id", "page_id": QA_PARENT},
                                               "properties": {"title": {"type": "title", "title": rich_text(TEMPLATE_TITLE)}},
                                               "template": {"type": "template_id", "template_id": shells[0]}})
    sleep(TEMPLATE_WAIT)                                                    # Notion applies a template after the create returns
    applied = len(_children(client, page["id"], budget))
    _append(client, page["id"], [_text("paragraph", "Machine paragraph after the template")])
    sleep(TEMPLATE_WAIT)
    return {"template_blocks_before_append": applied, "template_blocks_after_append": len(_children(client, page["id"], budget))}


def _one(client, parent, title, kind="child_page"):
    found = [b["id"] for b in notion.children(client, parent, [notion.MAX_CALLS]) if b.get("type") == kind
             and ((b.get("child_page") or {}).get("title") if kind == "child_page" else notion.text(b)) == title]
    if len(found) > 1:
        raise NotionError("UI_PROBE_DUPLICATE_PAGE")
    return found[0] if found else None


def _seed(client, parent, title):
    """Once, a synthetic note inside a protected container, standing in for Jim's own writing in QA."""
    box = _one(client, parent, title, "toggle") or _one(client, parent, title, "paragraph")
    if box and SEED not in [notion.text(b) for b in notion.children(client, box, [notion.MAX_CALLS])]:
        notion.write_tree(client, box, [template.para(SEED)])


def _round_parts(client, page):
    left = notion.children(client, notion.children(client, [b for b in notion.children(client, page, [notion.MAX_CALLS])
                           if b.get("type") == "column_list"][0]["id"], [notion.MAX_CALLS])[0]["id"], [notion.MAX_CALLS])
    return [b["id"] for b in left if b.get("type") == "tab"][0], left


def _render(client):
    """Renders Walmart's real evidence into one QA opportunity page and its QA Interview 1, then proves: replay writes nothing,
    a source change (the interview page appearing) updates the same page in place, protected notes and child pages survive,
    and the same view refuses another job's page. Returns fixed codes and structural diffs against the approved pages."""
    out, today = {}, create.today()
    props = client.call("GET", f"/pages/{LEDGER_ROW}").get("properties") or {}
    parent_query = ParentQuery(view._plain(props.get("Company")) or "", view._plain(props.get("Job")) or "")
    found = notion.explicit_child(client, ROUND_PAGE)
    out["evidence"] = {"ledger_row": "read", "round_identity": "valid" if found and found.identity_valid else "invalid"}
    if not found or not found.identity_valid:
        raise NotionError("UI_PROBE_EVIDENCE_INVALID")
    query = RoundQuery(True, found.interview_date, found.interviewer, found.ordinal, stable_job_key=view._plain(props.get("Stable Job Key")),
                       ledger_page_id=LEDGER_ROW)
    before_rounds = view.opportunity(client, parent_query, query, QA_PARENT, None, today)
    out["ledger"] = before_rounds.ledger
    page = _one(client, QA_PARENT, RENDER_TITLE)
    counts = {"writes": 0, "readback_ok": 0, "readback_failed": 0, "writes_planned": 0}
    if page is None:
        blocks = create.rendered(before_rounds, tail=None)[0]
        page = notion.write_tree(client, QA_PARENT, blocks, title=RENDER_TITLE, icon=template.PAGE_ICONS["opportunity"])
        out["create"] = create.finalize(client, page, before_rounds, blocks)
    else:
        out["source_a"] = create.refresh(client, page, before_rounds, True, counts)
    out["replay"] = create.refresh(client, page, before_rounds, True, counts)
    round_page = _one(client, page, "Interview 1")
    if round_page is None:
        round_page = notion.write_tree(client, page, template.round_page(before_rounds, query, page, create.identity_text(query)), title="Interview 1",
                                       icon=template.PAGE_ICONS["round"])
        client.call("POST", "/pages", {"parent": {"type": "page_id", "page_id": round_page},
                                       "properties": {"title": {"type": "title", "title": rich_text("Full Transcript")}}, "children": [template.para(SEED)]})
        tab, left = _round_parts(client, round_page)
        _seed(client, tab, notion.LIVE_PANE)
        raw = [b["id"] for i, b in enumerate(left) if i and notion.text(left[i - 1]) == notion.RAW_HEADING]
        notion.write_tree(client, raw[0], [template.para(SEED)])
    heads = [b["id"] for b in notion.children(client, page, [notion.MAX_CALLS]) if notion.text(b) == notion.JIM_NOTES]
    _seed(client, page, "Live Notes · protected space")
    kept = (notion.human_tail(client, page), notion.protected_snapshot(client, round_page),
            [b["id"] for b in notion.children(client, round_page, [notion.MAX_CALLS]) if b.get("type") == "child_page"])
    out["jim_notes_heading"] = len(heads)
    children = notion.child_scan(client, page)
    after_rounds = view.opportunity(client, parent_query, query, QA_PARENT, children, today)
    out["rounds_seen"] = len(after_rounds.rounds)
    out["source_b"] = create.refresh(client, page, after_rounds, True, counts)
    out["protected_after_b"] = "unchanged" if kept == (notion.human_tail(client, page), notion.protected_snapshot(client, round_page),
        [b["id"] for b in notion.children(client, round_page, [notion.MAX_CALLS]) if b.get("type") == "child_page"]) else "CHANGED"
    out["pages_under_qa_render"] = len(notion.child_scan(client, page).items)
    control = _one(client, QA_PARENT, CONTROL_TITLE)
    if control is None:
        other = view.Opportunity("QA Control", "Wrong-job control", "qa-control-not-this-job", None, None, "not_linked", None, QA_PARENT)
        blocks = create.rendered(other, tail=None)[0]
        control = notion.write_tree(client, QA_PARENT, blocks, title=CONTROL_TITLE, icon=template.PAGE_ICONS["opportunity"])
        create.finalize(client, control, other, blocks)
    snap = notion.tree_digest(notion.read_tree(client, control, [notion.TREE_MAX_CALLS]))
    out["wrong_job"] = create.refresh(client, control, after_rounds, True, counts)
    out["wrong_job_page"] = "unchanged" if snap == notion.tree_digest(notion.read_tree(client, control, [notion.TREE_MAX_CALLS])) else "CHANGED"
    for name, approved, rendered_page in (("ui3c", UI3C, page), ("ui4", UI4, round_page)):
        try:
            want = notion.outline(notion.read_tree(client, approved, [notion.TREE_MAX_CALLS]))
        except NotionError as error:
            out[f"compare_{name}"] = [_code(error)]
            continue
        got = notion.outline(notion.read_tree(client, rendered_page, [notion.TREE_MAX_CALLS]))
        out[f"compare_{name}"] = [d for d in difflib.ndiff(want, got) if d[:1] in "+-"] or ["identical"]
    return out


def _check(counts, name, action):
    try:
        result = action()
        counts["checks"][name] = "ok"
        return result
    except NotionError as error:
        counts["checks"][name] = _code(error)
        counts["failed"] += 1
        return None


def run(limit, live, environ=os.environ, client=None, sleep=time.sleep):
    if not live or (environ.get("PROBE_CONFIRM") or "").strip() != "QA":
        raise NotionError("UI_PROBE_CONFIRM_REQUIRED")
    client = client or Client({"NOTION_API_TOKEN": environ.get("NOTION_INTERVIEW_TOKEN") or "", "UNUSED": "interview-unused"}, source_name="UNUSED")
    counts = {"interview_ui_probe": "live", "page": "", "checks": {}, "ui3c_types": [], "ui3c_page_keys": [], "nonstandard_types": [],
              "nonstandard_shapes": {}, "ui3c_tab_tree": [], "column_ratios": [], "probe_tree": [], "failed": 0}
    budget = [120]
    tree = _check(counts, "read_ui3c_tree", lambda: _tree(client, UI3C, budget, shapes=counts["nonstandard_shapes"]))
    if tree:
        counts["ui3c_types"] = sorted({kind for _, kind, _ in tree})
        counts["nonstandard_types"] = sorted({kind for _, kind, _ in tree} - STANDARD)
        counts["ui3c_tab_tree"] = _subtree(tree, "tab")
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
        counts["column_ratios"] = [(column.get("column") or {}).get("width_ratio") for column in columns]
        tab = _check(counts, "create_tab", lambda: _append(client, left, [_tab(counts["ui3c_tab_tree"])]))
        if tab:
            panes = _check(counts, "read_tab_panes", lambda: _children(client, tab[0]["id"], budget)) or []
            if panes and panes[0].get("type") in TEXT:
                kind = panes[0]["type"]
                _check(counts, "update_tab_pane", lambda: client.call_once("PATCH", f"/blocks/{panes[0]['id']}", {kind: {"rich_text": rich_text("Pane 1 (updated)")}}))
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
    counts.update(_check(counts, "template_page", lambda: _template_page(client, budget, sleep)) or {})
    counts["qa_render"] = _check(counts, "qa_render", lambda: _render(client)) or {}
    return counts
