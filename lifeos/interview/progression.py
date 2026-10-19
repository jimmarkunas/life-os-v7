"""LIFE-42: one verified interview request -> the existing Interview OS writer's queries, or a fail-closed refusal.

Pure: no Notion, Gmail or Ledger I/O. The caller hands in the invite's evidence and, when it has them, the Job Ledger rows
already read for that company. The Ledger link is made only by an exact Stable Job Key or requisition match; a
company/title match alone is reported as `unverified`, and two same-title rows with no requisition to tell them apart
block (D218). Never serialize Invite or Ledger rows into public logs.
"""
from dataclasses import dataclass
from lifeos.platform.names import norm, same_company, same_role
from .identity import valid_date
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
    """('linked', page_id) | ('unverified', None) | ('none', None) | ('requisition_ambiguous', None)."""
    key, req = _text(invite.stable_job_key), _text(invite.requisition)
    same = [r for r in rows if same_company(r.get("company") or "", invite.company) and same_role(r.get("role") or "", invite.role)]
    for field, value in (("stable_job_key", key), ("requisition", req)):
        if value:
            hits = [r for r in same if norm(r.get(field) or "") == norm(value)]
            if len(hits) == 1:
                return "linked", hits[0]["page_id"]
            if len(hits) > 1:
                return "requisition_ambiguous", None
    if len(same) > 1:
        return "requisition_ambiguous", None
    return ("unverified", None) if same else ("none", None)


def progress(invite, rows=()):
    company, role = _text(invite.company), _text(invite.role)
    if not company or not role:
        return Progression("identity_invalid")
    if not valid_date(invite.interview_date):
        return Progression("round_date_missing")
    who = _text(invite.interviewer)
    ordinal = invite.ordinal if type(invite.ordinal) is int and invite.ordinal > 0 else None
    if not who and ordinal is None:
        return Progression("round_identity_incomplete")
    ledger, page_id = ledger_link(invite, rows)
    if ledger == "requisition_ambiguous":
        return Progression(ledger, ledger=ledger)
    return Progression("ready", ParentQuery(company, role), RoundQuery(True, invite.interview_date, who, ordinal), ledger, page_id)
