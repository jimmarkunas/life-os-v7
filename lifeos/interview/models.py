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


@dataclass(frozen=True)
class Resolution:
    state: State
    code: str
    page_id: str | None = None


@dataclass(frozen=True)
class Scan:
    items: tuple
    complete: bool
