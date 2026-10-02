from dataclasses import fields, replace
import ast
from pathlib import Path
import unittest

from lifeos.interview import advisor
from lifeos.interview.post_interview import (GroundedReviewItem, PostInterviewReview,
    RecommendedFollowUp, ReviewAssessment, render_post_interview_review,
    validate_post_interview_review)


def input_bundle():
    manifest = advisor.AdvisorManifest("sl-v1", "a" * 64, "gt-v1", "b" * 64,
        "profile-v1", "c" * 64, "bank-v1", "d" * 64, "prompt-v1")
    sources = (advisor.AdvisorSource("JD-SYN", advisor.SourceKind.JOB_DESCRIPTION, "Synthetic role."),
        advisor.AdvisorSource("CP-SYN", advisor.SourceKind.CANDIDATE_PROFILE, "Synthetic profile."),
        advisor.AdvisorSource("SL-SYN", advisor.SourceKind.STRAIGHT_LINE_DOCTRINE, "Synthetic doctrine."),
        advisor.AdvisorSource("GT-SYN", advisor.SourceKind.GAME_THEORY_DOCTRINE, "Synthetic game theory."))
    evidence = (advisor.CandidateEvidence(advisor.EvidenceRef("E-SYN-001", 1), "American Apparel synthetic result.", "CP-SYN@1"),
        advisor.CandidateEvidence(advisor.EvidenceRef("E-SYN-002", 2), "DIRECTV synthetic orchestration result.", "CP-SYN@1"))
    return advisor.AdvisorInputBundle("Synthetic Co", "Synthetic Role", "2026-10-04", "Interviewer", 1,
        "JOB-SYN", manifest, sources, evidence)


def item(text="Synthetic grounded observation.", **changes):
    values = dict(text=text, transcript_lines=(1,), note_refs=(), assessment=None)
    values.update(changes)
    return GroundedReviewItem(**values)


def review(**changes):
    base = PostInterviewReview(
        executive_read=(item("Synthetic executive read.", assessment=ReviewAssessment.PARTIAL),),
        actual_needs=(item("Needed clearer ownership."),),
        questions_or_themes=(item("Asked about operating cadence."),),
        what_landed=(item("Explained a synthetic launch."),),
        what_needs_work=(item("Candidate proposed additional meetings instead of reducing synchronous burden."),),
        interviewer_signals=(item("Stakeholders are hard to get time from."),),
        objection_audit=(item("Loop was not completed."),),
        certainty_updates=(item("Certainty about async decision-making increased."),),
        game_theory_updates=(item("Interviewer named the functional owner."),),
        evidence_used=(item("Used a synthetic case study."),),
        stronger_unused_evidence_refs=("E-SYN-002@2",),
        commitments=(item("Send a short operating model."),),
        recommended_follow_up=RecommendedFollowUp("Send a follow-up", "Within one day",
            "Lower the action threshold", "Show a low-meeting coordination operating model.",
            "One-page operating model", "Thank you for the conversation.", (1,), ()),
        exact_next_action=item("Draft the one-page operating model."),
        next_round_adjustments=(item("Lead with explicit owners and async decisions."),))
    return replace(base, **changes)


class PostInterviewContractTests(unittest.TestCase):
    def test_valid_review_validates_and_renders(self):
        value = input_bundle(); result = review()
        validate_post_interview_review(value, 4, (), result)
        rendered = render_post_interview_review(value, 4, (), result)
        self.assertIn("# Executive Read", rendered)
        self.assertIn("# Next-Round Strategy", rendered)

    def test_transcript_line_bounds_order_and_uniqueness(self):
        for lines in ((0,), (5,), (2, 1), (1, 1)):
            bad = replace(review(), executive_read=(item(transcript_lines=lines),))
            with self.subTest(lines=lines), self.assertRaises(advisor.AdvisorContractError):
                validate_post_interview_review(input_bundle(), 4, (), bad)

    def test_unknown_note_ref_and_ungrounded_item_fail(self):
        unknown = replace(review(), executive_read=(item(transcript_lines=(), note_refs=("NOTE-X",)),))
        ungrounded = replace(review(), executive_read=(item(transcript_lines=(), note_refs=()),))
        for bad in (unknown, ungrounded):
            with self.assertRaises(advisor.AdvisorContractError):
                validate_post_interview_review(input_bundle(), 4, ("NOTE-A",), bad)

    def test_note_refs_must_be_unique_trimmed_and_accepted(self):
        for refs in (("NOTE-A", "NOTE-A"), (" NOTE-A",)):
            with self.subTest(refs=refs), self.assertRaises(advisor.AdvisorContractError):
                validate_post_interview_review(input_bundle(), 4, refs, review())
        noted = replace(review(), executive_read=(item(transcript_lines=(), note_refs=("NOTE-A",)),))
        validate_post_interview_review(input_bundle(), 4, ("NOTE-A",), noted)

    def test_optional_sections_may_be_empty_but_required_section_may_not(self):
        validate_post_interview_review(input_bundle(), 4, (), replace(review(),
            interviewer_signals=(), objection_audit=(), commitments=()))
        with self.assertRaises(advisor.AdvisorContractError):
            validate_post_interview_review(input_bundle(), 4, (), replace(review(), actual_needs=()))

    def test_stronger_unused_evidence_resolves_from_full_bank(self):
        value = input_bundle()
        rendered = render_post_interview_review(value, 4, (), replace(review(),
            stronger_unused_evidence_refs=("E-SYN-001@1",)))
        self.assertIn("American Apparel synthetic result.", rendered)
        self.assertNotIn("E-SYN-001@1", rendered)
        self.assertIn("DIRECTV synthetic orchestration result.", render_post_interview_review(value, 4, (), review()))

    def test_unused_evidence_refs_are_unique_bounded_and_resolved(self):
        for refs in ((), ("E-SYN-001@1", "E-SYN-001@1"), tuple("E-SYN-001@1" for _ in range(6)),
                     ("E-MISSING@1",)):
            with self.subTest(refs=refs), self.assertRaises(advisor.AdvisorContractError):
                validate_post_interview_review(input_bundle(), 4, (),
                    replace(review(), stronger_unused_evidence_refs=refs))

    def test_follow_up_and_exact_next_action_each_require_grounding(self):
        follow = replace(review(), recommended_follow_up=replace(review().recommended_follow_up,
            transcript_lines=(), note_refs=()))
        action = replace(review(), exact_next_action=item(transcript_lines=(), note_refs=()))
        for bad in (follow, action):
            with self.assertRaises(advisor.AdvisorContractError):
                validate_post_interview_review(input_bundle(), 4, (), bad)

    def test_assessments_render_and_internal_provenance_is_hidden_in_order(self):
        value = input_bundle()
        assessed = replace(review(), executive_read=(item("Read assessment.", assessment=ReviewAssessment.EFFECTIVE),),
                           actual_needs=(item("Need assessment.", assessment=ReviewAssessment.MISSED),))
        rendered = render_post_interview_review(value, 4, (), assessed)
        self.assertIn("**Assessment:** EFFECTIVE", rendered)
        self.assertIn("**Assessment:** MISSED", rendered)
        self.assertLess(rendered.index("# Executive Read"), rendered.index("# What They Actually Needed"))
        self.assertLess(rendered.index("# Stronger Unused Evidence"), rendered.index("# Commitments"))
        self.assertLess(rendered.index("# Commitments"), rendered.index("# Recommended Follow-Up"))
        self.assertLess(rendered.index("# Recommended Follow-Up"), rendered.index("# Exact Next Action"))
        self.assertLess(rendered.index("# Exact Next Action"), rendered.index("# Next-Round Strategy"))
        for private_ref in ("E-SYN-002@2", "NOTE-A"):
            self.assertNotIn(private_ref, rendered)
        self.assertNotIn("line 1", rendered.lower())

    def test_synthetic_stakeholder_scarcity_failure_mode(self):
        result = review()
        rendered = render_post_interview_review(input_bundle(), 4, (), result)
        self.assertIn("Stakeholders are hard to get time from.", rendered)
        self.assertIn("Candidate proposed additional meetings instead of reducing synchronous burden.", rendered)
        self.assertIn("Show a low-meeting coordination operating model.", rendered)

    def test_no_outcome_or_hidden_motive_fields_or_external_capabilities(self):
        names = {field.name for field in fields(PostInterviewReview)}
        self.assertFalse(any("probab" in name or "outcome" in name or "motive" in name for name in names))
        source = Path(__import__("lifeos.interview.post_interview", fromlist=["__file__"]).__file__).read_text()
        tree = ast.parse(source)
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imports |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse(any(name and (name.startswith("openai") or name.startswith("anthropic")
            or "notion" in name or "workflow" in name or "platform" in name) for name in imports))
        self.assertNotIn("urlopen", source)
        self.assertNotIn("parse_transcript", source)
        self.assertNotIn("raw_notes", source.lower())


if __name__ == "__main__":
    unittest.main()
