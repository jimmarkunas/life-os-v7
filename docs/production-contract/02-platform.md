# Production Contract part 2: Authority model, invariants, systems, control plane, shared runtime

<!-- Part of docs/life-os-production-contract.md (the index). Section text moved unchanged. -->

## Authority model — SELF-CONTAINED CURRENT CONTRACT

This file is the complete current LIFE OS production/runtime authority. V7 scheduled execution must read this live file's gates before source reads or mutations; the retired `LIFE OS Daily Runs` reads nothing.

**Historical Production Contract revisions are provenance only. They are not runtime dependencies.** Do not fetch, traverse, incorporate, or require prior `docs/life-os-production-contract.md` blobs in order to execute the current contract. Git history preserves the provenance and supersession chain through v2.13.0.

Current supporting specifications may be read when their lane is exercised, including:
- `docs/notion-query-conservation-policy.md`
- `docs/run-reliability-policy.md`
- `docs/job-engine-contract.md`
- `docs/newsletter-job-discovery-contract.md`
- `docs/us-remote-job-discovery-contract.md`
- `docs/scale-up-job-discovery-contract.md`
- `docs/skilled-worker-job-discovery-contract.md`
- `docs/weekly-reports-archive-contract.md`
- `docs/interview-information-architecture-contract.md`
- `docs/interview-context-linkage-contract.md`
- `docs/interview-canon-production-contract.md`
- `docs/amazon-orders-production-contract.md`
- `docs/accountability-dashboard-sync.md`

Those documents are subordinate implementation/lane specifications. If any supporting specification conflicts with this file, **this current Production Contract wins**. A required supporting source that cannot be read completely and reliably degrades only the affected lane unless this contract explicitly makes it a whole-run prerequisite.

## Global production invariants

- Scheduling authority is single and approved schedules may be multiple. This Production Contract is the one authority that approves LIFE OS schedules, and V7's GitHub Actions workflows are the sole scheduled execution infrastructure. No ChatGPT automation may be enabled for LIFE OS production work (`LIFE OS Daily Runs` is retired and disabled). Each approved workload runs on its own trigger and need not wait for unrelated workloads. No second scheduler service, helper schedule, shadow monitor or watchdog, retry task, mailbox monitor, archive task, source-census task, duplicate orchestration task or new datastore is authorized.
- **Per-workload approval.** A new or changed schedule is separately approved by Jim and records: one authoritative owner; workflow and trigger; Central Time and daylight-saving interpretation; producer and consumer dependencies and source freshness targets; resource and provider budgets, shared quotas, Notion/Jira contention, and exactly one canonical writer for any workload that writes (a read-only workload needs no write permission); execution and retry bounds; durable checkpoints with missing, duplicate and late start recovery; complete, degraded and incomplete receipts with exact run, SHA and authoritative read-back; natural-run evidence; rollback; and UAT. It also needs a measured safe pilot. While the LIFE-686 stability freeze is still active, it needs the named LIFE-686 exception; once Jim independently accepts the freeze's stability evidence that exception no longer applies, and ordinary protected approval is never removed.
- **v2.15.17 is non-activating.** The existing authorized V7 hourly tick and domain workflow scheduling (`hourly.yml`, `domains.yml` and the other existing V7 workflows) stays authorized as configured; individual approval applies only to new or changed schedules and is not a retrospective reauthorization of existing operations. `LIFE OS Daily Runs` stays retired with no automatic transfer. Existing configured schedules are unchanged, Followup stays on its existing hourly contract, and domain ownership, canonical writers and undeployed or quarantined statuses are unchanged. Cadence examples (core hourly, Jim and Matt every two hours, boards three times a day, weekly reports, daily or weekly maintenance, event-triggered work) are proposals only and are not approved scheduled execution.
- `LIFE OS Daily Runs` stays disabled. It must not be enabled, replaced, or rescheduled except by Jim's explicit current instruction recorded in this file.
- If `LIFE OS Daily Runs` is found enabled, do not run it; report `RETIRED EXECUTOR ENABLED` to Jim. Do not re-enable it when found disabled, and create no replacement.
- A connector, API, query, tool, entitlement, rate limit, timeout, permission failure, malformed response, stale artifact, truncation, missing required field, trigger mismatch, ambiguous target, failed write, or failed read-back is **DEGRADED**, never zero/no-change evidence.
- Never overwrite known-good persistent state with stale, partial, ambiguous, failed, or uncorrelated data.
- Never substitute an older PASS artifact for the current run. Prior accepted state may remain visible only when clearly identified as last accepted while the current lane is degraded.
- Retries and replay must be idempotent. Retry the smallest failed unit and re-read authoritative state before a mutation retry.
- Before a surgical Notion mutation, resolve the exact target/natural key. If absence or identity cannot be proven, do not create a possible duplicate.
- Never mutate Jira planning data unattended, except the one approved V7 workload in `V7 JIRA SPRINT ROLLOVER AND DAILY RECONCILIATION` (JFM and MEJ sprint and recurrence maintenance, once activated). Production Jira reporting uses correlated GitHub snapshots.
- A natural scheduled run is post-promotion live acceptance, not the first test of changed behavior. Changes require the smallest deterministic/mechanical acceptance before production promotion; when GitHub CI covers the changed path, the exact candidate must pass before promotion.
- No source, mutation, or fail-closed guardrail may be weakened merely to make a test or recovery pass.
- **Skilled Worker is quarantined — Phase 2.** Current production acquisition, ingestion, source-driven lifecycle mutation, continuity recovery, current-run acceptance dependency, and active report lane output are disabled. Preserve known-good Skilled Worker Job Ledger rows and the persistent Notion reference surface. Skilled Worker may return to production only after Jim explicitly authorizes Phase 2 re-entry.

Canonical persistent natural keys:
- Drive Index = exact Drive File ID.
- Weekly Reviews = Week Ending.
- Target Companies = normalized Company name.
- Job Ledger = exact Stable Job Key.
- Bills = existing bill row/page.
- Recruiters = Week Ending + normalized Recruiter / Person + normalized Recruiter Company + normalized Role.
- Attention = Week Ending + Category + normalized Item.

## Canonical systems and surfaces

These are the durable Notion/Drive identities. Listing a surface here does not mean any executor under this contract refreshes it; see `DAILY RUNS RETIREMENT OVERRIDE` for what is delivered.

- Daily Report: `notion:daily_report`
- Dashboard: `notion:dashboard`
- Daily Report Template / UI canon: `notion:daily_report_template`
- J Search front door: `notion:j_search_front_door`
- Recruiters & Human Outreach: `collection:recruiters_human_outreach`
- Attention: `collection:attention`
- Hiring Pipeline context hub: `notion:hiring_pipeline_hub`
- US Remote Jobs: `notion:us_remote_jobs`
- Job Boards / US Web (RETIRED — superseded by US Remote Jobs): `notion:job_boards_us_web`
- Scale Up Jobs (ACTIVE): `notion:scale_up_jobs`
- Skilled Worker Jobs: `notion:skilled_worker_jobs`
- Canonical Job Ledger: `collection:canonical_job_ledger`
- Job Lane Configuration: `collection:job_lane_configuration`
- Target Companies: `collection:target_companies`
- Weekly Review Hub: `notion:weekly_review_hub`
- Weekly Reviews: `collection:weekly_reviews`
- Weekly Reports Archive hub: `notion:weekly_reports_archive_hub`
- Drive Index: `collection:drive_index`
- Bill Tracker: `notion:bill_tracker`
- Bill Tracker data source: `collection:bill_tracker`
- Interview Archive: `notion:interview_archive`
- Amazon Orders database: `notion:amazon_orders_database`
- Amazon Orders data source: `collection:amazon_orders`
- Accountability Dashboard: `notion:accountability_dashboard`
- Saturn Protocol: `notion:saturn_protocol`

Production job child pages are persistent canonical views plus one retired surface: US Remote Jobs (active), Scale Up Jobs (active), Skilled Worker Jobs (quarantined reference). Job Boards / US Web is RETIRED and superseded by US Remote Jobs — it must not be deleted or archived, but no acquisition module writes to it. Do not recreate, move, rename, or delete active pages from any executor. While Skilled Worker is quarantined, its page remains a persistent reference surface rather than a current production acquisition output.

## Control plane, cadence, and run identity

### Cadence and run identity — RETIRED

- **There is no cadence defined by this contract.** The hourly `:00` America/Chicago schedule, the 12:00 AM / 6:00 AM / 9:00 AM / 12:00 PM / 6:00 PM CT branches, the midnight Newsletter status, and the 12 PM / 6 PM change markers belonged to the retired `LIFE OS Daily Runs` and are void.
- **Schedules are V7's, approved here.** V7's GitHub Actions workflows run on the schedules their own workflows declare (in `jimmarkunas/life-os-v7`), and each schedule exists only if approved under the global production invariants. This contract does not set or change any existing schedule and does not depend on one.
- **No run stamp.** The `**LIFE OS run:** …` Daily Report stamp, the `DAILY REPORT RUN TIMESTAMP DEGRADED` check, and per-slot `scheduled`/`completed` identity are DISABLED — NOT DELIVERED. Nothing may write a stamp that implies a Daily Runs invocation.
- The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

### Continuity v2 — DORMANT (NO PRODUCER)

Canonical state remains `data/run-continuity-current.json` (schema version `2`), validated by `scripts/run_continuity_gate.py`, and the lane set and recovery classes are fixed by that validator's `LANE_SPECS`; they may not be reclassified ad hoc.

**Status: DISABLED — NOT DELIVERED.** Continuity transitions were supplied only by `LIFE OS Daily Runs` (scheduled slot, trigger, required lanes, evidence references). With Daily Runs retired nothing supplies them, no required-lane set is in force, no natural-boundary obligation (6 PM archive, Sunday Weekly Review, Monday rollover or Notes reset) is created, and no recovery debt may be created or extended on behalf of the retired schedule. V7 has no verified Continuity delivery recorded here. The existing state file is retained read-only as history; its `RECOVERY_REQUIRED` or `DEGRADED` visibility describes the retired schedule and is not a current production signal.

What stays binding wherever the code is run manually:
- `data/run-continuity-current.json` has exactly one mutation authority: the deterministic GitHub runtime, through `scripts/run_continuity_runtime.py` invoking the unmodified transition algorithms in `scripts/run_continuity_gate.py`. No LLM or connector may author, truncate, summarize, merge, or reconstruct Continuity JSON or write the file directly (this corrects the Sep 15 12 PM production failure, in which a ChatGPT-authored synthetic checkpoint containing `"lanes": {}` overwrote the complete lane state).
- `data/continuity-runtime-trigger.json` carries transition inputs only, never canonical state. It has no producer while Daily Runs is retired; `workflow_dispatch` on `life-os-runtime.yml` remains a manual/operator/test surface.
- For every mutation the runtime: (1) loads and validates the exact previous state; (2) executes exactly the requested gate transition; (3) validates the candidate (`schema_version == 2`, lanes exactly equal to `LANE_SPECS`, non-empty lane map, valid `required_lanes`, quarantined lanes never required); (4) persists atomically only after validation; (5) re-reads and verifies the persisted state equals the candidate exactly. On candidate or persistence failure it keeps the last-known-good state, retries once from the same validated prior state, then returns DEGRADED. It never manufactures a PASS and never creates a second Continuity state file.
- Skilled Worker stays quarantined: it cannot be a required lane or create recovery debt.
- Malformed schema, missing/extra lanes, invalid recovery-class changes, timezone-naive timestamps, backward slots, source-frontier gaps, unknown/duplicate obligations, stale evidence, dependency mismatch, or a quarantined-lane requirement fail closed.

The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

### Reliability budget

Use the live `docs/run-reliability-policy.md` within this contract's boundaries. The ordinary transient recovery cadence is immediate, then approximately 20 / 60 / 180 seconds, with retries scoped to the smallest failed unit. For correlated GitHub artifacts, use a unique current-run trigger, poll/re-fetch the exact artifact, and retry the trigger only within the bounded policy. `DEGRADED` is a last-resort safe state after recovery exhaustion or when retry is unsafe.

## Shared Platform Runtime — PRODUCTION AUTHORITY

The locked GitHub production topology is exactly four workflows:
- `.github/workflows/life-os-runtime.yml`
- `.github/workflows/jira-report-snapshot.yml`
- `.github/workflows/jira-sprint-rollover.yml`
- `.github/workflows/life-os-acceptance.yml`

No other workflow file is authorized in this repository without a future approved architecture change. No GitHub workflow may contain a recurring `schedule`/`cron` in the retired system's repository. V7's scheduled workflows live in `jimmarkunas/life-os-v7` and are outside this rule.

`life-os-runtime.yml` is the shared bounded worker for:
- Gmail provider acquisition
- Newsletter
- Notion Job + Bill Tracker snapshots
- Paid Bills snapshot

Authorized Runtime trigger files:
- `data/mail-acquisition-trigger.txt`
- `data/newsletter-ingestion-trigger.txt`
- `data/notion-job-snapshot-trigger.txt`
- `data/notion-bill-paid-snapshot-trigger.txt`

These trigger files have no producer while `LIFE OS Daily Runs` is retired, so the modules run only by manual `workflow_dispatch` unless Jim authorizes a new producer here. Manual `workflow_dispatch` requires explicit module selection. Skilled Worker has no active Runtime module or trigger while quarantined.

The Runtime directly composes existing product scripts; it may not introduce a second Python orchestration framework, competing datastore, duplicate canonical writer, or helper scheduler. Selected modules execute in one shared GitHub job under concurrency group `life-os-platform-runtime`. Active Job Ledger writers therefore serialize through the existing Runtime publication path.

Selected modules report PASS or DEGRADED; unselected modules are SKIPPED. Failure of one selected module must not automatically suppress an unrelated safe selected module. After all selected modules have had their bounded opportunity, the workflow fails if any selected module is DEGRADED or output publication fails.

Module retry behavior:
- Gmail OAuth/token requests and Gmail provider requests own their request-level transient retries with delays `0 / 20 / 60 / 180` seconds, honoring bounded `Retry-After` when supplied; authentication/authorization and deterministic validation failures do not retry.
- Newsletter ingestion gate: four attempts with delays `0 / 20 / 60 / 180` seconds.
- Notion Job + Bill Tracker snapshot scripts: four attempts with inter-attempt waits `20 / 40 / 60` seconds.
- Paid Bills snapshot: four attempts with inter-attempt waits `15 / 30 / 45` seconds.

The Runtime is the single Git publisher for outputs produced by selected Runtime modules. Stage only selected-module outputs, publish at most one combined Runtime output commit per invocation, use bounded fetch/rebase/push reconciliation against current `main`, and never force push, rewind, delete, or recreate `main`.

Newsletter checkpoint/acceptance evidence may publish after deterministic batch/backfill preparation succeeds even when the final Newsletter ingestion gate is DEGRADED. Notion snapshot outputs remain read-only evidence.
