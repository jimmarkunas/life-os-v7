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
    if blocks[0].get("type") != "paragraph":
        return "conflict", None
    first = notion.text(blocks[0])
    if first.startswith(DERIVED_PREFIX) and len(first) == len(DERIVED_PREFIX) + 64:
        marker = first[len(DERIVED_PREFIX):]
        if all(c in "0123456789abcdef" for c in marker):
            payload = _parse_blocks(blocks)
            return ("marker", marker) if payload is not None and digest(payload) == marker else ("conflict", None)
    return "conflict", None


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
        state, _ = _read_derived(client, prior.page_id)
        context.require_time()
        return _parse_payload(client, prior.page_id) if state == "marker" else {}
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return {}


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
        state, marker_hash = _read_derived(client, child.page_id)
        if state == "conflict": return finish("derived_conflict")
        inherited = _carry(client, children, identity, context)
        merged = _merge(current, inherited)
        expected = digest(merged)
        if state == "marker": return finish("derived_match" if marker_hash == expected else "derived_conflict")
        out["writes_planned"] = 1
        if not live: return finish("derived_allowed")
        context.require_time()
        _, heading, _ = notion.derived_blocks(client, child.page_id)
        before = notion.protected_snapshot(client, child.page_id)
        if before is None: return finish("readback_protected_missing")
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
