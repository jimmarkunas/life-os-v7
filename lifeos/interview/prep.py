"""Deterministic, append-once Interview Derived content."""
import hashlib
import json
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.platform.runtime import DeadlineExceeded
from . import notion
from .identity import resolve_parent, valid_date
from .models import Ownership, PrepEvidence, State

CAPS = {"focus": 5, "strongest_evidence": 5, "pressure_points": 5, "questions": 5}
LABELS = {"focus": "Focus", "strongest_evidence": "Strongest Evidence",
          "pressure_points": "Pressure Points", "questions": "Questions"}
MAX_ITEM = 500
DERIVED_PREFIX = "v7-interview-derived:1;"


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


def render(normalized):
    blocks = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(DERIVED_PREFIX + digest(normalized))}}]
    for name, label in LABELS.items():
        if normalized[name]:
            blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": rich_text(label)}})
            blocks.extend({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text(item)}} for item in normalized[name])
    return blocks


def _read_derived(client, page_id):
    _, _, blocks = notion.derived_blocks(client, page_id)
    if not blocks:
        return "empty", None
    first = notion.text(blocks[0])
    if first.startswith(DERIVED_PREFIX) and len(first) == len(DERIVED_PREFIX) + 64:
        marker = first[len(DERIVED_PREFIX):]
        if all(c in "0123456789abcdef" for c in marker):
            payload = _parse_blocks(blocks)
            return ("marker", marker) if payload is not None and digest(payload) == marker else ("conflict", None)
    return "conflict", None


def _children(client, parent_id, context):
    from .models import Child, Scan
    try:
        blocks = notion.children(client, parent_id, [notion.MAX_CALLS], context)
        found = []
        for block in blocks:
            if block.get("type") != "child_page":
                continue
            try:
                found.append(notion.explicit_child(client, block["id"]))
            except (NotionError, KeyError, TypeError, ValueError):
                found.append(Child(block.get("id", ""), parent_id, identity_valid=False))
        return Scan(tuple(item for item in found if item is not None), True)
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return Scan((), False)


def _carry(client, parent_id, current, current_input, context):
    scan = _children(client, parent_id, context)
    if not scan.complete:
        return {}, False
    valid = []
    try:
        for child in scan.items:
            if child.identity_valid is not True:
                continue
            owner = notion.ownership(client, child.page_id, "round")
            if owner != Ownership.MACHINE:
                continue
            data = notion.explicit_child(client, child.page_id)
            if not data or data.identity_valid is not True:
                continue
            valid.append((child, data))
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return {}, False
    previous = []
    if type(current.ordinal) is int and current.ordinal > 0:
        previous = [(c, d) for c, d in valid if type(d.ordinal) is int and 0 < d.ordinal < current.ordinal]
        if previous:
            greatest = max(d.ordinal for _, d in previous)
            previous = [(c, d) for c, d in previous if d.ordinal == greatest]
    elif valid_date(current.interview_date):
        previous = [(c, d) for c, d in valid if valid_date(d.interview_date) and d.interview_date < current.interview_date]
        if previous:
            latest = max(d.interview_date for _, d in previous)
            previous = [(c, d) for c, d in previous if d.interview_date == latest]
    if len(previous) != 1 or notion.same_notion_id(previous[0][0].page_id, current.page_id):
        return {}, False
    try:
        state, _ = _read_derived(client, previous[0][0].page_id)
        if state != "marker":
            return {}, False
    except (NotionError, KeyError, TypeError, ValueError):
        return {}, False
    try:
        return _parse_payload(client, previous[0][0].page_id), True
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return {}, False


def _parse_payload(client, page_id):
    _, _, blocks = notion.derived_blocks(client, page_id)
    return _parse_blocks(blocks) or {}


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
            if value not in values:
                values.append(value)
        if len(values) > CAPS[key]:
            return None
        result[key] = tuple(values)
    return result


def apply(client, environ, parent_query, round_query, evidence, live, context):
    out = {"writes_planned": 0, "writes": 0, "code": "prep_invalid"}
    def finish(code):
        out["code"] = code
        return out
    try:
        current = normalize(evidence)
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
        identity = notion.explicit_child(client, child.page_id)
        if not identity or identity.identity_valid is not True: return finish("round_identity_incomplete")
        before = notion.protected_snapshot(client, child.page_id)
        if before is None: return finish("readback_protected_missing")
        state, marker_hash = _read_derived(client, child.page_id)
        if state == "conflict": return finish("derived_conflict")
        inherited, _ = _carry(client, parent.page_id, identity, current, context)
        merged = _merge(current, inherited)
        if merged is None: return finish("prep_too_large")
        expected = digest(merged)
        if state == "marker": return finish("derived_match" if marker_hash == expected else "derived_conflict")
        out["writes_planned"] = 1
        if not live: return finish("derived_allowed")
        context.require_time()
        _, heading, _ = notion.derived_blocks(client, child.page_id)
        notion.append_children(client, child.page_id, heading["id"], render(merged))
        out["writes"] = 1
        after_state, after_hash = _read_derived(client, child.page_id)
        if after_state != "marker" or after_hash != expected: return finish("derived_readback_failed")
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
    except OverflowError: return finish("prep_too_large")
    except (ValueError, TypeError): return finish("prep_invalid")
    except NotionError as error:
        if out["writes"]: return finish("derived_readback_failed")
        return finish("derived_missing" if str(error) == "INTERVIEW_DERIVED_MISSING" else "derived_readback_failed")
    except (DeadlineExceeded, KeyError, AttributeError):
        return finish("derived_readback_failed" if out["writes"] else "derived_missing")
