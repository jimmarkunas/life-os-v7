<!-- LIFE OS Development Policy, part 10 of 11: Mutation, proof, tests and TestKit. Verbatim baseline lines 358-411; canonical with the same authority as the whole policy (see ../life-os-development-policy.md). Edit only with Jim's approval. -->

## 4. Mutation, proof, tests, and TestKit

Prefer deterministic local evidence and small representative fixtures. Run the domain proof, not unrelated suites. Live Gmail, Outlook, Notion, web, or other connector reads belong in the coding run only when that boundary changed and cannot be proved locally. Verify docs/config by exact diff/read-back; deterministic code by targeted unit/fixture/static proof; shared/writer/runtime-facing code by targeted tests plus one changed real-boundary proof only when that boundary changed; consequential source writes by direct post-write verification. Green CI proves code runs; boundary evidence proves changed behavior works. Do not rerun green proof without changed code or a new factual failure.

Before implementation state the changed claim, failure that could keep it false, lowest boundary exposing it, and fail-closed negative control. Use targeted proof first, affected regression once, and full suite at most once before landing; then rely on main CI.

### Standing composability / architecture-health phase gate — Jim-approved October 19, 2026 (LIFE-79)

This is a **repeatable safeguard using existing LIFE Jira and PR review**, not an extra development workflow, scheduler, QA hierarchy, status board, or permission gate. It supplements the locked "one live implementation per behavior" and User Outcome Gates; never block today's actual user outcome on a speculative global cleanup. The **first full review follows LIFE-78 M2 acceptance** (LIFE-23 map, LIFE-24 small remediation). Apply the routine PR check only after this policy amendment is explicitly reviewed and merged.

**A. Every substantive implementation PR — bounded reuse check, within its existing same-head PR evidence.** Record one compact `Owner/contract | Existing primitive and direct callers checked | Added or modified live behavior | Superseded paths removed or N/A | Relevant user-outcome + negative proof | Residual complexity/debt issue or none` receipt in the existing LIFE issue/PR. The delivery owner checks only changed files, CODEMAP and actual adjacent consumers; do not reread the whole repository. Fail an unauthorized duplicate writer, canonical store, scheduler, orchestration path or unsafe one-consumer framework; don't create speculative abstractions merely to reuse code. Shared primitives require at least **two actual current consumers**. If leaving justified duplicate mechanics or adding unavoidable complexity, record concrete reason, accountable owner, and repayment trigger within the current LIFE issue. Trivial docs-only and presentation-copy changes state N/A; no second human review or Jim approval is added for otherwise already-approved work.

**B. Formal architecture-health phase gate — once after each accepted major product phase/release train, and never more than three accepted substantive implementation packages apart.** This is **event-driven at the existing Jira release-acceptance boundary**, not clock-scheduled. Tech Lead performs a **15–30-minute, evidence-bounded** health check before starting the next *major* feature train, limited first to the touched domains and known hot spots. Compare the previous gate's evidence: canonical ownership and duplicate writers/state; superseded paths; existing platform primitive reuse; CODEMAP/direct callers; workflow/secret setup repetition; stale governance pointers; deterministic suite runtime; agent bootstrap/change surface; material incidents and actual product latency. Expand inspection only on specific demonstrated drift. Use existing Jira release issue as the sole phase-gate receipt, not one new issue per review.

**C. Decision and action:** `PASS`, `ACCEPT_DEBT_WITH_OWNER` (noncritical debt tracked in existing/new appropriately owned LIFE issue, with clear removal trigger no later than the next gate), or `REMEDIATE_BEFORE_NEXT_MAJOR_TRAIN` (proven duplicate production authority, competing scheduler/store, unsafe boundary or recurring significant architecture/performance regression). Critical security, data integrity and user-outcome failures remain blocked by their original gates regardless of this review. Emergency/hotfix packages keep all existing safety, approval and read-back requirements; they may defer **only** noncritical hygiene, with owner/next-gate repayment. Jim approves departures from locked architecture, broad remediation, and final user outcome, not each routine no-drift review.

**D. Enforcement must be measurable and cheap.** Reuse `tests/contracts/`, CODEMAP freshness and established CI for objective invariants. Existing baseline technical debt is initially logged, not cause for indiscriminate failing builds. Don't add a fuzzy duplicate-code classifier, another agent scheduler, a numerical LOC-reduction quota, or a new global approval step. Metrics are user-visible regressions, duplicate live implementations, agent bootstrap/change-time, representative files touched, CI duration, stage failure rate, and outcome latency. One-time remediation starts with LIFE-23/LIFE-24 **after** LIFE-78 M2; LOS-40 retains the standing product requirement. Any code/config/CI or production activation changes still need their actual applicable authorization, protection rules and tests.

### Decision numbers are claimed, never picked — Jim-approved October 20, 2026 (LIFE-89)

Before writing a new decision in `docs/DECISIONS.md`, run `python -m lifeos.decisions claim` and use only the number it prints. It claims the number on GitHub as the tag `decision-claim/D###`; GitHub creates a tag atomically, so two parallel sessions can never get the same number. Never take "the next number" from a local copy of the log: that is how D229, D231, D235 and D236 were each taken twice on October 20. A claim that ends up unused leaves a harmless gap. An amendment or advice to an existing decision keeps that decision's number (`## D12 amendment …`). The contract test `DecisionNumberTests` fails any PR that reuses a number.

### Jobs incremental architecture stress gate

For LIFE OS Jobs, every new capability, integration, source adapter, crawler increment, qualification stage, persistence path, or other architectural increment must be stress/load tested during its initial implementation before the pattern is replicated.

A feature working at minimum scale proves functional correctness only. It does **not** prove architectural fitness.

Each increment must be deliberately exercised beyond its immediate production requirement toward the credible future operating envelope, with proof designed to expose scaling limits while architectural change is still cheap. As applicable, this must test source/provider fan-out, concurrency, rate-limit behavior, timeout amplification, retry amplification, memory/runtime growth, persistence pressure, queue/backlog behavior, and failure isolation.

For example, a crawler implemented against one job source must be tested with simulated or real multi-source load representative of the expected future source count before its architecture is accepted for replication. Passing against one source is insufficient evidence that the same design can safely support many sources.

Jobs incremental acceptance therefore requires both:

1. **Functional proof** — the increment produces the correct user/product behavior.
2. **Scale proof** — the architecture remains viable under deliberately increased load and fails predictably/fail-closed when pushed beyond its supported envelope.

The purpose is early architectural falsification, not benchmark theater: actively try to break the proposed architecture before multiplying it across integrations.

> **Build at one. Test toward many. Scale the pattern only after it survives.**

### Production-shaped regression gate

When a defect is exposed by live acceptance or production evidence, the next correction must prove the promised outcome on the actual failing shape, not merely on a plausible synthetic happy path.

1. If representative failing input is available, regression proof must use that input or a fixture preserving the acceptance-critical structure that caused the failure.
2. Proof must traverse the lowest deterministic production-equivalent path that can falsify the claim. For parser/compiler/scoring defects, prove `production-shaped evidence → extractor output → compiler/scorer output → final disposition`.
3. A change that only renames an error, improves diagnostics, preserves an intermediate representation, or otherwise changes a symptom without proving the promised user outcome is `CORRECTION REQUIRED`.
4. Green CI is insufficient when its tests do not assert the acceptance-critical production shape. The Tech Lead must inspect the same-head regression proof and verify that the previously failing shape now reaches the promised state.
5. For LIFE OS Jobs qualification, merge is prohibited unless representative production-shaped JD evidence proves: `employer/ATS JD + actionable Apply URL + usable requirement language → non-empty structured Requirement rows → non-null Fit through the actual adapter/qualification path → correct disposition`. Genuinely empty/unscoreable requirements must remain fail-closed.
6. If post-merge live acceptance still fails, the next correction must be grounded in the exact new live failure evidence. Do not repeat the same hypothesis without new proof.

Jim-approved lightweight Composable TestKit under `tests/` may provide reusable builders, deterministic boundary doubles, behavioral cases, and lightweight contract-harness functions when they serve at least two real test consumers. It is test-only and owns only reusable test mechanics; product/domain contracts own behavior. TestKit exists to reduce duplicated setup/context and make implementation boundaries easier to swap. It may not become a runner, custom framework, plugin, registry/config language, datastore, scheduler, orchestrator, evidence store, inheritance framework, production dependency, or parallel implementation.

