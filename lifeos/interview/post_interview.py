"""Grounded, deterministic contracts for post-interview review output."""
from dataclasses import dataclass
from enum import Enum

from .advisor import AdvisorContractError, AdvisorInputBundle


class ReviewAssessment(str, Enum):
    EFFECTIVE = "effective"
    PARTIAL = "partial"
    MISSED = "missed"
    NOT_OBSERVED = "not_observed"


@dataclass(frozen=True)
class GroundedReviewItem:
    text: str
    transcript_lines: tuple[int, ...] = ()
    note_refs: tuple[str, ...] = ()
    assessment: ReviewAssessment | None = None


@dataclass(frozen=True)
class RecommendedFollowUp:
    action: str
    timing: str
    objective: str
    angle: str
    deliverable: str
    draft_text: str
    transcript_lines: tuple[int, ...] = ()
    note_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class PostInterviewReview:
    executive_read: tuple[GroundedReviewItem, ...]
    actual_needs: tuple[GroundedReviewItem, ...]
    questions_or_themes: tuple[GroundedReviewItem, ...]
    what_landed: tuple[GroundedReviewItem, ...]
    what_needs_work: tuple[GroundedReviewItem, ...]
    interviewer_signals: tuple[GroundedReviewItem, ...]
    objection_audit: tuple[GroundedReviewItem, ...]
    certainty_updates: tuple[GroundedReviewItem, ...]
    game_theory_updates: tuple[GroundedReviewItem, ...]
    evidence_used: tuple[GroundedReviewItem, ...]
    stronger_unused_evidence_refs: tuple[str, ...]
    commitments: tuple[GroundedReviewItem, ...]
    recommended_follow_up: RecommendedFollowUp
    exact_next_action: GroundedReviewItem
    next_round_adjustments: tuple[GroundedReviewItem, ...]


_SECTIONS = (("executive_read", "Executive Read"), ("actual_needs", "What They Actually Needed"),
             ("questions_or_themes", "Questions / Themes"), ("what_landed", "What Landed"),
             ("what_needs_work", "What Needs Work"), ("interviewer_signals", "Interviewer Signals"),
             ("objection_audit", "Straight Line Audit"), ("certainty_updates", "Three Tens Update"),
             ("game_theory_updates", "Game Theory Update"), ("evidence_used", "Evidence Used"),
             ("commitments", "Commitments"), ("next_round_adjustments", "Next-Round Strategy"))
_OPTIONAL = {"interviewer_signals", "objection_audit", "commitments"}


def _require(ok, code="advisor_draft_invalid"):
    if not ok:
        raise AdvisorContractError(code)


def _grounded(lines, refs, line_count, note_refs):
    _require(isinstance(lines, tuple) and all(type(line) is int for line in lines))
    _require(len(lines) == len(set(lines)) and tuple(sorted(lines)) == lines)
    _require(all(1 <= line <= line_count for line in lines))
    _require(isinstance(refs, tuple) and all(isinstance(ref, str) and ref.strip() == ref for ref in refs))
    _require(len(refs) == len(set(refs)) and all(ref in note_refs for ref in refs), "advisor_ref_missing")
    _require(bool(lines or refs))


def validate_post_interview_review(bundle, transcript_line_count, accepted_note_refs, review):
    _require(isinstance(bundle, AdvisorInputBundle) and type(transcript_line_count) is int
             and transcript_line_count > 0)
    _require(isinstance(accepted_note_refs, tuple)
             and all(isinstance(ref, str) and ref.strip() == ref and bool(ref) for ref in accepted_note_refs))
    _require(len(accepted_note_refs) == len(set(accepted_note_refs)))
    _require(isinstance(review, PostInterviewReview))
    for name, _ in _SECTIONS:
        items = getattr(review, name)
        _require(isinstance(items, tuple) and (bool(items) or name in _OPTIONAL))
        for item in items:
            _require(isinstance(item, GroundedReviewItem)
                     and isinstance(item.text, str) and item.text.strip() == item.text
                     and bool(item.text) and len(item.text) <= 800)
            _require(item.assessment is None or isinstance(item.assessment, ReviewAssessment))
            _grounded(item.transcript_lines, item.note_refs, transcript_line_count, set(accepted_note_refs))
    refs = review.stronger_unused_evidence_refs
    _require(isinstance(refs, tuple) and 1 <= len(refs) <= 5 and all(isinstance(ref, str) for ref in refs))
    _require(len(refs) == len(set(refs)))
    evidence = {item.ref.canonical(): item for item in bundle.evidence}
    _require(all(ref in evidence for ref in refs), "advisor_evidence_missing")
    follow_up = review.recommended_follow_up
    _require(isinstance(follow_up, RecommendedFollowUp))
    for value, limit in ((follow_up.action, 300), (follow_up.timing, 120),
                         (follow_up.objective, 500), (follow_up.angle, 600),
                         (follow_up.deliverable, 300), (follow_up.draft_text, 2000)):
        _require(isinstance(value, str) and value.strip() == value and bool(value) and len(value) <= limit)
    _grounded(follow_up.transcript_lines, follow_up.note_refs, transcript_line_count, set(accepted_note_refs))
    action = review.exact_next_action
    _require(isinstance(action, GroundedReviewItem) and isinstance(action.text, str)
             and action.text.strip() == action.text and bool(action.text) and len(action.text) <= 800)
    _require(action.assessment is None or isinstance(action.assessment, ReviewAssessment))
    _grounded(action.transcript_lines, action.note_refs, transcript_line_count, set(accepted_note_refs))


def render_post_interview_review(bundle, transcript_line_count, accepted_note_refs, review):
    validate_post_interview_review(bundle, transcript_line_count, accepted_note_refs, review)
    blocks = []
    for name, title in _SECTIONS[:10]:
        blocks.append((title, getattr(review, name)))
    blocks.append(("Stronger Unused Evidence", tuple(
        GroundedReviewItem(next(e.canonical_text for e in bundle.evidence if e.ref.canonical() == ref))
        for ref in review.stronger_unused_evidence_refs)))
    blocks.append(("Commitments", review.commitments))
    output = []
    for title, items in blocks:
        body = []
        for item in items:
            line = f"- {item.text}"
            if item.assessment is not None:
                line += f"\n  **Assessment:** {item.assessment.value.upper()}"
            body.append(line)
        output.append(f"# {title}" + ("\n\n" + "\n".join(body) if body else ""))
    follow = review.recommended_follow_up
    output.append("# Recommended Follow-Up\n\n" + "\n".join(
        f"- **{label}:** {value}" for label, value in (("Action", follow.action), ("Timing", follow.timing),
        ("Objective", follow.objective), ("Angle", follow.angle), ("Deliverable", follow.deliverable),
        ("Draft", follow.draft_text))))
    action = review.exact_next_action
    action_text = f"- {action.text}"
    if action.assessment is not None:
        action_text += f"\n  **Assessment:** {action.assessment.value.upper()}"
    output.append("# Exact Next Action\n\n" + action_text)
    output.append("# Next-Round Strategy" + ("\n\n" + "\n".join(
        f"- {item.text}" + (f"\n  **Assessment:** {item.assessment.value.upper()}" if item.assessment else "")
        for item in review.next_round_adjustments) if review.next_round_adjustments else ""))
    return "\n\n".join(output)
