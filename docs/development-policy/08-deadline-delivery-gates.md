<!-- LIFE OS Development Policy, part 08 of 11: Deadline Mode, delivery and activation contract, outcome and blocker gates. Verbatim baseline lines 192-265; canonical with the same authority as the whole policy (see ../life-os-development-policy.md). Edit only with Jim's approval. -->

### Deadline Mode — Jim-directed October 19, 2026

A user-stated time limit (e.g., "within 10 minutes", "by 5 PM CT", "within the hour", or "now, in the next N minutes") is an **execution constraint** for the authorized current LIFE outcome, not a suggestion. Activate Deadline Mode without another planning or approval ceremony. **It never waives existing security, scope, model-spend, privacy, source-write, G2 merge, or independent live-acceptance permission gates.**

**Real clock, not estimated time.** Before research, code, or a handoff: capture actual local time via `TZ=America/Chicago date '+%Y-%m-%d %H:%M:%S %Z'` and UTC epoch via `date -u +%s` in the authorized execution shell. For a relative limit, compute an absolute deadline from that measured start: `LIFEOS_DEADLINE_EPOCH=$(( $(date -u +%s) + N * 60 ))`, replacing N with the requested minute count; for a wall-clock deadline, resolve the specified date/time and America/Chicago zone (DST-aware), then store its epoch. Do **not** invent a clock or derive elapsed time from chat turn counts or agent estimates. If a tool/session cannot access a real clock or preserve the deadline, state that immediately; use a real clock source in the current available execution environment before making time-based claims.

At **every significant step** (after inspection, implementation, proof, review, before merge/deploy/dispatch, and after external waits), sample `date -u +%s` again, calculate `remaining_seconds=$(( LIFEOS_DEADLINE_EPOCH - $(date -u +%s) ))`, and compare elapsed percentage against the original budget. Agents that launch subprocesses must pass the absolute deadline onward; never reset the start after a handoff or an interrupted session. No background monitoring, timer daemon, new scheduler, datastore, or additional workflow is authorized.

**Deadline-driven execution, using the existing issue, repository and owner:**
1. **0–10%:** Verify active repo V7, named deliverable, actual enabled end-to-end path, required approvals, operator tool permissions and the earliest hard blocker. Give Jim a concrete achievable slice *immediately* if the whole request cannot physically fit; do not promise the impossible or begin unrelated inspection.
2. **10–50%:** Have the one existing capable implementer build the smallest complete production-shaped slice continuously; use one bounded handoff at most when actually required by tool permissions. Reuse known good proof; do not create diagnosis/review/approval loops.
3. **At 50% elapsed:** Automatically drop nonessential polish, refactors, broad research, extra documentation, repeated full tests and optional scope. Keep the approved visible user outcome and required negative controls. Disclose any change that would shrink the actual requested functionality; do not silently redefine success.
4. **At 75% elapsed:** Freeze feature scope. Finish integration, minimum deterministic proof, exact-head CI/review and safe release readiness; stop adding secondary capabilities. Check remaining time before any long-running operation.
5. **At 90% elapsed:** Prioritize the already-authorized safe merge/activation and authoritative destination read-back. If a necessary permission or live boundary prevents shipping, report the exact operation, capable owner and evidence **before** the deadline; preserve the tested candidate. Never skip CI, invent approvals or force a release.
6. **At deadline:** Report one truthful outcome: `DELIVERED ON TIME` only for live real-destination read-back before the deadline; `BLOCKED BEFORE DEADLINE` when a concrete external permission/dependency was surfaced promptly; otherwise `DEADLINE MISSED` with the exact remaining action and no mislabelled success. A successful PR, merge, test, shadow result or dispatcher acknowledgement alone is not delivered product.

**Tech Lead enforcement:** A deadline-bound package without verified start/deadline epoch, checkpoint rechecks, evidence of automatic removal of optional work when time tightened, or a truthful final outcome is `DEADLINE_MODE_VIOLATION — CORRECTION REQUIRED`. Put checkpoints in the existing LIFE issue/PR or concise delivery message only; do not create a new tracking artifact. Deadline Mode extends the existing Delivery/Activation and User Outcome Gates; it does not create another queue, scheduler, autonomous background promise or authority to override Jim's permissions.

### Delivery and activation contract — Jim-directed October 19, 2026

**User outcome is the deliverable.** For requests to *build, finish, ship, enable, activate, or turn on* a feature, the Tech Lead and implementer must work backward from the actual enabled user-visible behavior in its canonical destination. A green test, PR, merge, successful transport, private/shadow persistence, partial proof, or running workflow is **not** an operational feature. Do not report `DONE`, `LIVE`, `READY FOR ACTIVATION`, or “end to end” unless the requested mode is actually executable, the real path has completed, and the authoritative destination has been read back. `LANDED — ACCEPTANCE PENDING` is permissible for completed code awaiting an external live gate, but is not product delivery.

1. **Discover activation blockers before building intermediates.** At the start of the *same* user-outcome package, inspect the actual production entry point, mode gates/switches, deployment/dispatch authority, budgets, and downstream user-visible consumer. If a requested `on` mode is a stub, intentionally rejected, cannot influence presentation, or exceeds the authorized capacity, surface that **before** spending time on shadow-only proofs or subsidiary PRs. Make the smallest explicit mode/quality/capacity decision immediately; never silently reinterpret “full on” as “shadow” or “canary.” When a real user choice is required, ask it once, early, with a recommended bounded alternative and its limits.
2. **One accountable delivery owner, verified operators.** The designated implementer owns the complete usable vertical slice through deterministic proof and PR corrections. The Tech Lead coordinates acceptance and may review on request; routine PR delivery does not require a separate reviewer or review handoff. Before assigning a live operation, verify the named operator actually has the necessary tool, repository-variable, credential and deployment permissions. A session that cannot execute it must not be the dispatcher. Preserve ownership boundaries, but coordinate a **single executable handoff** to the capable operator; no circular agent-to-agent permission loops, repeated handoffs, or requiring Jim to relay unchanged evidence. Reuse approvals within their exact scope; ask again only for an unapproved consequential change.
3. **Prove the actual destination.** Define the minimum pass case before implementation: one representative real eligible input → the production entry point → required computation/validation → canonical persisted state → user-facing output or action → authoritative read-back, with at least one fail-closed negative control. An external/paid execution requires its specific permission. Static/shadow-only proof can be a milestone but cannot close a request for visible, enabled behavior. Include any missing activation wiring or visible consumer in the *same outcome scope* or disclose the missing functionality as a blocking product gap before calling the package ready.
4. **No diagnostic or review treadmill.** Each factual gate is checked once; share its evidence across owners and related issues. Repeat it only when the relevant code, runtime, or failure evidence changes. Repair an invalid diagnostic at its smallest failing query; do not expand it into unrelated QA programs. A targeted delta review is optional unless an issue-specific or GitHub rule requires it; perform one integrated handoff only when necessary. An unchanged blocker must not trigger another identical plan, prompt, agent referral, review, or production run.
5. **Urgency changes priorities, not safety.** When Jim gives a deadline, immediately prioritize the smallest *working, visible* release over more internal architecture/proof milestones. Within the first useful inspection, disclose any mode that cannot run, missing authorized dispatcher, or hard capacity limit; provide the concrete achievable scope and the shortest path. Never spend the deadline discovering that the requested mode was disabled from the beginning. Existing security/privacy gates, one-owner rules, explicit paid-model permission, no-paid-TinyFish rule, runtime integrity, and production write authorization remain mandatory.
6. **Truthful five-state report, not ceremonial status.** When relevant, distinguish: `IMPLEMENTED` (candidate code), `CI PASS` (deterministic tests), `MERGED` (main), `RUNNING/ENABLED` (actual runtime), and `ACCEPTED` (real output and authoritative read-back). State only verified stages. If not accepted, give **the single concrete remaining blocker and named executor**. Do not celebrate a partial gate as the completed request.

**Tech Lead enforcement:** A candidate that omits the actual activation/consumer path, treats unavailable permissions as a downstream surprise, substitutes shadow-only success for requested live behavior, or claims completion without authoritative read-back fails the **Delivery/Activation Gate** (`DELIVERY_CONTRACT_VIOLATION — CORRECTION REQUIRED`). Correct the existing Jira issue/PR; do not create another governance artifact, approval tier, scheduler, datastore, or permanent QA workflow. This gate enforces the existing User Outcome and Runtime Completeness Gates; it does not replace them or waive Jim's required production permissions.

### User Outcome Gate

Before substantive architecture, refactor, decomposition, migration, or platform work:

1. Read the relevant canonical product requirements.
2. Identify the user-visible product outcome the package must preserve or improve.
3. Identify the production-shaped acceptance that proves that outcome.
4. Treat that acceptance as a release gate for the package.

A package may not be declared `LANDED` when its relevant user-outcome gate fails, even if unit tests, component tests, repository contracts, CI, architecture boundaries, or a cleaner/more-composable implementation pass. Internal architectural correctness is necessary where required, but it is not a substitute for working product behavior.

For decomposition or refactoring of a previously working vertical slice:
- preserve the last known-good vertical slice continuously;
- extract one seam at a time;
- rerun the same user-outcome acceptance after each material extraction;
- stop the refactor immediately if the user-outcome gate regresses.

> No horizontal rewrite before one vertical slice passes.

Shared platform/core/repository/query/integration abstractions may not be treated as sufficient product progress when no real source can complete the full user journey.

### Blocker Qualification Gate

`BLOCKED` is permitted only when a concrete dependency outside the approved package boundary prevents safe implementation or required deterministic proof. A blocker must prevent execution, not merely make execution less convenient.

Before declaring `BLOCKED`, the implementer must:

1. identify the exact blocked operation;
2. identify the exact external dependency preventing it;
3. prove the dependency cannot be resolved within the approved mutation boundary and execution budget;
4. complete every remaining non-blocked part of the package;
5. publish a deterministic candidate when implementation is complete and only live/external acceptance remains unavailable.

The following are not blockers by themselves: an unclean working tree whose changes do not overlap the mutation surface; unrelated existing changes; uncertainty resolvable by one bounded direct read; unavailable live credentials when deterministic implementation/proof can still complete; unavailable browser/screenshot tooling when implementation can still complete and visual review is an external acceptance boundary; unavailable live production acceptance after deterministic implementation is complete; a future dependency that does not prevent the current package; fear of a conflict when no concrete conflict has been identified; or unavailable optional proof beyond the package's required acceptance.

`BLOCKED` must never mean “I stopped because proceeding required judgment.” When canonical authority already defines intended behavior and mutation scope, execute within that boundary.

A live look that has not happened does not hold Done; report `DONE` and name the watch. If one narrow part is externally blocked but the remaining package can complete, complete the rest first and report `PARTIAL CANDIDATE — BLOCKED ON <exact external dependency>`.

After one `BLOCKED` result, the next Tech Lead handoff must either remove the exact blocker or materially change the execution boundary. Do not resend substantially the same package and expect a different result.

This gate does not weaken hard stops for wrong workspace, unavailable required canonical authority, destructive ambiguity, locked-architecture conflict, missing required implementation inputs, or deterministic proof failure.

