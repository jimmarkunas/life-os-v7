"""Manual-only private acceptance harness: one case from a secret, through LIFE-42 progression into the existing stage. Counts and fixed codes only."""
import json
import os
from . import stage
from .identity import valid_date
from .progression import Invite, progress

KEYS = ("company", "role", "confirmed_round", "interview_date", "interviewer", "ordinal")


def _object(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate")
    return dict(pairs)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def resolve(raw):
    """The case's Progression, or None for anything but the exact contract. No Ledger rows are read here, so the link is `unverified`."""
    try:
        data = json.loads(raw, object_pairs_hook=_object)
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or set(data) != set(KEYS):
        return None
    who, ordinal = data["interviewer"], data["ordinal"]
    if not (_text(data["company"]) and _text(data["role"]) and data["confirmed_round"] is True
            and valid_date(data["interview_date"])):
        return None
    if who is not None and not _text(who):
        return None
    if ordinal is not None and (type(ordinal) is not int or ordinal <= 0):
        return None
    return progress(Invite(data["company"], data["role"], data["interview_date"], who, ordinal), rows=None)


def parse(raw):
    """(ParentQuery, RoundQuery) or None for anything but the exact contract."""
    result = resolve(raw)
    return (result.parent, result.round) if result is not None and result.code == "ready" else None


def _refuse(code):
    return {"blocked": 1, "writes": 0, "writes_planned": 0, "why": {code: 1}}


def run(limit, live, environ=os.environ, **kwargs):
    if environ.get("GITHUB_EVENT_NAME", "workflow_dispatch") != "workflow_dispatch":
        return _refuse("acceptance_not_manual")
    raw = environ.get("INTERVIEW_ACCEPTANCE_JSON")
    if not raw or not raw.strip():
        return _refuse("acceptance_secret_missing")
    result = resolve(raw)
    if result is None or result.code != "ready":
        return _refuse("acceptance_secret_invalid")
    counts = stage.run(limit, live, environ=environ, evidence=((result.parent, result.round),), **kwargs)
    return {**counts, "ledger": result.ledger}
