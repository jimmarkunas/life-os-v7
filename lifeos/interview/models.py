"""Internal identity evidence; never serialize these records into public logs."""
from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    MATCH = "MATCH"
    NOT_FOUND = "NOT_FOUND"
    BLOCKED = "BLOCKED"


class Ownership(str, Enum):
    MACHINE = "MACHINE"
    HUMAN = "HUMAN"
    UNKNOWN = "UNKNOWN"
    ADOPT = "ADOPT"                            # LIFE-46: a human page whose exact first line asks LIFE OS to adopt it


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
    source: str | None = None                  # LIFE-42: the exact request message link (private page content only)
    requisition: str | None = None
    stable_job_key: str | None = None
    ledger_page_id: str | None = None          # LIFE-46: the exact Job Ledger row progression linked (private page content only)
    calendar_ref: str | None = None            # LIFE-46 E07: a short hash of the accepted Calendar event's id (never the id), kept in the round's Logistics
    cancelled: bool = False                    # LIFE-46 E07: that event was cancelled in Calendar; the round keeps its date and identity
    meeting: tuple | None = None               # E10: (join link, location) from that accepted invite, either may be None; private page content only
    recruiters: tuple = ()                     # LIFE-78 E10: Recruiters rows whose Source URL is exactly `source`, as (person, company, thread url); private page content only
    recruiters_state: str | None = None        # None = the Recruiters source is not connected to this run; "linked" | "none" | "unreadable"


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


@dataclass(frozen=True)
class Child:
    page_id: str
    parent_id: str
    interview_date: str | None = None
    interviewer: str | None = None
    ordinal: int | None = None
    identity_valid: bool | None = None
    legacy: bool = False                       # LIFE-88: a hand-made round page whose number and date Jim let V7 read from its title (named pursuits only); shown, never written


@dataclass(frozen=True)
class Resolution:
    state: State
    code: str
    page_id: str | None = None


@dataclass(frozen=True)
class Scan:
    items: tuple
    complete: bool
