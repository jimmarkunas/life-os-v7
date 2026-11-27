"""Manual-only private acceptance harness: one case from a secret, through progression into the stage.

Counts and fixed codes only. Two exact secret shapes: the dated shape (KEYS) reads no Ledger rows, so its link stays
`unverified`; the Scheduling Pending shape (PENDING_KEYS) has no date and reads the company's Job Ledger rows
(read-only) so progression can confirm the exact job. A refusal is its fixed progression code; an unreadable Ledger
is `ledger_unreadable`."""
import json
import os
from lifeos.platform import names, notion_pages, notion_text
from lifeos.platform.notion_client import Client, NotionError
from . import stage
from .identity import valid_date
from .progression import Invite, progress

KEYS = ("company", "role", "confirmed_round", "interview_date", "interviewer", "ordinal")
PENDING_KEYS = KEYS + ("source", "requisition", "stable_job_key")
LEDGER_PAGES = 10


def _object(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate")
    return dict(pairs)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _plain(prop, kind):
    return notion_text.plain((prop or {}).get(kind)).strip()


def ledger_rows(company, environ=os.environ):
    """Live Job Ledger rows whose Company contains the company's first word, read completely or refused."""
    words = names.tokens(company)
    if not words:
        raise NotionError("INTERVIEW_LEDGER_UNREADABLE")
    client = Client(environ, token_name="NOTION_INTERVIEW_TOKEN", source_name="NOTION_JOB_LEDGER_DATA_SOURCE_ID")
    rows = []
    for page in notion_pages.query(client, None, {"page_size": 100, "filter": {"property": "Company", "rich_text": {"contains": words[0]}}}, NotionError,
                                   incomplete="INTERVIEW_LEDGER_UNREADABLE", limit=LEDGER_PAGES):
        for row in page["results"]:
            if row.get("archived") or row.get("in_trash"):
                continue
            props = row.get("properties") or {}
            rows.append({"page_id": row.get("id"), "company": _plain(props.get("Company"), "rich_text"), "role": _plain(props.get("Job"), "title"),
                         "stable_job_key": _plain(props.get("Stable Job Key"), "rich_text"), "requisition": None})
    return rows


def _pending(data, read_rows):
    who, ordinal = data["interviewer"], data["ordinal"]
    texts = (who, data["source"], data["requisition"], data["stable_job_key"])
    if not (_text(data["company"]) and _text(data["role"]) and data["confirmed_round"] is True and data["interview_date"] is None):
        return None
    if any(v is not None and not _text(v) for v in texts) or (ordinal is not None and (type(ordinal) is not int or ordinal != 1)):
        return None
    invite = Invite(data["company"], data["role"], None, who, ordinal, data["requisition"], data["stable_job_key"], data["source"])
    return progress(invite, read_rows(data["company"]))


def resolve(raw, read_rows=None):
    """The case's Progression, or None for anything but an exact contract."""
    try:
        data = json.loads(raw, object_pairs_hook=_object)
    except (TypeError, ValueError, RecursionError):
        return None
    if isinstance(data, dict) and set(data) == set(PENDING_KEYS):
        return _pending(data, read_rows or ledger_rows)
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
    result = resolve(raw)
    return (result.parent, result.round) if result is not None and result.code == "ready" else None


def _refuse(code):
    return {"blocked": 1, "writes": 0, "writes_planned": 0, "why": {code: 1}}


def run(limit, live, environ=os.environ, read_rows=None, **kwargs):
    if environ.get("GITHUB_EVENT_NAME", "workflow_dispatch") != "workflow_dispatch":
        return _refuse("acceptance_not_manual")
    raw = environ.get("INTERVIEW_ACCEPTANCE_JSON")
    if not raw or not raw.strip():
        return _refuse("acceptance_secret_missing")
    try:
        result = resolve(raw, read_rows or (lambda company: ledger_rows(company, environ)))
    except NotionError:
        return _refuse("ledger_unreadable")
    if result is None:
        return _refuse("acceptance_secret_invalid")
    if result.code != "ready":
        pending = isinstance(json.loads(raw), dict) and set(json.loads(raw)) == set(PENDING_KEYS)
        return {**_refuse(result.code if pending else "acceptance_secret_invalid"), "ledger": result.ledger}
    counts = stage.run(limit, live, environ=environ, evidence=((result.parent, result.round),), **kwargs)
    return {**counts, "ledger": result.ledger}
