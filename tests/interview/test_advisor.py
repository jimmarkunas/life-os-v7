import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import unittest
from unittest.mock import patch

from lifeos.interview import advisor
from lifeos.interview.advisor import (AdvisorContractError, AdvisorDraft, AdvisorInputBundle, AdvisorManifest,
    AdvisorModel, AdvisorSource, CandidateEvidence, EvidenceRef, GroundedAdvice, SourceKind, bundle_digest, compile_prep)
from lifeos.interview.models import PrepEvidence


HASH = "a" * 64


def manifest(**changes):
    base = AdvisorManifest("sl-v1", HASH, "gt-v1", "b" * 64, "profile-v1", "c" * 64,
                          "bank-v1", "d" * 64, "prompt-v1")
    return replace(base, **changes)


def candidate(ref="E-SYN-001@1", text="Led a synthetic migration across five markets.", active=True):
    return CandidateEvidence(EvidenceRef.parse(ref), text, "PROFILE-SYN-001", ("delivery",), active)


def sources():
    return (
        AdvisorSource("JD-SYN-001", SourceKind.JOB_DESCRIPTION, "Synthetic role description."),
        AdvisorSource("PROFILE-SYN-001", SourceKind.CANDIDATE_PROFILE, "Synthetic profile source."),
        AdvisorSource("GUIDE-SYN-001", SourceKind.GUIDANCE, "Keep the response concise."),
        AdvisorSource("SIGNAL-SYN-001", SourceKind.ACCEPTED_SIGNAL, "The panel values clear tradeoffs.", source_round="ROUND-SYN-001"),
        AdvisorSource("SL-SYN-001", SourceKind.STRAIGHT_LINE_DOCTRINE, "Synthetic doctrine source."),
        AdvisorSource("GT-SYN-001", SourceKind.GAME_THEORY_DOCTRINE, "Synthetic strategy source."),
    )


def bundle(src=None, evidence=None, **changes):
    base = AdvisorInputBundle("Example Company", "Synthetic Role", "2026-10-02", "Synthetic Interviewer", 1,
                              "JOB-SYN-001", manifest(), sources() if src is None else src,
                              (candidate(),) if evidence is None else evidence)
    return replace(base, **changes)


def advice(text="Synthetic finding", ref="JD-SYN-001@1"):
    return GroundedAdvice(text, (ref,))


def draft(**changes):
    base = AdvisorDraft(
        straight_line=(GroundedAdvice("Establish the decision threshold.", ("SL-SYN-001@1", "JD-SYN-001@1")),),
        game_theory=(GroundedAdvice("Clarify who decides and what they value.", ("GT-SYN-001@1", "JD-SYN-001@1")),),
        decision_criteria=(), objections=(), close_strategy=(),
        focus=(advice("Confirm the role's success measure."),),
        strongest_evidence_refs=("E-SYN-001@1",),
        pressure_points=(advice("Test the timing assumption."),),
        questions=(advice("What would change the decision?"),),
    )
    return replace(base, **changes)


class EvidenceContractTests(unittest.TestCase):
    def test_versioned_reference_parse_and_canonical_render(self):
        ref = EvidenceRef.parse("E-SYN-001@1")
        self.assertEqual((ref.evidence_id, ref.version, ref.canonical()), ("E-SYN-001", 1, "E-SYN-001@1"))

    def test_malformed_references_fail_closed(self):
        for value in ("E-SYN-001@0", "E-SYN-001@-1", "E-SYN-001", " E-SYN-001@1", "E-SYN 001@1",
                      "e-SYN-001@1", "E--SYN@1", "E-SYN@01", "E-SYN@1 "):
            with self.subTest(value=value), self.assertRaises(AdvisorContractError):
                EvidenceRef.parse(value)

    def test_evidence_bank_constraints_and_frozen_values(self):
        item = candidate(active=False)
        self.assertFalse(item.active)
        with self.assertRaises(AdvisorContractError):
            bundle(evidence=(item,))
        with self.assertRaises(FrozenInstanceError):
            item.active = True
        for args in ((EvidenceRef.parse("E-SYN-001@1"), " ", "PROFILE-SYN-001"),
                     (EvidenceRef.parse("E-SYN-001@1"), "Text", " ")):
            with self.subTest(args=args), self.assertRaises(AdvisorContractError):
                CandidateEvidence(*args)
        with self.assertRaises(AdvisorContractError):
            bundle(evidence=(candidate(), candidate()))


class SourceAndManifestTests(unittest.TestCase):
    def test_allowed_source_kinds_and_protected_kinds_absent(self):
        allowed = {kind.value for kind in SourceKind}
        self.assertEqual(allowed, {"job_description", "company", "interviewer", "prior_derived", "guidance",
                                   "accepted_signal", "candidate_profile", "straight_line_doctrine", "game_theory_doctrine"})
        for forbidden in ("live_notes", "raw_notes"):
            with self.assertRaises(ValueError):
                SourceKind(forbidden)
        self.assertTrue(all(isinstance(item, AdvisorSource) for item in sources()))
        historical_source = AdvisorSource("OLD-SYN-001", SourceKind.COMPANY, "Historical source.", active=False)
        self.assertFalse(historical_source.active)

    def test_source_identity_text_and_version_constraints(self):
        with self.assertRaises(AdvisorContractError):
            bundle(src=(sources()[0], sources()[0]))
        for args in (("JD-SYN-001", SourceKind.COMPANY, " "),
                     ("JD-SYN-002", SourceKind.COMPANY, "Company", 0),
                     ("https://synthetic.test", SourceKind.COMPANY, "Company")):
            with self.subTest(args=args), self.assertRaises(AdvisorContractError):
                AdvisorSource(*args)

    def test_guidance_and_signal_caps(self):
        guidance = tuple(AdvisorSource(f"G-SYN-{i}", SourceKind.GUIDANCE, "Guidance") for i in range(4))
        with self.assertRaises(AdvisorContractError):
            bundle(src=guidance)
        with self.assertRaises(AdvisorContractError):
            AdvisorSource("G-SYN-LONG", SourceKind.GUIDANCE, "x" * 301)
        signals = tuple(AdvisorSource(f"S-SYN-{i}", SourceKind.ACCEPTED_SIGNAL, "Signal",
                                      source_round="ROUND-SYN-1") for i in range(6))
        with self.assertRaises(AdvisorContractError):
            bundle(src=signals)
        with self.assertRaises(AdvisorContractError):
            AdvisorSource("S-SYN-LONG", SourceKind.ACCEPTED_SIGNAL, "x" * 301, source_round="ROUND-SYN-1")
        with self.assertRaises(AdvisorContractError):
            AdvisorSource("S-SYN-NO-ROUND", SourceKind.ACCEPTED_SIGNAL, "Signal")

    def test_current_bundle_requires_core_sources_and_active_inputs(self):
        required = (SourceKind.JOB_DESCRIPTION, SourceKind.CANDIDATE_PROFILE,
                    SourceKind.STRAIGHT_LINE_DOCTRINE, SourceKind.GAME_THEORY_DOCTRINE)
        valid = bundle()
        self.assertTrue(all(any(source.kind == kind and source.active for source in valid.sources) for kind in required))
        self.assertTrue(valid.evidence and all(item.active for item in valid.evidence))
        for missing in required:
            with self.subTest(missing=missing), self.assertRaises(AdvisorContractError) as error:
                bundle(src=tuple(source for source in sources() if source.kind != missing))
            self.assertEqual(str(error.exception), "advisor_source_invalid")
        with self.assertRaises(AdvisorContractError) as empty_evidence:
            bundle(evidence=())
        self.assertEqual(str(empty_evidence.exception), "advisor_evidence_invalid")
        for kind in (SourceKind.JOB_DESCRIPTION, SourceKind.GUIDANCE, SourceKind.ACCEPTED_SIGNAL):
            inactive = tuple(replace(source, active=False) if source.kind == kind else source for source in sources())
            with self.subTest(inactive=kind), self.assertRaises(AdvisorContractError) as error:
                bundle(src=inactive)
            self.assertEqual(str(error.exception), "advisor_source_invalid")
        with self.assertRaises(AdvisorContractError) as inactive_evidence:
            bundle(evidence=(candidate(active=False),))
        self.assertEqual(str(inactive_evidence.exception), "advisor_evidence_invalid")

    def test_source_and_evidence_reference_namespaces_must_not_collide(self):
        collision = AdvisorSource("E-SYN-001", SourceKind.COMPANY, "Synthetic company source.")
        with self.assertRaises(AdvisorContractError) as error:
            bundle(src=sources() + (collision,))
        self.assertEqual(str(error.exception), "advisor_input_invalid")

    def test_distinct_source_and_evidence_refs_remain_usable(self):
        result = compile_prep(bundle(), draft(focus=(GroundedAdvice("Evidence grounded focus", (
            "SL-SYN-001@1", "E-SYN-001@1")),)))
        self.assertEqual(result.focus, ("Evidence grounded focus",))

    def test_manifest_hashes_and_versions_are_required(self):
        bundle()
        for change in ({"straight_line_hash": "BAD"}, {"game_theory_hash": "A" * 64},
                       {"candidate_profile_version": " "}, {"advisor_prompt_version": ""}):
            with self.subTest(change=change), self.assertRaises(AdvisorContractError):
                replace(manifest(), **change)


class BundleDigestTests(unittest.TestCase):
    def test_exact_bundle_digest_is_stable_and_ordered(self):
        first = bundle()
        self.assertEqual(bundle_digest(first), bundle_digest(first))
        changed_text = replace(first.sources[0], text="Changed synthetic source.")
        changed_version = replace(first.sources[0], version=2)
        changed_signal = replace(first.sources[2], text="Changed accepted signal.")
        changed_guidance = replace(first.sources[1], text="Different private guidance.")
        changes = (
            replace(first, sources=(changed_text,) + first.sources[1:]),
            replace(first, sources=(changed_version,) + first.sources[1:]),
            replace(first, evidence=(candidate(text="Changed canonical evidence."),)),
            replace(first, evidence=(candidate(ref="E-SYN-001@2"),)),
            replace(first, manifest=manifest(advisor_prompt_version="prompt-v2")),
            replace(first, sources=(first.sources[0], changed_guidance) + first.sources[2:]),
            replace(first, sources=(first.sources[0], first.sources[1], changed_signal) + first.sources[3:]),
            replace(first, sources=tuple(reversed(first.sources))),
            replace(first, evidence=(candidate("E-SYN-002@1"), candidate("E-SYN-001@1"))),
            replace(first, company="Different Example Company"),
            replace(first, role="Different Synthetic Role"),
            replace(first, interview_date="2026-10-03"),
            replace(first, source_job_id="JOB-SYN-002"),
        )
        original = bundle_digest(first)
        self.assertTrue(all(bundle_digest(changed) != original for changed in changes))


class ModelAndCompilerTests(unittest.TestCase):
    class LocalModel:
        def advise(self, input_bundle):
            return draft()

    def test_fake_implements_provider_neutral_protocol_and_no_provider_import(self):
        fake = self.LocalModel()
        self.assertIsInstance(fake.advise(bundle()), AdvisorDraft)
        self.assertTrue(hasattr(AdvisorModel, "advise"))
        source = Path(advisor.__file__).read_text()
        tree = ast.parse(source)
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imports |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse(any(name and (name.startswith("openai") or name.startswith("urllib") or name == "requests") for name in imports))
        self.assertNotIn("logging", imports)

    def test_compiler_uses_canonical_evidence_and_preserves_order(self):
        item = candidate(text="Canonical synthetic fact")
        result = compile_prep(bundle(evidence=(item,)), draft(
            focus=(advice("Second", "E-SYN-001@1"), advice("First", "JD-SYN-001@1")),
            pressure_points=(advice("Risk B"), advice("Risk A")),
            questions=(advice("Question B"), advice("Question A"))))
        self.assertIsInstance(result, PrepEvidence)
        self.assertEqual(result.focus, ("Second", "First"))
        self.assertEqual(result.pressure_points, ("Risk B", "Risk A"))
        self.assertEqual(result.questions, ("Question B", "Question A"))
        self.assertEqual(result.strongest_evidence, ("Canonical synthetic fact [E-SYN-001@1]",))

    def test_draft_sections_grounding_and_bounds_are_enforced(self):
        cases = (
            {"straight_line": ()}, {"game_theory": ()},
            {"focus": (GroundedAdvice("Dangling", ("NO-SUCH-REF@1",)),)},
            {"decision_criteria": tuple(advice("Finding") for _ in range(9))},
            {"focus": tuple(advice("Finding") for _ in range(6))},
            {"strongest_evidence_refs": ("E-SYN-001@1", "E-SYN-001@1")},
        )
        for change in cases:
            with self.subTest(change=change), self.assertRaises(AdvisorContractError):
                compile_prep(bundle(), draft(**change))
        for text, refs in ((" ", ("JD-SYN-001@1",)), ("Advice", ())):
            with self.subTest(text=text, refs=refs), self.assertRaises(AdvisorContractError):
                GroundedAdvice(text, refs)

    def test_each_framework_finding_requires_matching_doctrine_and_context(self):
        valid_sl = GroundedAdvice("Apply framework to this interview.", ("SL-SYN-001@1", "JD-SYN-001@1"))
        valid_gt = GroundedAdvice("Apply strategy to this interview.", ("GT-SYN-001@1", "JD-SYN-001@1"))
        self.assertIsInstance(compile_prep(bundle(), draft(straight_line=(valid_sl,))), PrepEvidence)
        self.assertIsInstance(compile_prep(bundle(), draft(game_theory=(valid_gt,))), PrepEvidence)
        invalid = (
            ("straight_line", GroundedAdvice("Context only.", ("JD-SYN-001@1",))),
            ("straight_line", GroundedAdvice("Doctrine only.", ("SL-SYN-001@1",))),
            ("straight_line", GroundedAdvice("Wrong doctrine.", ("GT-SYN-001@1", "JD-SYN-001@1"))),
            ("game_theory", GroundedAdvice("Context only.", ("JD-SYN-001@1",))),
            ("game_theory", GroundedAdvice("Doctrine only.", ("GT-SYN-001@1",))),
            ("game_theory", GroundedAdvice("Wrong doctrine.", ("SL-SYN-001@1", "JD-SYN-001@1"))),
        )
        for section, item in invalid:
            with self.subTest(section=section, refs=item.source_refs), self.assertRaises(AdvisorContractError):
                compile_prep(bundle(), draft(**{section: (item,)}))
        candidate_context = GroundedAdvice("Evidence contextualized through Straight Line.",
                                          ("SL-SYN-001@1", "E-SYN-001@1"))
        self.assertIsInstance(compile_prep(bundle(), draft(straight_line=(candidate_context,))), PrepEvidence)

    def test_evidence_resolution_is_exact_active_version_and_never_model_prose(self):
        evidence = (candidate("E-SYN-001@1", "Immutable canonical text"),
                    candidate("E-SYN-001@2", "New immutable canonical text"))
        compiled = compile_prep(bundle(evidence=evidence), draft(strongest_evidence_refs=("E-SYN-001@2",)))
        self.assertEqual(compiled.strongest_evidence, ("New immutable canonical text [E-SYN-001@2]",))
        with self.assertRaises(AdvisorContractError) as missing:
            compile_prep(bundle(), draft(strongest_evidence_refs=("E-SYN-009@1",)))
        self.assertEqual(str(missing.exception), "advisor_evidence_missing")
        with self.assertRaises(AdvisorContractError) as inactive:
            bundle(evidence=(candidate(active=False),))
        self.assertEqual(str(inactive.exception), "advisor_evidence_invalid")
        with self.assertRaises(AdvisorContractError) as too_large:
            compile_prep(bundle(evidence=(candidate(text="x" * 490),)), draft())
        self.assertEqual(str(too_large.exception), "advisor_compile_too_large")

    def test_final_prep_validation_and_no_side_effects(self):
        with self.assertRaises(AdvisorContractError):
            compile_prep(bundle(), draft(focus=(), strongest_evidence_refs=(), pressure_points=(), questions=()))
        with patch("lifeos.interview.prep.apply", side_effect=AssertionError("B3 mutation")) as apply_b3, \
             patch("urllib.request.urlopen", side_effect=AssertionError("network")) as urlopen:
            result = compile_prep(bundle(), draft())
            apply_b3.assert_not_called()
            urlopen.assert_not_called()
        self.assertTrue(any((result.focus, result.strongest_evidence, result.pressure_points, result.questions)))

    def test_contract_errors_are_fixed_and_do_not_leak_values(self):
        private = "Synthetic Private Source Text"
        errors = []
        try:
            AdvisorSource("BAD", SourceKind.GUIDANCE, private * 50)
        except AdvisorContractError as error:
            errors.append(error)
        try:
            compile_prep(bundle(), draft(focus=(advice(private, "SECRET-SYN-001@1"),)))
        except AdvisorContractError as error:
            errors.append(error)
        self.assertTrue(errors)
        for error in errors:
            self.assertIn(error.code, advisor._CODES)
            self.assertEqual(str(error), error.code)
            self.assertNotIn(private, str(error))
        for code in advisor._CODES:
            error = AdvisorContractError(code)
            self.assertEqual(str(error), code)

    def test_compiler_has_no_notion_or_network_imports(self):
        source = Path(advisor.__file__).read_text()
        self.assertNotIn("lifeos.interview.notion", source)
        self.assertNotIn("urlopen", source)
        self.assertNotIn("sleep(", source)


if __name__ == "__main__":
    unittest.main()
