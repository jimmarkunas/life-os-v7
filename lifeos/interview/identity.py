"""Fail-closed identity decisions with platform-owned normalization."""
from datetime import date
from lifeos.platform.names import norm, same_company, same_role, split_title
from .models import Ownership, Resolution, State


def valid_date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def resolve_parent(query, scan):
    if not scan.complete:
        return Resolution(State.BLOCKED, "pipeline_incomplete")
    if not isinstance(query.company, str) or not isinstance(query.role, str) or not query.company.strip() or not query.role.strip():
        return Resolution(State.BLOCKED, "identity_invalid")
    matches = []
    for parent in scan.items:
        if not parent.active:
            continue
        company, role = split_title(parent.title)
        if not company.strip() or not role.strip():
            return Resolution(State.BLOCKED, "identity_invalid")
        if same_company(company, query.company) and same_role(role, query.role):
            matches.append(parent)
    if len(matches) > 1:
        return Resolution(State.BLOCKED, "parent_ambiguous")
    return Resolution(State.MATCH, "parent_match", matches[0].page_id) if matches else Resolution(State.NOT_FOUND, "parent_not_found")


def resolve_child(parent, query, scan, explicit=None):
    if parent.state != State.MATCH:
        return Resolution(State.BLOCKED, parent.code)
    if not scan.complete:
        return Resolution(State.BLOCKED, "child_scan_incomplete")
    candidates = [child for child in scan.items if child.parent_id == parent.page_id]
    if query.explicit_child_page_id:
        if explicit is not None and explicit.parent_id != parent.page_id:
            return Resolution(State.BLOCKED, "child_wrong_parent")
        candidates = [c for c in candidates if c.page_id == query.explicit_child_page_id]
        if explicit is not None and not candidates:
            return Resolution(State.BLOCKED, "child_scan_incomplete")
    else:
        if query.interview_date is not None:
            if not valid_date(query.interview_date):
                return Resolution(State.BLOCKED, "round_identity_incomplete")
            if any(not valid_date(c.interview_date) for c in candidates):
                return Resolution(State.BLOCKED, "round_identity_incomplete")
            candidates = [c for c in candidates if c.interview_date == query.interview_date]
        elif query.interviewer and query.interviewer.strip():
            if any(not c.interviewer for c in candidates):
                return Resolution(State.BLOCKED, "round_identity_incomplete")
            candidates = [c for c in candidates if norm(c.interviewer) == norm(query.interviewer)]
        elif type(query.ordinal) is int and query.ordinal > 0:
            if any(type(c.ordinal) is not int or c.ordinal <= 0 for c in candidates):
                return Resolution(State.BLOCKED, "round_identity_incomplete")
            candidates = [c for c in candidates if c.ordinal == query.ordinal]
        else:
            return Resolution(State.BLOCKED, "round_identity_incomplete")
    if not query.explicit_child_page_id and len(candidates) > 1 and query.interviewer:
        candidates = [c for c in candidates if norm(c.interviewer) == norm(query.interviewer)]
    if not query.explicit_child_page_id and len(candidates) > 1 and query.ordinal is not None:
        candidates = [c for c in candidates if c.ordinal == query.ordinal]
    if len(candidates) > 1:
        return Resolution(State.BLOCKED, "child_ambiguous")
    return Resolution(State.MATCH, "child_match", candidates[0].page_id) if candidates else Resolution(State.NOT_FOUND, "child_not_found")


def creation_eligibility(target, parent, ownership, child, scan, query):
    if target != "target_ok":
        return target
    if parent.state != State.MATCH:
        return parent.code
    if ownership != Ownership.MACHINE:
        return "human_page" if ownership == Ownership.HUMAN else "ownership_unknown"
    if not scan.complete:
        return "child_scan_incomplete"
    if child.state != State.NOT_FOUND:
        return child.code
    if query.confirmed_round is not True:
        return "round_unconfirmed"
    if not valid_date(query.interview_date):
        return "round_date_missing"
    if not (query.interviewer and query.interviewer.strip()) and not (type(query.ordinal) is int and query.ordinal > 0):
        return "round_identity_incomplete"
    return "create_allowed"
