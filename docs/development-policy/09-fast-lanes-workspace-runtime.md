<!-- LIFE OS Development Policy, part 09 of 11: Fast shipping, fast-lane coordination, workspace affinity, runtime completeness. Verbatim baseline lines 266-357; canonical with the same authority as the whole policy (see ../life-os-development-policy.md). Edit only with Jim's approval. -->

### Fast shipping mode

For an already-approved coherent feature, default to **one branch/PR, one compiled implementation handoff, and continuous execution through internal checkpoints**. Internal checkpoints are commits and proof boundaries, not new planning or permission gates. Re-open planning only for a real stop condition, scope expansion, architectural conflict, or changed authority.

Within the first two minutes of an implementation run, verify the execution boundary: writable intended workspace, correct base/branch, GitHub authentication, and whether any required external runtime credential or acceptance boundary is available. Record an unavailable external boundary once; do not rediscover it after implementation is complete.

### Fast-lane coordination mode

When Jim has approved an ordered roadmap sequence, operate two non-overlapping lanes by default:

1. **Repo-heavy lane — Codex/Claude Code.** Exactly one repository-heavy package owns the active code/config/workflow mutation surface at a time. Keep that workspace warm and move directly from implementation to targeted proof, PR, review, and live acceptance. Do not re-onboard from history between adjacent packages.
2. **Connector/config lane — ChatGPT.** While the repo-heavy lane is active, ChatGPT should proactively clear downstream connector-native prerequisites already approved on the relevant LIFE issue and not overlapping the active package: Notion properties/views/surfaces, block ids, connector access, bounded configuration inspection, Jira issue/link/status read-back, and other tiny reversible mutations supported directly by connected tools.
3. **No overlap.** The two lanes may not mutate the same repository code/config/workflow, canonical source record, schema, or presentation region concurrently. If overlap is plausible, the connector lane waits.
4. **Minimal bootstrap.** At package start, read only this policy, the exact current LIFE issue and linked LOS idea where relevant, the directly relevant product/scope section, `CODEMAP` when repo navigation is needed, the directly affected implementation/callers, and the Production Contract only for runtime behavior. Historical WBS evidence is read only for missing technical scope. Do not reread unrelated canon/history after the boundary is known.
5. **Delta-only base movement.** If `main` moves, inspect only commits since the recorded base. Continue automatically when the delta does not overlap the package claim. Stop with `BASE_MOVED_STOP` only for a material overlap or authority change.
6. **Direct review.** Jim does not need to paste a coding agent's transcript or result. Once a branch/PR exists, ChatGPT reviews the actual GitHub delta, same-head proof, and acceptance evidence directly.
7. **Meaningful state changes only.** Jira LIFE status/blocker/dependency updates happen at verified execution boundaries (start, blocker, accepted, Done), under the approved write authority. LOS owns product-priority/stage decisions; Notion Development Projects changes only for real ownership/concurrency/handoff. Do not keep GitHub/Notion status boards or create progress-document churn.
8. **Protect an active code base from coordination churn.** While a repo-heavy package is actively coding against `main`, avoid nonessential direct commits to that repository solely for planning/docs bookkeeping. Use `Development Projects` only for temporary ownership coordination; track delivery state in the owning LIFE issue. Let the active PR carry package-specific technical documentation; batch unrelated documentation edits after the active package lands.
9. **One acceptance pass.** The coding agent runs targeted proof plus at most one final full suite/runtime gate. ChatGPT performs one delta-only review and one production-shaped acceptance/read-back where required. Re-run only after changed code or a specific failed boundary.
10. **Automatic next-prerequisite pull.** While the repo-heavy lane is busy, ChatGPT should inspect only the next few approved LIFE issues in the current Jira dependency order and execute already-approved, reversible, non-overlapping connector prerequisites without a separate planning ceremony. Consequential permission gates still apply.

11. **Automatic ordered handoff.** After the active package is accepted Done and Jira read-back passes, the same authorized, correctly anchored Codex/Claude session refreshes `origin/main` and applies the **Deterministic agent work selection** rule itself. It resumes/claims only an eligible next LIFE issue, or truthfully reports `QUEUE_NOT_READY` / `NO_ELIGIBLE_WORK`. Do not re-onboard from history, guess a next package, or ask ChatGPT to manually dispatch an issue.
12. **Batch permission classes.** Jim may approve a recurring class of narrow reversible mutations once (for example: adding Jira-approved Notion properties/views, registering an approved router owner, or creating one explicitly scoped private snapshot table). That class approval applies to later approved LIFE issues only when the mutation remains within the same bounded class and no new canonical-source, destructive, privacy, scheduler, schema-family, or architecture decision is introduced. Anything outside the approved class still requires an explicit gate.

**Current standing authorization — Jim, October 5, 2026:** ChatGPT may execute already-approved, reversible Notion schema/view/surface changes and related authoritative read-back verification without asking again, provided the change does not delete data, change canonical ownership, introduce a new datastore/schema family, alter scheduling, or overlap an active Codex/Claude package. This authorization does not cover destructive changes, source-of-truth changes, runtime architecture, new schema families/datastores, scheduling, or overlapping mutations.

This mode changes coordination speed, not architecture or safety. It does not authorize a second scheduler, duplicate writer, destructive mutation, unapproved schema, overlapping repository work, or bypass of explicit acceptance/permission gates.

### Repository workspace affinity gate

Codex/Claude workspaces are **repository-scoped execution environments**. A session that can read a sibling repository is not thereby authorized or able to write it. Never route a mutation for repository B into an existing workspace/session anchored to repository A merely because the sibling path is visible.

Before **any substantive repository read, mutation, branch creation, test run, or handoff execution**, the implementer must prove all of the following for the exact target repository:

1. `git rev-parse --show-toplevel` resolves to the intended repository root;
2. `git remote get-url origin` resolves to the intended GitHub repository;
3. the repository root is writable;
4. Git metadata/worktree state needed for branch/commit operations is writable;
5. a reversible write probe in the intended repository can be created and removed successfully;
6. the intended base/branch is available from that same writable repository.

If any check fails, stop immediately before reading or changing product files with:

`WRONG_WORKSPACE_STOP — target repository is not the writable workspace`

There is no retry in a different sibling path inside the same read-only session. Do not attempt permission workarounds, cross-repo patching, manual reconstruction, archive transfer, or force operations. Cross-repository work is split by repository: one writable workspace/session per repository. A later handoff targeting another repository requires a new correctly anchored workspace/session unless that repository's writeability was independently proven in the same environment.

The Tech Lead must preserve **repository affinity** when issuing handoffs. Never tell Jim to reuse “the same Codex session” for a different repository. Tiny, single-file, reversible governance/documentation changes that are safely supported by the connected GitHub write tool should be executed directly there rather than routed through a known-wrong local workspace. Jim must not be asked to perform terminal/git plumbing merely to repair an orchestration mistake.

Use targeted proof during implementation and run the full suite at most once on the final deterministic candidate. Do not rerun green proof without changed code or a new factual failure. Tech Lead review is delta-only from the last accepted checkpoint and does not re-prove passed gates.

**Land first, accept second.** When deterministic code/config is complete and green, publish it to the intended branch/PR before waiting on external runtime acceptance, unless the changed boundary itself cannot be proved safely without that external dependency. `LANDED` and `ACCEPTED` run on separate clocks: missing live credentials, provider evidence, or connector availability may block `ACCEPTED`, but must not strand an otherwise complete deterministic candidate locally. Downstream roadmap work that explicitly requires acceptance remains blocked until acceptance passes.

Do not update Notion/GitHub roadmap status at internal checkpoints. Update canonical Jira/coordination state only on meaningful ownership, sequencing, blocker, landed, accepted, or closed boundaries. Git is the implementation history.

Environment failures get one retry at the failing boundary. After the second failure, stop with the exact blocker; do not cycle clones, auth methods, transports, or alternate execution paths.

### Runtime Completeness Gate

For any package that changes acquisition, connector behavior, runtime composition, production entry points, workflows, persistence, or scheduled execution, the Tech Lead must define before implementation:

`ENTRY POINT → REQUIRED INPUTS → CALL CHAIN → MUTATION OWNER → SUCCESS SIGNAL → FAIL-CLOSED SIGNAL → CANONICAL STATE → AUTHORITATIVE READ-BACK → PRE-MERGE PROOF → LIVE ACCEPTANCE BOUNDARY`

If any required field is unresolved:

`RUNTIME_PATH_INCOMPLETE_STOP`

No implementation handoff may proceed until the execution path is coherent.

A runtime-facing package is not a final candidate when its primary happy-path proof ends `DEGRADED`, `BLOCKED`, partially accounted, or otherwise incomplete. `DEGRADED` belongs only in explicit negative-control proof unless degradation is the user-requested outcome.

Before recommending merge, the Tech Lead must inspect the exact production entry point on the exact candidate SHA and prove:

1. the production entry point actually invokes the changed behavior;
2. every acceptance-critical runtime input is actually loaded and supplied;
3. the normal happy path reaches the promised complete state;
4. missing/incomplete inputs fail closed;
5. transport/process success cannot be reported when the promised product outcome is degraded;
6. canonical persistence uses the existing mutation owner;
7. authoritative read-back is wired;
8. deterministic pre-merge proof exercises the same composition path that production will use.

External credentials/providers may defer live `ACCEPTED` status until after promotion only when they are genuinely unavailable pre-merge. Missing runtime composition, missing input wiring, incorrect success signaling, or missing canonical persistence/read-back are implementation defects and may never be deferred to a natural production run.

For runtime-facing packages, `CI: PASS` does not imply merge readiness. Merge requires both:

`CI: PASS`

`PRE_MERGE_RUNTIME_GATE: PASS`

The Tech Lead may not recommend or perform merge until both are true.

