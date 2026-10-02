"""Private Interview Advisor contracts and deterministic B3 compiler."""
from dataclasses import dataclass
from datetime import date
from enum import Enum
import hashlib
import json
import re
from typing import Protocol

from .models import PrepEvidence
from .prep import normalize as normalize_prep

_HASH = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_ID = re.compile(r"^E-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CODES = {"advisor_input_invalid", "advisor_manifest_invalid", "advisor_source_invalid",
          "advisor_evidence_invalid", "advisor_draft_invalid", "advisor_ref_missing",
          "advisor_evidence_missing", "advisor_evidence_inactive", "advisor_compile_too_large"}


class AdvisorContractError(ValueError):
    """A private-data-safe, fixed-code contract failure."""
    def __init__(self, code):
        self.code = code if code in _CODES else "advisor_input_invalid"
        super().__init__(self.code)

    def __str__(self):
        return self.code


def _fail(condition, code):
    if not condition:
        raise AdvisorContractError(code)


def _text(value, code, limit):
    _fail(isinstance(value, str) and bool(value.strip()) and len(value) <= limit, code)


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    version: int

    def __post_init__(self):
        _fail(isinstance(self.evidence_id, str) and len(self.evidence_id) <= 64
              and _EVIDENCE_ID.fullmatch(self.evidence_id) is not None
              and type(self.version) is int and self.version > 0, "advisor_evidence_invalid")

    def canonical(self):
        return f"{self.evidence_id}@{self.version}"

    @classmethod
    def parse(cls, value):
        if not isinstance(value, str) or value.count("@") != 1:
            raise AdvisorContractError("advisor_evidence_invalid")
        identity, version = value.split("@")
        if not version.isascii() or not version.isdigit() or version.startswith("0"):
            raise AdvisorContractError("advisor_evidence_invalid")
        try:
            result = cls(identity, int(version))
        except (ValueError, TypeError):
            raise AdvisorContractError("advisor_evidence_invalid") from None
        _fail(result.canonical() == value, "advisor_evidence_invalid")
        return result


@dataclass(frozen=True)
class CandidateEvidence:
    ref: EvidenceRef
    canonical_text: str
    source_ref: str
    tags: tuple[str, ...] = ()
    active: bool = True

    def __post_init__(self):
        _fail(isinstance(self.ref, EvidenceRef), "advisor_evidence_invalid")
        _text(self.canonical_text, "advisor_evidence_invalid", 500)
        _text(self.source_ref, "advisor_evidence_invalid", 300)
        _fail(isinstance(self.tags, tuple) and len(self.tags) <= 16
              and all(isinstance(tag, str) and tag.strip() and len(tag) <= 80 for tag in self.tags)
              and type(self.active) is bool, "advisor_evidence_invalid")


class SourceKind(str, Enum):
    JOB_DESCRIPTION = "job_description"
    COMPANY = "company"
    INTERVIEWER = "interviewer"
    PRIOR_DERIVED = "prior_derived"
    GUIDANCE = "guidance"
    ACCEPTED_SIGNAL = "accepted_signal"
    CANDIDATE_PROFILE = "candidate_profile"
    STRAIGHT_LINE_DOCTRINE = "straight_line_doctrine"
    GAME_THEORY_DOCTRINE = "game_theory_doctrine"


@dataclass(frozen=True)
class AdvisorSource:
    source_id: str
    kind: SourceKind
    text: str
    version: int = 1
    source_round: str | None = None
    active: bool = True

    def __post_init__(self):
        _fail(isinstance(self.source_id, str) and len(self.source_id) <= 120
              and _SOURCE_ID.fullmatch(self.source_id) is not None and "://" not in self.source_id,
              "advisor_source_invalid")
        _fail(isinstance(self.kind, SourceKind), "advisor_source_invalid")
        _text(self.text, "advisor_source_invalid", 20000)
        _fail(type(self.version) is int and self.version > 0 and type(self.active) is bool,
              "advisor_source_invalid")
        if self.kind == SourceKind.GUIDANCE:
            _text(self.text, "advisor_source_invalid", 300)
        if self.kind == SourceKind.ACCEPTED_SIGNAL:
            _text(self.text, "advisor_source_invalid", 300)
            _text(self.source_round, "advisor_source_invalid", 120)
        elif self.source_round is not None:
            _fail(False, "advisor_source_invalid")

    def canonical_ref(self):
        return f"{self.source_id}@{self.version}"


@dataclass(frozen=True)
class AdvisorManifest:
    straight_line_version: str
    straight_line_hash: str
    game_theory_version: str
    game_theory_hash: str
    candidate_profile_version: str
    candidate_profile_hash: str
    evidence_bank_version: str
    evidence_bank_hash: str
    advisor_prompt_version: str

    def __post_init__(self):
        for name in ("straight_line_version", "game_theory_version", "candidate_profile_version",
                     "evidence_bank_version", "advisor_prompt_version"):
            _text(getattr(self, name), "advisor_manifest_invalid", 80)
        for name in ("straight_line_hash", "game_theory_hash", "candidate_profile_hash", "evidence_bank_hash"):
            _fail(isinstance(getattr(self, name), str) and _HASH.fullmatch(getattr(self, name)) is not None,
                  "advisor_manifest_invalid")


@dataclass(frozen=True)
class AdvisorInputBundle:
    company: str
    role: str
    interview_date: str
    interviewer: str | None
    ordinal: int | None
    source_job_id: str
    manifest: AdvisorManifest
    sources: tuple[AdvisorSource, ...]
    evidence: tuple[CandidateEvidence, ...]

    def __post_init__(self):
        _text(self.company, "advisor_input_invalid", 200)
        _text(self.role, "advisor_input_invalid", 200)
        _fail(isinstance(self.interview_date, str), "advisor_input_invalid")
        try:
            _fail(date.fromisoformat(self.interview_date).isoformat() == self.interview_date,
                  "advisor_input_invalid")
        except ValueError:
            raise AdvisorContractError("advisor_input_invalid") from None
        _fail(self.interviewer is None or isinstance(self.interviewer, str), "advisor_input_invalid")
        if self.interviewer is not None:
            _text(self.interviewer, "advisor_input_invalid", 200)
        _fail(self.ordinal is None or (type(self.ordinal) is int and self.ordinal > 0), "advisor_input_invalid")
        _fail((self.interviewer and self.interviewer.strip()) or self.ordinal is not None, "advisor_input_invalid")
        _text(self.source_job_id, "advisor_input_invalid", 160)
        _fail(isinstance(self.manifest, AdvisorManifest), "advisor_manifest_invalid")
        _fail(isinstance(self.sources, tuple) and len(self.sources) <= 100
              and all(isinstance(source, AdvisorSource) for source in self.sources), "advisor_input_invalid")
        _fail(isinstance(self.evidence, tuple) and len(self.evidence) <= 500
              and all(isinstance(item, CandidateEvidence) for item in self.evidence), "advisor_input_invalid")
        source_keys = [(s.source_id, s.version) for s in self.sources]
        evidence_refs = [e.ref.canonical() for e in self.evidence]
        _fail(len(source_keys) == len(set(source_keys)), "advisor_source_invalid")
        _fail(len(evidence_refs) == len(set(evidence_refs)), "advisor_evidence_invalid")
        _fail(bool(self.evidence) and all(item.active for item in self.evidence), "advisor_evidence_invalid")
        _fail(all(source.active for source in self.sources), "advisor_source_invalid")
        required = {SourceKind.JOB_DESCRIPTION, SourceKind.CANDIDATE_PROFILE,
                    SourceKind.STRAIGHT_LINE_DOCTRINE, SourceKind.GAME_THEORY_DOCTRINE}
        _fail(required.issubset({source.kind for source in self.sources}), "advisor_source_invalid")
        _fail(not set(source.canonical_ref() for source in self.sources).intersection(evidence_refs),
              "advisor_input_invalid")
        _fail(sum(s.kind == SourceKind.GUIDANCE and s.active for s in self.sources) <= 3,
              "advisor_source_invalid")
        signal_counts = {}
        for source in self.sources:
            if source.kind == SourceKind.ACCEPTED_SIGNAL and source.active:
                signal_counts[source.source_round] = signal_counts.get(source.source_round, 0) + 1
        _fail(all(count <= 5 for count in signal_counts.values()), "advisor_source_invalid")


def bundle_digest(bundle: AdvisorInputBundle) -> str:
    _fail(isinstance(bundle, AdvisorInputBundle), "advisor_input_invalid")
    manifest = {name: getattr(bundle.manifest, name) for name in AdvisorManifest.__dataclass_fields__}
    payload = {
        "identity": [bundle.company, bundle.role, bundle.interview_date, bundle.interviewer, bundle.ordinal],
        "source_job_id": bundle.source_job_id,
        "manifest": manifest,
        "sources": [[s.source_id, s.kind.value, s.version, s.text, s.source_round, s.active] for s in bundle.sources],
        "evidence": [[e.ref.canonical(), e.canonical_text, e.source_ref, list(e.tags), e.active] for e in bundle.evidence],
    }
    try:
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        raise AdvisorContractError("advisor_input_invalid") from None


@dataclass(frozen=True)
class GroundedAdvice:
    text: str
    source_refs: tuple[str, ...]

    def __post_init__(self):
        _text(self.text, "advisor_draft_invalid", 600)
        _fail(isinstance(self.source_refs, tuple) and bool(self.source_refs)
              and all(isinstance(ref, str) and ref.strip() and len(ref) <= 160 for ref in self.source_refs),
              "advisor_draft_invalid")


@dataclass(frozen=True)
class AdvisorDraft:
    straight_line: tuple[GroundedAdvice, ...]
    game_theory: tuple[GroundedAdvice, ...]
    decision_criteria: tuple[GroundedAdvice, ...]
    objections: tuple[GroundedAdvice, ...]
    close_strategy: tuple[GroundedAdvice, ...]
    focus: tuple[GroundedAdvice, ...]
    strongest_evidence_refs: tuple[str, ...]
    pressure_points: tuple[GroundedAdvice, ...]
    questions: tuple[GroundedAdvice, ...]


class AdvisorModel(Protocol):
    def advise(self, bundle: AdvisorInputBundle) -> AdvisorDraft: ...


class ForecastConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class PreInterviewRisk:
    hypothesis: str
    confidence: ForecastConfidence
    why_plausible: str
    source_refs: tuple[str, ...]
    watch_for: tuple[str, ...]
    if_confirmed: str
    do_not_do: str
    evidence_refs: tuple[str, ...]
    certainty_target: str


@dataclass(frozen=True)
class PreInterviewForecast:
    risks: tuple[PreInterviewRisk, ...]


def _validate_forecast(bundle, forecast):
    _fail(isinstance(bundle, AdvisorInputBundle) and isinstance(forecast, PreInterviewForecast),
          "advisor_draft_invalid")
    _fail(isinstance(forecast.risks, tuple) and 3 <= len(forecast.risks) <= 7
          and all(isinstance(risk, PreInterviewRisk) for risk in forecast.risks), "advisor_draft_invalid")
    sources = {source.canonical_ref(): source for source in bundle.sources}
    evidence = {item.ref.canonical(): item for item in bundle.evidence}
    hypotheses = set()
    personal_pattern = re.compile(
        r"\b(?:jim|i|me|my|mine|myself|you|your|yours|candidate|interviewee|prior|previous|historical)\b", re.I)
    accepted_kinds = {SourceKind.CANDIDATE_PROFILE, SourceKind.GUIDANCE, SourceKind.ACCEPTED_SIGNAL,
                      SourceKind.PRIOR_DERIVED}
    for risk in forecast.risks:
        for value, limit in ((risk.hypothesis, 300), (risk.why_plausible, 500),
                             (risk.if_confirmed, 500), (risk.do_not_do, 300)):
            _fail(isinstance(value, str) and value.strip() == value and bool(value) and len(value) <= limit,
                  "advisor_draft_invalid")
        _fail(isinstance(risk.confidence, ForecastConfidence), "advisor_draft_invalid")
        identity = risk.hypothesis.casefold()
        _fail(identity not in hypotheses, "advisor_draft_invalid")
        hypotheses.add(identity)
        _fail(isinstance(risk.watch_for, tuple) and 1 <= len(risk.watch_for) <= 5,
              "advisor_draft_invalid")
        watch_items = set()
        for item in risk.watch_for:
            _fail(isinstance(item, str) and item.strip() == item and bool(item) and len(item) <= 200,
                  "advisor_draft_invalid")
            _fail(item.casefold() not in watch_items, "advisor_draft_invalid")
            watch_items.add(item.casefold())
        _fail(isinstance(risk.source_refs, tuple) and bool(risk.source_refs)
              and all(isinstance(ref, str) for ref in risk.source_refs), "advisor_draft_invalid")
        _fail(len(risk.source_refs) == len(set(risk.source_refs)), "advisor_draft_invalid")
        _fail(all(ref in sources for ref in risk.source_refs), "advisor_ref_missing")
        _fail(isinstance(risk.evidence_refs, tuple)
              and all(isinstance(ref, str) for ref in risk.evidence_refs), "advisor_draft_invalid")
        _fail(len(risk.evidence_refs) == len(set(risk.evidence_refs)), "advisor_draft_invalid")
        _fail(all(ref in evidence for ref in risk.evidence_refs),
              "advisor_evidence_missing")
        _fail(risk.certainty_target in ("opportunity", "candidate", "company"), "advisor_draft_invalid")
        narrative = " ".join((risk.hypothesis, risk.why_plausible, *risk.watch_for,
                              risk.if_confirmed, risk.do_not_do))
        if personal_pattern.search(narrative):
            _fail(any(sources[ref].kind in accepted_kinds for ref in risk.source_refs), "advisor_ref_missing")


def render_forecast(bundle: AdvisorInputBundle, forecast: PreInterviewForecast) -> str:
    """Validate and render a deterministic pre-interview failure-mode forecast."""
    _validate_forecast(bundle, forecast)
    evidence = {item.ref.canonical(): item.canonical_text for item in bundle.evidence}
    sections = ["## Pre-Mortem — How This Interview Could Go Wrong"]
    for risk in forecast.risks:
        evidence_lines = [f"- {evidence[ref]}" for ref in risk.evidence_refs] or ["- None selected"]
        sections.append("\n".join((f"### {risk.hypothesis}", f"**Confidence:** {risk.confidence.value.upper()}",
                         f"**Why this is plausible:** {risk.why_plausible}",
                         "**Watch for:**\n" + "\n".join(f"- {item}" for item in risk.watch_for),
                         f"**If confirmed:** {risk.if_confirmed}", f"**Do not:** {risk.do_not_do}",
                         "**Evidence to deploy:**\n" + "\n".join(evidence_lines),
                         f"**Certainty target:** {risk.certainty_target.title()}")))
    return "\n\n".join(sections)


_PRIVATE_SECTIONS = ("straight_line", "game_theory", "decision_criteria", "objections", "close_strategy")
_CONCLUSION_SECTIONS = ("focus", "pressure_points", "questions")


def _validate_draft(bundle, draft):
    _fail(isinstance(draft, AdvisorDraft), "advisor_draft_invalid")
    source_refs = {source.canonical_ref() for source in bundle.sources}
    evidence_refs = {item.ref.canonical() for item in bundle.evidence}
    all_refs = source_refs | evidence_refs
    straight_line_refs = {source.canonical_ref() for source in bundle.sources
                          if source.kind == SourceKind.STRAIGHT_LINE_DOCTRINE}
    game_theory_refs = {source.canonical_ref() for source in bundle.sources
                        if source.kind == SourceKind.GAME_THEORY_DOCTRINE}
    contextual_refs = all_refs - straight_line_refs - game_theory_refs
    for section in _PRIVATE_SECTIONS:
        items = getattr(draft, section)
        _fail(isinstance(items, tuple) and len(items) <= 8 and all(isinstance(x, GroundedAdvice) for x in items),
              "advisor_draft_invalid")
        if section in ("straight_line", "game_theory"):
            _fail(bool(items), "advisor_draft_invalid")
        for item in items:
            _fail(len(item.text) <= 600, "advisor_draft_invalid")
            _fail(all(ref in all_refs for ref in item.source_refs), "advisor_ref_missing")
            if section == "straight_line":
                _fail(any(ref in straight_line_refs for ref in item.source_refs)
                      and any(ref in contextual_refs for ref in item.source_refs), "advisor_ref_missing")
            if section == "game_theory":
                _fail(any(ref in game_theory_refs for ref in item.source_refs)
                      and any(ref in contextual_refs for ref in item.source_refs), "advisor_ref_missing")
    for section in _CONCLUSION_SECTIONS:
        items = getattr(draft, section)
        _fail(isinstance(items, tuple) and len(items) <= 5 and all(isinstance(x, GroundedAdvice) for x in items),
              "advisor_draft_invalid")
        for item in items:
            _fail(len(item.text) <= 500, "advisor_compile_too_large")
            _fail(all(ref in all_refs for ref in item.source_refs), "advisor_ref_missing")
    refs = draft.strongest_evidence_refs
    _fail(isinstance(refs, tuple) and len(refs) <= 5 and len(refs) == len(set(refs)), "advisor_draft_invalid")
    for value in refs:
        ref = EvidenceRef.parse(value)
        _fail(ref.canonical() in evidence_refs, "advisor_evidence_missing")
        candidate = next(item for item in bundle.evidence if item.ref == ref)
        _fail(candidate.active, "advisor_evidence_inactive")


def compile_prep(bundle: AdvisorInputBundle, draft: AdvisorDraft) -> PrepEvidence:
    """Validate an untrusted draft and compile bounded conclusions to the existing B3 contract."""
    _fail(isinstance(bundle, AdvisorInputBundle), "advisor_input_invalid")
    _validate_draft(bundle, draft)
    evidence_by_ref = {item.ref.canonical(): item for item in bundle.evidence}
    strongest = tuple(f"{evidence_by_ref[ref].canonical_text} [{ref}]" for ref in draft.strongest_evidence_refs)
    _fail(all(len(text) <= 500 for text in strongest), "advisor_compile_too_large")
    result = PrepEvidence(
        focus=tuple(item.text for item in draft.focus),
        strongest_evidence=strongest,
        pressure_points=tuple(item.text for item in draft.pressure_points),
        questions=tuple(item.text for item in draft.questions),
    )
    try:
        normalize_prep(result)
    except OverflowError:
        raise AdvisorContractError("advisor_compile_too_large") from None
    except (ValueError, TypeError):
        raise AdvisorContractError("advisor_draft_invalid") from None
    return result
