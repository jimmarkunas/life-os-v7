"""Deterministic, append-once Interview Derived content. LIFE-88: on a UI 4 interview page it is the Prepare tab, written once
from its not-run state; its hashes sit in the collapsed LIFE OS toggle instead of on the page. Older pages keep "Derived"."""
import hashlib
import json
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.platform.runtime import DeadlineExceeded
from . import notion, template
from .identity import resolve_parent, valid_date
from .models import Ownership, PrepEvidence, State

CAPS = {"focus": 5, "strongest_evidence": 5, "pressure_points": 5, "questions": 5}
LABELS = {"focus": "Focus", "strongest_evidence": "Strongest Evidence",
          "pressure_points": "Pressure Points", "questions": "Questions"}
MAX_ITEM = 500
DERIVED_PREFIX = "v7-interview-derived:2;"


def normalize(evidence):
    if not isinstance(evidence, PrepEvidence):
        raise ValueError("prep_invalid")
    result = {}
    total = 0
    for name, cap in CAPS.items():
        values = getattr(evidence, name)
        if not isinstance(values, tuple) or len(values) > cap:
            raise OverflowError("prep_too_large")
        normalized = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError("prep_invalid")
            value = value.strip()
            if not value:
                raise ValueError("prep_invalid")
            if len(value) > MAX_ITEM:
                raise OverflowError("prep_too_large")
            if value not in normalized:
                normalized.append(value)
        result[name] = tuple(normalized)
        total += len(normalized)
    if total == 0:
        raise ValueError("prep_invalid")
    return result


def digest(normalized):
    canonical = json.dumps({k: list(normalized[k]) for k in CAPS}, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render(normalized, current_hash=None):
    current_hash = current_hash or digest(normalized)
    full_hash = digest(normalized)
    marker = f"{DERIVED_PREFIX}{current_hash};{full_hash}"
    blocks = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(marker)}}]
    for name, label in LABELS.items():
        if normalized[name]:
            blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": rich_text(label)}})
            blocks.extend({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text(item)}} for item in normalized[name])
    return blocks


def prepare(client, page_id):
    """UI 4: (Prepare pane, its blocks, LIFE OS toggle, its lines, top blocks), or None for a page that keeps "Derived"."""
    tops = notion.children(client, page_id, [notion.MAX_CALLS])
    if not tops or tops[0].get("type") != "toggle" or notion.text(tops[0]) != notion.ROUND_FOLD:
        return None
    lists = [b for b in tops if b.get("type") == "column_list"]
    columns = notion.children(client, lists[0]["id"], [notion.MAX_CALLS]) if len(lists) == 1 else []
    tabs = [b for b in (notion.children(client, columns[0]["id"], [notion.MAX_CALLS]) if columns else []) if b.get("type") == "tab"]
    panes = [b for b in (notion.children(client, tabs[0]["id"], [notion.MAX_CALLS]) if len(tabs) == 1 else []) if notion.text(b) == "Prepare"]
    if len(panes) != 1:
        raise NotionError("INTERVIEW_DERIVED_MISSING")
    return (panes[0], notion.children(client, panes[0]["id"], [notion.MAX_CALLS]), tops[0],
            notion.children(client, tops[0]["id"], [notion.MAX_CALLS]), tops)


def _parse_pane(nodes):
    out, key = {k: [] for k in CAPS}, None
    for node in nodes:
        if node["type"] == "heading_3":
            key = next((k for k, label in template.PREP_SECTIONS if label == node["text"]), None)
        elif node["type"] == "bulleted_list_item" and key:
            out[key].append(node["text"])
    return out


def _read_pane(client, ui, ordinal):
    _, items, _, lines, _ = ui
    nodes = notion.region_nodes(client, items, [notion.TREE_MAX_CALLS])
    marks = [notion.text(b) for b in lines if notion.text(b).startswith(DERIVED_PREFIX)]
    if not marks:
        return ("empty", None, None) if nodes == notion.as_nodes(template.prepare_pane(None, ordinal)) else ("conflict", None, None)
    hashes = marks[0][len(DERIVED_PREFIX):].split(";")
    payload = _parse_pane(nodes)
    if (len(marks) == 1 and len(hashes) == 2 and digest(payload) == hashes[1]
            and nodes == notion.as_nodes(template.prepare_pane(payload, ordinal))):
        return "marker", tuple(hashes), payload
    return "conflict", None, None


def _write_pane(client, ui, merged, ordinal, marker):
    pane, items, fold, lines, tops = ui
    for item in items:
        notion.delete_block(client, item["id"])
    notion.write_tree(client, pane["id"], template.prepare_pane(merged, ordinal))
    notion.write_tree(client, fold["id"], [template.para(marker)])
    for container, label, value in ((lines, "Preparation", "accepted source-grounded preparation"),
                                    (tops[1:2], "Objective (derived)", "See Prepare.")):
        for box in (b for b in container if b.get("type") == "callout"):
            for line in notion.children(client, box["id"], [notion.MAX_CALLS]):
                if notion.text(line).startswith(f"{label}: "):
                    notion.set_text(client, line["id"], "paragraph", template.field(label, value)["paragraph"]["rich_text"])


def _read_derived(client, page_id, ordinal=None):
    ui = prepare(client, page_id)
    if ui is not None:
        return _read_pane(client, ui, ordinal)
    _, blocks = notion.derived_blocks(client, page_id)
    if not blocks:
        return "empty", None, None
    if blocks[0].get("type") != "paragraph":
        return "conflict", None, None
    first = notion.text(blocks[0])
    if first.startswith(DERIVED_PREFIX):
        hashes = first[len(DERIVED_PREFIX):].split(";")
        if len(hashes) == 2 and all(len(value) == 64 and all(c in "0123456789abcdef" for c in value) for value in hashes):
            payload = _parse_blocks(blocks)
            if payload is not None and digest(payload) == hashes[1]:
                return "marker", tuple(hashes), payload
    return "conflict", None, None


def _carry(client, children, current, context):
    if not children.complete:
        return {}
    valid = [child for child in children.items if child.identity_valid is True
             and not notion.same_notion_id(child.page_id, current.page_id)]
    if type(current.ordinal) is int and current.ordinal > 0:
        previous = [child for child in valid if type(child.ordinal) is int and 0 < child.ordinal < current.ordinal]
        if previous:
            greatest = max(child.ordinal for child in previous)
            previous = [child for child in previous if child.ordinal == greatest]
    elif valid_date(current.interview_date):
        previous = [child for child in valid if valid_date(child.interview_date) and child.interview_date < current.interview_date]
        if previous:
            latest = max(child.interview_date for child in previous)
            previous = [child for child in previous if child.interview_date == latest]
    else:
        previous = []
    if len(previous) != 1:
        return {}
    prior = previous[0]
    try:
        context.require_time()
        if notion.ownership(client, prior.page_id, "round") != Ownership.MACHINE:
            return {}
        context.require_time()
        state, _, payload = _read_derived(client, prior.page_id, prior.ordinal)
        return payload if state == "marker" else {}
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return {}


def _parse_blocks(blocks):
    out = {k: [] for k in CAPS}
    key = None
    seen = set()
    for block in blocks[1:]:
        kind = block.get("type")
        value = notion.text(block)
        if kind == "heading_3":
            key = next((k for k, label in LABELS.items() if label == value), None)
            if key is None or key in seen:
                return None
            seen.add(key)
        elif kind == "bulleted_list_item" and key:
            out[key].append(value)
        else:
            return None
    if any(not out[k] for k in seen):
        return None
    if any(len(out[k]) > CAPS[k] or any(not item.strip() or len(item) > MAX_ITEM for item in out[k]) for k in CAPS):
        return None
    canonical = []
    for name, label in LABELS.items():
        if out[name]:
            canonical.append(label)
            canonical.extend(out[name])
    if [notion.text(block) for block in blocks[1:]] != canonical:
        return None
    return out


def _merge(current, inherited):
    result = dict(current)
    for key in ("pressure_points", "questions"):
        values = list(result[key])
        for value in inherited.get(key, ()):
            if value not in values and len(values) < CAPS[key]:
                values.append(value)
        result[key] = tuple(values)
    return result


def apply(client, environ, parent_query, round_query, evidence, live, context):
    out = {"writes_planned": 0, "writes": 0, "code": "prep_invalid"}
    def finish(code):
        out["code"] = code
        return out
    try:
        current = normalize(evidence)
        current_hash = digest(current)
        context.require_time()
        target = notion.target_check(client, environ)
        if target != "target_ok": return finish(target)
        parent = resolve_parent(parent_query, notion.parent_scan(client, environ["HIRING_PIPELINE_PAGE_ID"], context))
        if parent.state != State.MATCH: return finish(parent.code)
        if notion.ownership(client, parent.page_id, "opportunity") != Ownership.MACHINE:
            return finish("human_page" if notion.ownership(client, parent.page_id, "opportunity") == Ownership.HUMAN else "ownership_unknown")
        children = notion.child_scan(client, parent.page_id, context)
        from .identity import resolve_child
        child = resolve_child(parent, round_query, children)
        if child.state != State.MATCH: return finish(child.code)
        if notion.ownership(client, child.page_id, "round") != Ownership.MACHINE:
            return finish("human_page" if notion.ownership(client, child.page_id, "round") == Ownership.HUMAN else "ownership_unknown")
        identity = next((item for item in children.items if notion.same_notion_id(item.page_id, child.page_id)), None)
        if not identity or identity.identity_valid is not True: return finish("round_identity_incomplete")
        state, marker_hash, _ = _read_derived(client, child.page_id, identity.ordinal)
        if state == "conflict": return finish("derived_conflict")
        if state == "marker": return finish("derived_match" if marker_hash[0] == current_hash else "derived_conflict")
        merged = _merge(current, _carry(client, children, identity, context))
        expected = digest(merged)
        out["writes_planned"] = 1
        if not live: return finish("derived_allowed")
        context.require_time()
        ui = prepare(client, child.page_id)
        heading = None if ui else notion.derived_blocks(client, child.page_id)[0]
        before = notion.protected_snapshot(client, child.page_id)
        if before is None: return finish("readback_protected_missing")
        if ui:
            _write_pane(client, ui, merged, identity.ordinal, f"{DERIVED_PREFIX}{current_hash};{expected}")
        else:
            notion.append_children(client, child.page_id, heading["id"], render(merged, current_hash))
        out["writes"] = 1
        after_state, after_hash, _ = _read_derived(client, child.page_id, identity.ordinal)
        if after_state != "marker" or after_hash != (current_hash, expected): return finish("derived_readback_failed")
        page = client.call("GET", f"/pages/{child.page_id}")
        if page.get("archived") or page.get("in_trash") or not notion.same_notion_id((page.get("parent") or {}).get("page_id"), parent.page_id):
            return finish("derived_readback_failed")
        if notion.ownership(client, parent.page_id, "opportunity") != Ownership.MACHINE or notion.ownership(client, child.page_id, "round") != Ownership.MACHINE:
            return finish("derived_readback_failed")
        refreshed = notion.explicit_child(client, child.page_id)
        if not refreshed or refreshed.identity_valid is not True or refreshed != identity:
            return finish("derived_readback_failed")
        if notion.protected_snapshot(client, child.page_id) != before: return finish("readback_protected_changed")
        return finish("derived_created")
    except (OverflowError, ValueError, TypeError, NotionError, DeadlineExceeded, KeyError, AttributeError) as error:
        if out["writes"]:
            return finish("derived_readback_failed")          # whatever failed after the append, the page is unverified
        if isinstance(error, OverflowError): return finish("prep_too_large")
        if isinstance(error, (ValueError, TypeError)): return finish("prep_invalid")
        if isinstance(error, NotionError) and str(error) != "INTERVIEW_DERIVED_MISSING": return finish("derived_readback_failed")
        return finish("derived_missing")
