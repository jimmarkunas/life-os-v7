"""integrated POC-to-production delivery (synthetic): a complete package releases from a Scoping Epic, the promotion decision is GO,
RETRY, NO-GO or BLOCKED, and legacy research and ordinary work behave as before."""

import unittest

from lifeos.jira import package, release

# A synthetic Honcho-style delivery package: a memory-store replacement with three candidates. No real id, host or secret.
HONCHO_STYLE = """## Experimental package
## Outcome
The agent memory service recalls approved memories faster and cheaper than today with no loss of recall quality.
## Candidate POCs
- A: current store with a tuned index
- B: a managed vector store
- C: a local embedding cache in front of the current store
## Integration
Each candidate runs behind the same recall interface against the synthetic corpus in the isolated environment.
## Deployment
Promote the winner with the same container image and configuration; verify the release SHA and environment parity.
## Rollback
Point the interface back at the current store; no data migration, no delete.
## Acceptance matrix
- outcome: recall at 5 at least 0.90 on the frozen set and a fresh holdout
- security: no new credential, no real data
- cost: at most 2 cents per 100 recalls
- rollback: switch back in under 5 minutes
## Budget
2 hours, 400 calls, 3 dollars of spend.
## Alternatives
- B2: the managed store with a smaller index
- A2: the tuned index with a larger cache
## Approval
- synthetic corpus load: target the isolated test environment; budget 0 dollars
- candidate B managed store: target the vendor trial workspace; budget 3 dollars
## Jim must do first
- [x] Approved the envelope above (synthetic example).
## Done when
- Given the package, when promoted, then recall and cost meet the matrix on the release SHA.
"""

GREEN = {
    "approval": True,
    "unsafe": False,
    "acceptance": "passed",
    "alternatives_left": 2,
    "budget_left": True,
    "same_head_ci": True,
    "proofs": ("outcome", "security", "cost", "rollback"),
}


def child(labels, description, epic="Scoping"):
    return {
        "key": "LIFE-1",
        "fields": {
            "summary": "x",
            "description": description,
            "labels": list(labels),
            "issuelinks": [],
            "parent": {"key": "LIFE-846", "fields": {"status": {"name": epic}}},
        },
    }


class Package(unittest.TestCase):
    def test_a_complete_package_has_nothing_missing_and_every_act_names_target_and_budget(self):
        self.assertEqual(package.missing(HONCHO_STYLE), [])
        self.assertEqual(package.approval_gaps(HONCHO_STYLE), [])

    def test_each_missing_or_empty_section_is_named(self):
        for name, pattern in package.SECTIONS:
            text = "\n".join(
                line
                for line in HONCHO_STYLE.splitlines()
                if not line.lower().startswith("## " + pattern.split("\\")[0])
            )
            self.assertIn(name, package.missing(text), name)
        self.assertEqual(package.missing("## Outcome\nsomething"), [n for n, _ in package.SECTIONS[1:]])
        self.assertEqual(package.missing("no headings at all"), [n for n, _ in package.SECTIONS])

    def test_a_protected_act_without_a_target_or_a_budget_is_a_gap(self):
        weak = HONCHO_STYLE.replace(
            "- synthetic corpus load: target the isolated test environment; budget 0 dollars",
            "- load the corpus: whenever",
        )
        self.assertEqual(package.approval_gaps(weak), ["load the corpus"])


class ReleaseGate(unittest.TestCase):
    def test_a_complete_package_releases_from_an_epic_in_scoping_with_one_approval_and_no_research_ticket(self):
        self.assertIsNone(release.eligible(child(["ready", "package"], HONCHO_STYLE)))

    def test_an_incomplete_package_is_refused_by_name(self):
        text = HONCHO_STYLE.replace("## Rollback", "## Notes")
        self.assertEqual(release.eligible(child(["ready", "package"], text)), "package_incomplete")

    def test_ordinary_build_work_still_waits_in_scoping_and_research_and_verdict_still_release(self):
        self.assertEqual(release.eligible(child(["ready"], HONCHO_STYLE)), "epic_scoping")
        self.assertIsNone(release.eligible(child(["ready", "research"], HONCHO_STYLE)))
        self.assertIsNone(release.eligible(child(["ready", "verdict"], HONCHO_STYLE)))

    def test_a_package_does_not_skip_the_other_gates(self):
        open_item = HONCHO_STYLE.replace("- [x] Approved", "- [ ] Approved")
        self.assertEqual(release.eligible(child(["ready", "package"], open_item)), "jim_first_open")
        self.assertEqual(release.eligible(child(["package"], HONCHO_STYLE)), "no_readiness_label")
        self.assertEqual(release.eligible(child(["needs-jim", "package"], HONCHO_STYLE)), "not_ready_label")
        blocked = child(["ready", "package"], HONCHO_STYLE)
        blocked["fields"]["issuelinks"] = [
            {
                "type": {"name": "Blocks"},
                "inwardIssue": {"key": "LIFE-9", "fields": {"status": {"statusCategory": {"key": "new"}}}},
            }
        ]
        self.assertEqual(release.eligible(blocked), "blocked_by_open")
        self.assertIsNone(release.eligible(child(["ready", "package"], HONCHO_STYLE, epic="Ready")))


class Promotion(unittest.TestCase):
    def test_go_only_with_approval_same_head_ci_and_the_four_proofs(self):
        self.assertEqual(package.promotion(GREEN), ("GO", ["promote_proven_implementation"]))

    def test_each_missing_requirement_blocks_promotion_and_nothing_is_marked_done(self):
        self.assertEqual(package.promotion(dict(GREEN, approval=False)), ("BLOCKED", ["missing_protected_approval"]))
        self.assertEqual(package.promotion(dict(GREEN, same_head_ci=False)), ("BLOCKED", ["ci_not_green_on_this_head"]))
        self.assertEqual(
            package.promotion(dict(GREEN, proofs=("outcome", "security"))),
            ("BLOCKED", ["no_cost_proof", "no_rollback_proof"]),
        )

    def test_a_failed_experiment_retries_an_approved_alternative_then_is_no_go_and_keeps_production(self):
        failed = dict(GREEN, acceptance="failed", proofs=())
        self.assertEqual(package.promotion(failed), ("RETRY", ["acceptance_failed", "next_approved_alternative"]))
        verdict, reasons = package.promotion(dict(failed, alternatives_left=0))
        self.assertEqual(verdict, "NO-GO")
        self.assertEqual(reasons, ["acceptance_failed_exhausted", "retain_prior_production", "no_new_tickets"])
        self.assertEqual(package.promotion(dict(failed, budget_left=False))[0], "NO-GO")

    def test_an_unsafe_deployment_or_an_exhausted_budget_is_no_go_even_when_acceptance_passed(self):
        self.assertEqual(package.promotion(dict(GREEN, unsafe=True))[0], "NO-GO")
        self.assertEqual(package.promotion(dict(GREEN, budget_left=False))[1][0], "budget_exhausted")

    def test_the_synthetic_delivery_end_to_end_one_approval_one_matrix_one_promotion(self):
        self.assertIsNone(release.eligible(child(["ready", "package"], HONCHO_STYLE)))  # released once
        tries = [
            dict(GREEN, acceptance="failed", proofs=(), alternatives_left=2),
            dict(GREEN, acceptance="failed", proofs=(), alternatives_left=1),
            GREEN,
        ]  # candidate A fails, B2 fails, A2 passes
        verdicts = [package.promotion(t)[0] for t in tries]
        self.assertEqual(verdicts, ["RETRY", "RETRY", "GO"])


if __name__ == "__main__":
    unittest.main()
