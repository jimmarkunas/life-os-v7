# Integrated POC-to-production delivery (policy section 7)

Jim, Nov 27 2026 (LIFE-1102). It replaces the fragmented path for experimental work (research ticket, verdict ticket, then build tickets, each with its own approval). The default is:

**Scope once, approve once, execute integrated POCs, validate, promote the proven implementation, verify production, close.**

Amended in place: no new framework or service. Every protected-action stop in policy sections 4 and 5, the workflow gate, human UAT and wait-only UAT still apply. Research-only requests (no build wanted) stay valid and keep the `research` and `verdict` labels and the closeout rule in section 3.

## The package (one ticket, one PR by default)

An experimental outcome is one Task whose description carries these sections (`lifeos/jira/package.py` checks them; the ticket template has them):

1. **Outcome:** the complete outcome, not one experiment.
2. **Candidate POCs:** the candidates to try, each with what it would prove.
3. **Integration:** how a candidate is integrated and exercised end to end, in an isolated or synthetic environment.
4. **Deployment:** the promotion path to production, with the release SHA and environment parity check.
5. **Rollback:** how production returns to the prior state.
6. **Acceptance matrix:** frozen criteria (and a fresh holdout where relevant) for outcome, security, cost and rollback.
7. **Budget:** bounded time, calls and spend.
8. **Alternatives:** the approved alternatives to try if acceptance fails.
9. **Approval:** the protected acts the package may perform, each with its target and budget.

Separate tickets exist only for genuinely independent outcomes or safety boundaries, never one per experiment.

## Approve once

The executor dry run (`policy/dry-run-gate.md`) and the owner decisions are batched for the whole package. The Approval section names each protected act, its target and its budget; nothing is implied. No implicit grants, credential changes, real data, paid inference, deletes, schedules or wider deployment. Agent Go on a package approves the envelope, not an unconditional production GO. Existing UI and privacy rules remain.

## Execute, validate, promote

- One exclusive writer. Integrated experiments run in an isolated or synthetic environment against the frozen criteria.
- Choose the simplest candidate the evidence supports; discard failed paths. Reuse the tested implementation and configuration for the release, and verify the release SHA and environment parity.
- **Promotion needs all of:** the named protected approval, same-head green CI, and integrated proof of outcome, security, cost and rollback. `lifeos/jira/package.py promotion()` returns GO only then.
- **Failed acceptance:** try the next approved alternative inside the session while time and budget remain (RETRY).
- **NO-GO:** acceptance failed with the alternatives or the budget exhausted, or the deployment is unsafe. Keep the evidence, keep the prior production, close the package as research, open no speculative tickets.
- **BLOCKED:** a missing approval, CI or proof. Nothing is promoted and nothing is marked Done.
- Research closure is never reported as a delivered product. First live proof, human UAT and wait-only UAT remain as in section 3; accepted proof is reused, not re-approved.

## Release gate

`lifeos/jira/release.py` lets an Epic in Scoping release a child labelled `package` only when its package is complete; any other build child still waits, and `research` and `verdict` children release as before. A package missing a section is refused with `package_incomplete`.

## Adoption

New experimental work is written as a package. Existing tickets are not migrated. Legacy research and verdict tickets already open run as they were.
