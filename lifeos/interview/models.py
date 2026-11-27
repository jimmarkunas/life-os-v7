"""Internal identity evidence; never serialize these records into public logs."""

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    MATCH = "MATCH"
    NOT_FOUND = "NOT_FOUND"
    BLOCKED = "BLOCKED"


class Ownership(str, Enum):
    MACHINE = "MACHINE"
    HUMAN = "HUMAN"
    UNKNOWN = "UNKNOWN"
    ADOPT = "ADOPT"


@dataclass(frozen=True)
class ParentQuery:
    company: str
    role: str


@dataclass(frozen=True)
class RoundQuery:
    confirmed_round: bool
    interview_date: str | None = None
    interviewer: str | None = None
    ordinal: int | None = None
    explicit_child_page_id: str | None = None
    source: str | None = None
    requisition: str | None = None
    stable_job_key: str | None = None
    ledger_page_id: str | None = None
    calendar_ref: str | None = None
    cancelled: bool = False
    meeting: tuple | None = None
    recruiters: tuple = ()
    recruiters_state: str | None = None  # None: not connected; else "linked" | "none" | "unreadable".
    contacts: tuple = ()
    contacts_state: str | None = None  # None: no Ledger row linked; else "linked" | "none" | "unreadable".
    sent_mail: tuple = ()
    sent_mail_state: str | None = None  # None: Gmail not read; else "read" | "unreadable".
    refresh_only: bool = False


@dataclass(frozen=True)
class PrepEvidence:
    focus: tuple[str, ...] = ()
    strongest_evidence: tuple[str, ...] = ()
    pressure_points: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Parent:
    page_id: str
    title: str
    active: bool = True
    stage: str = field(default="", compare=False)


@dataclass(frozen=True)
class Child:
    page_id: str
    parent_id: str
    interview_date: str | None = None
    interviewer: str | None = None
    ordinal: int | None = None
    identity_valid: bool | None = None
    legacy: bool = False
    created: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Resolution:
    state: State
    code: str
    page_id: str | None = None


@dataclass(frozen=True)
class Scan:
    items: tuple
    complete: bool
    ledger: int = field(default=0, compare=False)
    tree_only: int = field(default=0, compare=False)
