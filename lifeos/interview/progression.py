"""Map one verified interview request to the Interview OS writer's queries, or fail closed.

Pure (no Notion, Gmail or Ledger I/O); never log Invite or Ledger rows. The Ledger link needs an exact Stable Job Key
or requisition match; a company/title match alone is `unverified`, as is any request when no rows were read
(rows=None). Same-title rows with no requisition, or a key and requisition naming different rows, block.

A request with no interview date ("Scheduling Pending") is Interview 1 only with the exact request message link and a
key or requisition the Ledger rows confirm (`linked`); anything weaker never creates a child.
"""
from dataclasses import dataclass, replace
from lifeos.platform.names import norm, same_company, same_role
from .identity import scheduling_pending, valid_date
from .models import ParentQuery, RoundQuery


@dataclass(frozen=True)
class Invite:
    company: str
    role: str
    interview_date: str | None
    interviewer: str | None = None
    ordinal: int | None = None
    requisition: str | None = None
    stable_job_key: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class Progression:
    code: str
    parent: ParentQuery | None = None
    round: RoundQuery | None = None
    ledger: str = "none"
    ledger_page_id: str | None = None


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def ledger_link(invite, rows):
    """-> ('linked', page_id) | ('unverified', None) | ('none', None) | ('requisition_ambiguous' |
    'identity_conflict', None)."""
    if rows is None:
        return "unverified", None
    ids = {"stable_job_key": _text(invite.stable_job_key), "requisition": _text(invite.requisition)}
    same = [r for r in rows if same_company(r.get("company") or "", invite.company) and same_role(r.get("role") or "", invite.role)]
    hits = {}
    for field, value in ids.items():
        if value:
            found = [r for r in same if norm(r.get(field) or "") == norm(value)]
            if len(found) > 1:
                return "requisition_ambiguous", None
            if found:
                hits[field] = found[0]
    if len({r["page_id"] for r in hits.values()}) > 1:
        return "identity_conflict", None
    if hits:
        row = next(iter(hits.values()))
        if any(value and _text(row.get(field)) and norm(row[field]) != norm(value) for field, value in ids.items()):
            return "identity_conflict", None
        return "linked", row["page_id"]
    if len(same) > 1:
        return "requisition_ambiguous", None
    return ("unverified", None) if same else ("none", None)


def progress(invite, rows=()):
    company, role = _text(invite.company), _text(invite.role)
    if not company or not role:
        return Progression("identity_invalid")
    if invite.interview_date is None and (invite.ordinal is None or (type(invite.ordinal) is int and invite.ordinal == 1)):
        return _pending(invite, company, role, rows)
    if not valid_date(invite.interview_date):
        return Progression("round_date_missing")
    who = _text(invite.interviewer)
    ordinal = invite.ordinal if type(invite.ordinal) is int and invite.ordinal > 0 else None
    if not who and ordinal is None:
        return Progression("round_identity_incomplete")
    ledger, page_id = ledger_link(invite, rows)
    if ledger in ("requisition_ambiguous", "identity_conflict"):
        return Progression(ledger, ledger=ledger)
    return Progression("ready", ParentQuery(company, role), RoundQuery(True, invite.interview_date, who, ordinal, ledger_page_id=page_id), ledger, page_id)


def _pending(invite, company, role, rows):
    source = _text(invite.source)
    query = RoundQuery(True, None, _text(invite.interviewer), 1, source=source, requisition=_text(invite.requisition),
                       stable_job_key=_text(invite.stable_job_key))
    if not scheduling_pending(query):
        return Progression("request_source_missing")
    if not query.requisition and not query.stable_job_key:
        return Progression("ledger_unverified", ledger="none")
    ledger, page_id = ledger_link(invite, rows)
    if ledger != "linked":
        return Progression(ledger if ledger in ("requisition_ambiguous", "identity_conflict") else "ledger_unverified", ledger=ledger)
    return Progression("ready", ParentQuery(company, role), replace(query, ledger_page_id=page_id), ledger, page_id)
