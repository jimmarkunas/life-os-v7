> **Canonical home: V7 (Nov 27 2026, 2:45 AM CT; LIFE-1060).** This file, `docs/life-os-production-contract.md` in `jimmarkunas/life-os-v7`, is now the single canonical copy of the Production Contract. The copy in `life-os-automation` (V1) is historical reference only and is no longer edited. The text below is v2.15.19 as it stood at `life-os-automation` commit `3fbf6c6`: no rule, wording or version was altered by the move. The one difference: this repository is public, so each Notion page ID is replaced by a placeholder `NOTION-PAGE-nn` and each `collection://` data source ID by `NOTION-COLLECTION-nn` (the titles in the surrounding text still name each page and source).

# LIFE OS Production Contract

**Status:** Current LIFE OS production contract. `LIFE OS Daily Runs` is RETIRED and holds no execution authority (see `DAILY RUNS RETIREMENT OVERRIDE`).  
**Version:** 2.15.19 — V7 Jira sprint rollover and daily reconciliation for JFM and MEJ recorded as activated (LIFE-918, November 25, 2026), eight future sprints, short sprint names; JFM and MEJ quarterly metric promotion authority with first-Wednesday timing (approved, not delivered) / 2.15.18 — V7 Jira sprint rollover and daily reconciliation approved for JFM and MEJ / one scheduling authority, multiple individually approved V7 GitHub Actions schedules (non-activating) / `LIFE OS Daily Runs` retired / V7 GitHub Actions sole scheduled infrastructure / Interview Canon writer ownership moves to V7 (retired here) / V7 Scale-Up active / Skilled Worker Phase 2 correction / Followup lane granted to V7 (disabled until acceptance).
**Timezone:** America/Chicago.  
**Owner:** Jim, who is the single scheduling authority through this contract. Scheduled execution: V7 GitHub Actions only, each workload on its own Jim-approved trigger. `LIFE OS Daily Runs` owns nothing.  
**Machine bootstrap routing index:** `docs/LIFE_OS_MACHINE_BOOTSTRAP.md` (routing only; this Production Contract remains the authority for scheduled/runtime behavior).  
**Schedule:** none for `LIFE OS Daily Runs` (retired, disabled). Scheduled LIFE OS execution runs only on V7 GitHub Actions: the existing scheduled tick (`hourly.yml`) and its domain workflows (`domains.yml` and the other existing V7 workflows). This contract does not change those schedules and creates no new one (v2.15.17, v2.15.18 and v2.15.19 add no schedule; v2.15.18 approved one workload, the V7 Jira sprint rollover and daily reconciliation for JFM and MEJ, which runs inside the existing hourly tick and was activated under LIFE-918 on November 25, 2026, as v2.15.19 records; v2.15.19's quarterly metric promotion runs inside that same daily reconciliation and is not delivered).

## DAILY RUNS RETIREMENT OVERRIDE — October 20, 2026 CT

> LIFE OS Daily Runs is RETIRED, disabled, and has no current LIFE OS production execution authority. V7 GitHub Actions workflows are the sole scheduled LIFE OS execution infrastructure, and each scheduled workload runs only on a schedule Jim has individually approved under this contract.

This section is the highest-precedence text in this file. It supersedes every other statement below, in this file and in every supporting specification.

- **Retired means inert.** `LIFE OS Daily Runs` must not be enabled, scheduled, re-enabled, repaired, replaced, or run. It may not read or write Notion, Gmail, Outlook, Calendar, GitHub, or any other system as a LIFE OS production executor. Finding it disabled is the expected state, not a `PRODUCTION SCHEDULER DEGRADED` condition.
- **Superseded requirements.** Any statement below that requires `LIFE OS Daily Runs` to remain enabled, run hourly or at the 12:00 AM / 6:00 AM / 9:00 AM / 12:00 PM / 6:00 PM CT slots, perform Interview Advisor work, acquire or route mail, write heartbeat/trigger files, supply continuity transition inputs, perform browser recovery, own a scheduler, or create/update/render the Daily Report is **superseded and inoperative**. Where the body below names `LIFE OS Daily Runs` (or "ChatGPT" as the scheduled executor), read it as provenance describing the retired design, not as a current obligation or as a description of what any other system now does.
- **No automatic transfer.** Nothing that `LIFE OS Daily Runs` previously owned moved to V7 by virtue of its retirement. Only behavior that V7 has actually implemented **and** verified is delivered, and the only such ownership this contract recognizes is the explicit V7 grants in the `MAIL ALERTS OWNERSHIP OVERRIDE` and `INTERVIEW CANON OWNERSHIP OVERRIDE` sections below (Interview OS writing remains a dry run until Jim sets `INTERVIEW_OS_WRITER=v7`).
- **Status of formerly ChatGPT-owned functionality:**

| Functionality | Status |
| --- | --- |
| Scheduled execution | V7 GitHub Actions only, on individually Jim-approved schedules |
| Daily Report create/update/render, including the Daily Command Center | V7 only (the Daily Command Center is V7's `dcc-card` stage, D175) |
| Daily Report regions: Jira, Calendar, Bills, Attention, Amazon Orders, Hiring Pipeline projection | V7 only (region owners in `lifeos/platform/router.py` `OWNERS`) |
| Jira sprint rollover (weekly) and daily reconciliation, boards JFM and MEJ only | V7 only; approved by v2.15.18 (LIFE-915); ACTIVE since the LIFE-918 activation (November 25, 2026, 2:30 PM CT): daily reconciliation delivered, weekly rollover natural-boundary acceptance pending (LIFE-1028); see `V7 JIRA SPRINT ROLLOVER AND DAILY RECONCILIATION` |
| JFM and MEJ quarterly metric promotion (legacy to v2) | V7 only; approved by v2.15.19 (LIFE-1026), DISABLED — NOT DELIVERED; see `V7 QUARTERLY METRIC PROMOTION` |
| Recruiters region | Not asserted here: V7's `recruiters` stage writes the Recruiters data source, but the region's owner is not recorded in `OWNERS` |
| Run stamp, audit history | DISABLED — NOT DELIVERED (the single run-stamp line is approved for V7, not yet built) |
| Drive Index, Weekly Reports Archive, Notes lifecycle, Weekly Review, Jim & Matt accountability sync (the retired legacy dashboard sync, not the V7 V3 page writer) | DISABLED — NOT DELIVERED |
| Coverage & System Health reporting | DISABLED — NOT DELIVERED |
| Hourly Gmail/Outlook mail acquisition, census, and routing | DISABLED — NOT DELIVERED |
| Newsletter acquisition and cleanup orchestration | DISABLED — NOT DELIVERED |
| Interview Advisor work | DISABLED — NOT DELIVERED |
| US Remote and Newsletter terminal browser-evidence recovery | DISABLED — NOT DELIVERED |
| Continuity transition-input submission | DISABLED — NOT DELIVERED |
| Mail Alerts region | V7 only, as granted below |
| Interview OS pages | V7 only, as granted below; dry run until Jim sets `INTERVIEW_OS_WRITER=v7` |
| Interview Canon writer (`scripts/interview_canon_runtime.py`) | RETIRED — write gate `INTERVIEW_CANON_WRITE_AUTHORIZED: false` preserved |
| Followup lane (Action Inbox sixth feed) | DISABLED — NOT DELIVERED until natural-run acceptance; then V7 only, as granted in `FOLLOWUP LANE OWNERSHIP OVERRIDE` |

- **What "NOT DELIVERED" means here.** This contract records a function as delivered only when it is implemented **and** verified in V7 and Jim has recorded that here. V7 may already contain code, region ownership (`lifeos/platform/router.py` `OWNERS`), or workflows for some of these areas (for example Jira, Calendar, Bills, Amazon, Attention, Hiring Pipeline, the Daily Command Center card, or `lifeos/interview/advisor*`); V7's own decisions and tests govern that code. Until Jim records verified delivery in this table, this contract does not assert it, and nothing in this file may be read as saying the function works or is scheduled.
- Any other formerly ChatGPT-owned function not listed above is likewise DISABLED — NOT DELIVERED until it is implemented and verified in V7 and Jim records it here. A consumer that needs such output reports it as `NOT DELIVERED`; it never infers delivery from silence, never falls back to the retired executor, and never presents an empty state as trustworthy.
- This section creates no scheduler, workflow family, runtime, datastore, or PR-side execution path. The V7 schedule is unchanged. Only Jim's explicit current instruction may change anything recorded here.

## MAIL ALERTS OWNERSHIP OVERRIDE — October 5, 2026

- **V7 is the sole live owner and writer of the Daily Report `Mail Alerts` region.**
- `LIFE OS Daily Runs` must not acquire Physical Mail source data, query Gmail/Outlook for Mail Alerts, compute Mail Alerts state, or mutate/render the `Mail Alerts` callout. The prior unread-`anytimemailbox.com` acquisition/rendering rule is retired.
- V7 owns Physical Mail acquisition, deterministic lifecycle/state, persistence, presentation, and authoritative read-back through the existing approved V7 scheduled tick + `domains.yml` execution path. This creates no new scheduler, workflow family, orchestration layer, or duplicate datastore.
- `LIFE OS Daily Runs` is retired and does not read this state. Any consumer of the latest accepted V7-owned Mail Alerts state may not refresh or repair the region itself.
- Missing, stale, partial, failed, or unaccepted V7 Mail Alerts evidence is `DEGRADED` for any consuming ChatGPT presentation. Do not fall back to the retired unread-mail query and do not present a trustworthy empty state.
- Physical Mail remains excluded from Attention. No second writer is permitted.
- Runtime target: the existing Daily Report callout headed exactly `Mail Alerts`; V7 addresses it through its configured `MAIL_ALERTS_CARD_BLOCK_ID`.

## INTERVIEW CANON OWNERSHIP OVERRIDE — October 20, 2026 CT

- **V7 is the sole live owner and writer of the Hiring Pipeline Interview OS pages** (opportunity parents and interview children) at the canonical root, Hiring Pipeline hub `3d13c5a0-5926-81f0-abb0-f8e277cb58b9`. Jim authorized the single-writer transition on October 20, 2026 CT (LIFE-88, PR #408 in `jimmarkunas/life-os-v7`).
- The prior Interview Canon writer (`scripts/interview_canon_runtime.py` and `interview_canon/runtime.py`) has **no write authority**. Its production gate reads the marker below from this file and refuses every apply while the marker is `false`. Nothing (including the retired `LIFE OS Daily Runs`) may run it in `qa-apply` or `apply` mode against the canonical root, must not assemble its input payload, and must not create, update, reorder or normalize any Interview OS page. The Smith/Willie layouts and the creation gates in `docs/interview-canon-production-contract.md` are superseded by the approved UI 3c / UI 4 pages and V7's identity and progression rules.
- V7 owns the evidence, identity, page creation and in-place refresh, and the authoritative read-back, through its existing hourly tick and `domains.yml` execution path (`lifeos.run interview`). This creates no new scheduler, workflow family, orchestration layer, or datastore. Each side stays the sole scheduler in its own scope, as the October 2026 scheduler-scope rule already records.
- `LIFE OS Daily Runs` is retired and does not read Interview OS pages. Missing, stale, partial, failed or unaccepted V7 Interview OS output is `DEGRADED` for any consumer; it never falls back to the retired writer or the retired executor.
- **Activation order (one writer at a time):** (1) this contract is merged on `life-os-automation/main`, which retires the old writer; (2) only then Jim sets the V7 repository variable `INTERVIEW_OS_WRITER=v7`. Until both have happened V7 runs the stage as a dry run and writes nothing. Jim alone may reverse the order of ownership, and only by editing this section.
- Jim-authored content stays Jim's: Raw Notes, Live Notes, Jim's Notes and every freeform section are never rewritten by either side.

## FOLLOWUP LANE OWNERSHIP OVERRIDE — November 16, 2026 CT

- **V7 is the sole owner and writer of the Followup derived state** that feeds the Action Inbox: open loops (waiting on someone, deliverable owed, post-interaction value-add, short-lived outcome thread), their derived states (Needs Action Today, Waiting/Aging, Upcoming, with Review Needed kept apart), checkpoints and suppression fingerprints. Jim authorized the lane on November 16, 2026 (LIFE-737; decision D258 in `jimmarkunas/life-os-v7`). `LIFE OS Daily Runs` is retired and does not read, write or repair this state.
- **Execution path.** One `followup` stage inside V7's existing hourly tick and `domains.yml` continuation (`lifeos.run followup`). This creates no new scheduler, workflow family, cron, watchdog, orchestration layer, or datastore beyond the table family named below. Only Jim may reverse this ownership, and only by editing this section.
- **Cadence.** Every hourly tick runs one full reconciliation of every approved source: each source is read from its last accepted checkpoint, every open thread is re-derived, and the result is written and read back in the same run. There is no separate delta-only run and no hour-specific full run; every tick is the same pass. Throttling or staggering jobs to save time is not part of this section and needs its own decision.
- **Sources (read-only, approved adapters only).** Gmail/Outlook delta, Calendar, Jira, Todoist, Notion source references and Interview OS context, each through the existing secrets broker and its own per-client grant. Asana is not read. Personal and Bytalos grants, identities, records and caches are separate; TGP is excluded. This section grants no new credential, OAuth scope or provider access; each grant is a separate step that needs Jim's explicit approval.
- **State and the single mutation owner.** The one authorized datastore is the `v7_followup_*` table family (threads, source checkpoints, overrides, suppression fingerprints) in the existing V7 MySQL store, approved by Jim on November 16, 2026. The `accept()` write path in `lifeos/inbox/followup_store.py` is the only writer: it validates the whole batch, writes it in one transaction, reads it back and compares before committing. A Jim checkpoint override lives in its own table and never edits a source's due date. Creating the tables in production is a separate step that needs Jim's go.
- **Freshness and DEGRADED are component-local.** A failed, missing, stale, denied, truncated or unaccepted source makes Followup `DEGRADED` with a "last accepted" time in America/Chicago, keeps the last accepted threads, and does not advance that source's checkpoint. A failed write or read-back keeps the known-good state. Followup never presents a trustworthy empty state or `0`, and an unrelated component keeps rendering when Followup is degraded. Strong proof that an outcome ended closes a thread and the same evidence cannot reopen it; an intermediate Jira or Todoist task, a chase email or an ended Calendar block never closes one.
- **Where it renders.** One component-state payload (PASS or DEGRADED, counts, thread projections, a review list outside the counts, provenance) read by the Action Inbox. This grants no Daily Report region; the Daily Command Center may read the payload only as a consumer and may not refresh or repair it. No consumer falls back to the retired executor or reads a source itself.
- **Live acceptance.** The lane is DISABLED — NOT DELIVERED until one natural scheduled hourly run shows the slot stamp, the contract load, a bounded read, the reconciliation, the write, the read-back and the projection, and a second run creates no duplicate thread, with no unauthorized source scan. A manual run or a unit test is not acceptance. Until then the stage may run only as a dry run that writes nothing.
- **Activation order (one step at a time):** (1) this contract is merged on `life-os-automation/main`; (2) Jim approves creating the `v7_followup_*` tables; (3) Jim approves each source credential grant; (4) the `followup` workflow step is added to V7 with no new `permissions`; (5) the natural run above; (6) only then Jim records delivery in the status table. Followup actions (confirm resolution, update checkpoint) and every Followup OS 8.1 roadmap capability (act now, drafting, email send, Calendar proposals, Jira/Todoist promotion) stay NOT DELIVERED.

## NEWSLETTER + US REMOTE OPERATING OVERRIDE — September 23, 2026

- **Newsletter acquisition is active.**
- **US Remote web acquisition is active.**
- **Scale-Up acquisition is ACTIVE in V7 Phase 1.** Preserve its scheduled acquisition and provider-specific source/recovery mechanics, and keep its downstream identity/Fit/persistence/lifecycle on the shared Jobs core.
- **Skilled Worker remains QUARANTINED and is Phase 2.** Preserve known-good/reference state only until Jim explicitly promotes it.
- US Remote runs through the existing shared V3 Jobs path: `Acquire → SourceVacancyObservation → Identify/Dedupe → Persist/Read Back → Terminal Evidence → Reconcile/Read Back → LIFE OS Fit → Qualify → Present`.
- **US Remote terminal browser-evidence ownership:** when deterministic/provider evidence cannot establish trustworthy terminal vacancy evidence, **[DISABLED — NOT DELIVERED: `LIFE OS Daily Runs` is retired and no V7 browser-recovery executor exists; the following describes the retired design only.]** `LIFE OS Daily Runs` (ChatGPT/browser) owned the bounded current browser/provider recovery read and the approved private handoff through the `Current Recovery Evidence` Files property on the exact `US Remote` row in Job Lane Configuration. The `life-os-v2` Runtime owns validation/consumption of that handoff, deterministic terminal-evidence resolution, shared qualification, guarded canonical Job Ledger persistence, and authoritative read-back; Runtime never runs a browser. Browser recovery is exact-vacancy evidence only, not a second acquisition lane, datastore, scheduler, workflow family, or Jobs engine. Missing, stale, truncated, ambiguous, or uncorrelated browser evidence is `DEGRADED` and must not advance acceptance/checkpoints or fabricate terminal evidence. Where authoritative deterministic/provider evidence already exists, do not browser-refetch.
- **Newsletter terminal browser-evidence ownership:** when deterministic/provider evidence for an exact Newsletter vacancy is exhausted, **[DISABLED — NOT DELIVERED: retired design only.]** `LIFE OS Daily Runs` (ChatGPT/browser) owned one bounded current exact-vacancy recovery read and the approved private handoff through the existing `Current Recovery Evidence` channel used by US Remote. The `life-os-v2` Runtime owns exact-vacancy correlation, freshness validation, deterministic terminal-evidence resolution, shared qualification, guarded canonical Job Ledger persistence, authoritative read-back, and safe Gmail finalization; Runtime never runs a browser. Evidence must remain exact-vacancy scoped and must still satisfy employer/ATS or verified native-application terminal authority. Missing, stale, truncated, ambiguous, or uncorrelated recovery evidence is `DEGRADED` / REVIEW and blocks Gmail finalization for that source message. This does not create a second acquisition lane, datastore, scheduler, workflow family, crawler, or Jobs engine.
- Current shared Jobs qualification owns the professional Fit floor: **72**. Do not restore historical US Remote 78 / Target 68–77 scoring policy. Provider scores contribute zero LIFE OS Fit points.
- Safe canonical identity permits persistence before terminal enrichment/Fit.
- US Remote source or accounting failure is `DEGRADED`, never zero/no-change.
- The first production execution after this re-entry must use the existing full-recovery-sweep path and account for every enabled configured source.
- Scale-Up is an active lane with its own production-shaped acceptance and source accounting. Skilled Worker is not an active acceptance dependency.
- Newsletter safe-cleanup semantics remain unchanged.
- `LIFE OS Daily Runs` is retired and is not a scheduler. V7 GitHub Actions workflows are the sole scheduled LIFE OS execution infrastructure, each on an individually approved schedule. No second scheduler service, duplicate workflow family, datastore, watchdog, dispatcher, or orchestration layer is authorized.

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

- Daily Report: `https://app.notion.com/p/NOTION-PAGE-01`
- Dashboard: `https://app.notion.com/p/NOTION-PAGE-02`
- Daily Report Template / UI canon: `https://app.notion.com/p/NOTION-PAGE-03`
- J Search front door: `https://app.notion.com/p/NOTION-PAGE-04`
- Recruiters & Human Outreach: `collection://NOTION-COLLECTION-01`
- Attention: `collection://NOTION-COLLECTION-02`
- Hiring Pipeline context hub: `https://app.notion.com/p/NOTION-PAGE-05`
- US Remote Jobs: `https://app.notion.com/p/NOTION-PAGE-06`
- Job Boards / US Web (RETIRED — superseded by US Remote Jobs): `https://app.notion.com/p/NOTION-PAGE-07`
- Scale Up Jobs (ACTIVE): `https://app.notion.com/p/NOTION-PAGE-08`
- Skilled Worker Jobs: `https://app.notion.com/p/NOTION-PAGE-09`
- Canonical Job Ledger: `collection://NOTION-COLLECTION-03`
- Job Lane Configuration: `collection://NOTION-COLLECTION-04`
- Target Companies: `collection://NOTION-COLLECTION-05`
- Weekly Review Hub: `https://app.notion.com/p/NOTION-PAGE-10`
- Weekly Reviews: `collection://NOTION-COLLECTION-06`
- Weekly Reports Archive hub: `https://app.notion.com/p/NOTION-PAGE-11`
- Drive Index: `collection://NOTION-COLLECTION-07`
- Bill Tracker: `https://app.notion.com/p/NOTION-PAGE-12`
- Bill Tracker data source: `collection://NOTION-COLLECTION-08`
- Interview Archive: `https://app.notion.com/p/NOTION-PAGE-13`
- Amazon Orders database: `https://app.notion.com/p/NOTION-PAGE-14`
- Amazon Orders data source: `5a720a09-dfec-400e-b1dd-8e45a2a4fd4b`
- Accountability Dashboard: `https://app.notion.com/p/NOTION-PAGE-15`
- Saturn Protocol: `https://app.notion.com/p/NOTION-PAGE-16`

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

No other workflow file is authorized in this repository without a future approved architecture change. No GitHub workflow may contain a recurring `schedule`/`cron` in this repository (`life-os-automation`). V7's scheduled workflows live in `jimmarkunas/life-os-v7` and are outside this rule.

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

## Shared mail census and independent routing

**Status: retained component specification; scheduled census and routing are DISABLED — NOT DELIVERED.** The scripts and gates below stay in this repository, but the hourly branch that invoked them belonged to the retired `LIFE OS Daily Runs`. Nothing currently schedules a Gmail + Outlook census, writes `data/mail-routing-census-current.json`, or routes human-hiring mail, and no consumer may treat the absence of fresh routing evidence as "no mail". Run manually, these specifications still apply.

Any run of the mail census performs one provider-agnostic Gmail + Outlook census over the frozen current production window plus required safety overlap. Exhaust pagination/continuations. Missing/truncated/tool-error results are not zero evidence.

### Deterministic Gmail provider acquisition

Before semantic classification, the operator running the census writes one unique correlated heartbeat to `data/mail-acquisition-trigger.txt` (no scheduled producer exists). The trigger's leading timezone-aware ISO timestamp is the frozen acquisition-window end. The existing `life-os-runtime.yml` Gmail module runs `scripts/gmail_mail_acquisition.py` with a minimum 48-hour lookback and `in:anywhere`, exhausts every Gmail `nextPageToken`, preserves the immutable Gmail Message ID, and hydrates only the provider metadata required by the existing census boundary: mailbox, message ID, thread ID when available, sender, subject, and timezone-aware received time.

Gmail authorization uses OAuth 2.0 offline access with the required `https://www.googleapis.com/auth/gmail.modify` scope. Runtime receives only these repository secrets/environment variables:
- `GMAIL_OAUTH_CLIENT_ID`
- `GMAIL_OAUTH_CLIENT_SECRET`
- `GMAIL_OAUTH_REFRESH_TOKEN`

The refresh token remains in GitHub Actions Secrets. Each execution exchanges it for one short-lived access token kept only in process memory. Credentials and access tokens are never committed, written into the acquisition payload, or printed.

On PASS, Runtime uploads one metadata-only GitHub Actions artifact named `gmail-mail-acquisition-<github_run_id>-<github_run_attempt>` with one-day retention. The payload reuses the existing Gmail pre-classification census fields: exact trigger/query window, query, contiguous page evidence with immutable IDs and continuation tokens, observed count/ID universe, and metadata-hydrated observed messages. It contains no message body, raw MIME, classification, Newsletter projection, or product mutation. This artifact is bounded execution evidence, not a canonical datastore or second mail schema; `data/mail-routing-census-current.json` remains the sole canonical persisted shared census.

Retrieval of the exact correlated Gmail artifact, Outlook acquisition against the exact same frozen window, the semantic `automated_job_source` / `human_hiring` / `unrelated` judgment, and publication of the provider-neutral census/handoff contracts were performed by the retired ChatGPT task and are DISABLED — NOT DELIVERED; no V7 delivery of them is recorded here. Outlook direct app-only acquisition remains outside this implementation. Newsletter, Hiring, Amazon, Continuity, and downstream cleanup semantics are unchanged.

Missing credentials, token failure, incomplete pagination, duplicate/mismatched immutable IDs, metadata hydration failure, artifact failure, or exhausted provider retry is Gmail acquisition DEGRADED and leaves `mail_census` DEGRADED/RECOVERY REQUIRED. It is never interpreted as an empty mailbox. Provider acquisition failure does not restart downstream work that already owns its own retry boundary.

Canonical shared routing census:
`data/mail-routing-census-current.json`

For each mailbox persist:
- exact current trigger and frozen query window;
- pagination-complete observed immutable-message-ID universe and observed count;
- exactly one classification row for every observed ID with mailbox, immutable ID, sender, subject, timezone-aware `received_at`, one allowed classification, and concise routing reason.

Allowed top-level classifications:
- `automated_job_source`
- `human_hiring`
- `unrelated`

`classification_complete=true` only when classification IDs equal observed IDs exactly.

### Bounded classification persistence

For a classification ledger too large to persist inline, use deterministic `classification_chunks` instead of partial inline state.

Each chunk reference carries a safe relative path under `data/` and exact `row_count`. Each chunk carries schema version >=1, exact trigger, mailbox, contiguous 1-based `chunk_index`, common `chunk_count`, and complete classification rows. Preferred chunk size is <=50 rows.

Before `classification_complete=true`:
- every referenced chunk exists and is readable;
- no path escapes the data tree;
- trigger/mailbox values match;
- indexes are contiguous with no duplicates or gaps;
- declared row counts equal actual rows;
- IDs do not duplicate across chunks;
- reconstructed classification IDs equal `observed_message_ids`;
- each row contains all required metadata.

Inline rows and chunk references may not be mixed for the same mailbox.

### Human-hiring routing is independent

Every `human_hiring` message independently routes to Hiring Pipeline regardless of Newsletter status. Routing classes are:
- `direct_recruiter_outreach`
- `interview_or_scheduling`
- `application_status`
- `referral_or_human_intro`
- `hiring_manager_outreach`
- `staffing_outreach`

Persist exactly one durable handoff under `data/hiring-mail-handoff/` for each current `human_hiring` message, containing mailbox/message identity, thread ID when available, sender, subject, received time, routing class, company/role when proven, disposition `CREATE` / `UPDATE` / `NO_CHANGE` / `REVIEW`, source link when available, and concise reason.

`scripts/hiring_mail_routing_gate.py` is the canonical reconstruction/reconciliation gate. `data/hiring-mail-routing-current.json` may PASS only when the complete shared census is valid, every current human-hiring classification has exactly one valid handoff, no extra current handoffs exist, and all dispositions reconcile.

Publish/reuse the accepted shared routing census before downstream Newsletter or Hiring Mail acceptance so a later Newsletter failure cannot erase otherwise-valid routing evidence.

A Newsletter failure never suppresses or rolls back a valid human-hiring update. Hiring uncertainty never weakens Newsletter source/manifest/accounting requirements. Human-hiring mail is never mutated by Newsletter cleanup.

### Dice Private Email exception

The normal Dice automated-provider backstop remains. A Dice message may bypass it only when:
1. sender is a per-user relay containing `@user.dice.com`; and
2. subject contains ` is shared with you`.

Only then is it eligible for `human_hiring`. Ordinary alerts such as `dice@connect.dice.com` remain automated. Never infer recruiter/company/role/stage/action from the exception; use REVIEW when facts are ambiguous.

## Newsletter / monitored-email acquisition

Production persistence is the canonical Job Ledger through shared `jobs/*`.

### Current source proof

Current Newsletter enumeration is schema v4 or newer and uses ordinary rolling continuity. Historical schema-v3 bootstrap and one-time `repair_rebaseline` migration paths are closed and must not be reused unless a future live Production Contract explicitly reauthorizes them.

For every mail census run while Newsletter is enabled:
1. perform provider-agnostic Gmail + Outlook mailbox-wide immutable-ID census over the branch window plus at least the required 48-hour safety lookback;
2. Gmail census includes archived mail (`in:anywhere` or equivalent); Outlook includes relevant Deleted Items inside the overlap;
3. exhaust all pages/continuations and persist page-by-page census evidence in connector order;
4. page numbers are contiguous from 1; non-final pages retain continuation evidence; the final continuation is empty; page union equals `observed_message_ids`; duplicates are invalid; `observed_count` reconciles;
5. persist `observed_messages` with exactly one timezone-aware `received_at` per observed ID, no extras/duplicates, all timestamps inside the frozen query window;
6. compare with the immediately prior accepted schema-v4+ census and require every prior message whose `received_at` remains in the current window to remain in the current observed universe;
7. classify every current observed ID exactly once;
8. derive `included_message_ids` mechanically as exactly the `automated_job_source` subset;
9. require the source manifest to equal that derived included set exactly;
10. require every manifest ID to be checkpoint-proven or backed by one valid deterministic handoff;
11. preserve exact provider-family accounting, complete vacancy projection/extraction, candidate accounting, qualification, persistence, and exact-trigger acceptance.

Known-provider tokens may detect obvious post-census misclassification for recurring automated families such as Lensa, Jobright, LinkedIn Jobs, and Dice, but provider names may never define the search universe.

A returned mailbox ID may not silently disappear between connector page evidence, observed universe, classification, inclusion, manifest, handoff/checkpoint, and ingestion.

### Bounded metadata hydration

The broad immutable-ID census is the observed-universe proof boundary. If a full-message response is truncated/incomplete or cannot safely provide sender, subject, and timezone-aware `received_at` for every observed ID:
1. re-read the same frozen window in deterministic non-overlapping slices;
2. preferred initial width is six hours;
3. recursively bisect only an oversized/incomplete slice until metadata is complete or the recovery budget is exhausted;
4. reconcile hydrated rows by exact immutable IDs back to the already-proven observed universe.

Every observed ID must hydrate exactly once; no extra/duplicate IDs; every timestamp remains inside the same frozen window. Missing/ambiguous metadata is DEGRADED and is never guessed.

### Oversized automated-source recovery

For oversized Lensa/LinkedIn-style sources, use this bounded order:
1. complete normal body;
2. complete raw MIME/full-message representation when available;
3. deterministic HTML/card extraction;
4. provider-specific deterministic adapter;
5. terminal DEGRADED only after the recovery budget is exhausted.

When complete raw-message recovery is used, preserve `capture_mode=complete_raw_message`, complete raw MIME/source evidence when available, and use the deterministic oversized-source projection path (`scripts/newsletter_oversized_projection.py` plus the ingestion gate) to prove provider terminal evidence and the complete primary-card universe.

A valid `complete_primary_vacancy_projection` requires positive integer `source_card_count`, `source_card_count == len(vacancies)`, non-empty company and role for every vacancy, exact source identity, and complete validated source evidence. Never accept a partial first-N card universe.

#### Raw-message recovery handoff persistence boundary

The large RFC822 payload for oversized Gmail Newsletter recovery must never cross the ChatGPT connector boundary (the connector response can truncate it). Orchestration and exact immutable source identity/manifest selection formerly belonged to the retired `LIFE OS Daily Runs` and are DISABLED — NOT DELIVERED. The existing GitHub Platform Runtime owns complete Gmail raw-message transport for exact Newsletter recovery, using the already-approved Gmail OAuth credentials (`GMAIL_OAUTH_CLIENT_ID`/`GMAIL_OAUTH_CLIENT_SECRET`/`GMAIL_OAUTH_REFRESH_TOKEN`) already used by `scripts/gmail_mail_acquisition.py`. There is no second Gmail provider implementation; do not add one.

When `scripts/newsletter_ingestion_gate.py --runtime` finds a canonical Newsletter handoff whose normal body-parse yields zero vacancy candidates, for that SAME exact immutable Gmail message ID:

1. Runtime fetches the complete RFC822 source via Gmail `format=raw` (`scripts/gmail_mail_acquisition.get_message_raw`), reusing the existing OAuth/retry boundary.
2. Raw MIME exists only transiently, in Runtime process memory, as input to `scripts/newsletter_oversized_projection.py`. Runtime must never persist raw MIME to Git, GitHub Actions artifacts, Notion, or any other datastore.
3. `scripts/newsletter_oversized_projection.py` remains the deterministic raw→projection owner; nothing else may manually construct `complete_primary_vacancy_projection`, `source_card_count`, or vacancy rows. Never accept a partial first-N card universe and never fabricate vacancy URLs.
4. Only after a valid `complete_primary_vacancy_projection` is produced does Runtime overwrite the SAME canonical handoff file at `data/newsletter-mailbox-handoff/<mailbox>-<message_id>.json` — never a new or parallel handoff path — preserving canonical source identity exactly (mailbox, message_id, sender, subject, received_at) and persisting the existing deterministic source hash/evidence (`oversized_recovery.source_sha256`), not raw MIME.
5. Recovery is atomic at the handoff-set boundary: if any required message in the current manifest fails raw recovery, no handoff in that set is written and previous canonical handoffs remain unchanged.
6. Failed recovery blocks that exact Newsletter source/run from PASS: no Job Ledger persistence, no Gmail cleanup, and no Newsletter continuity/watermark advancement occur. Newsletter remains DEGRADED, not silently skipped.

When a Newsletter vacancy has valid source identity but lacks sufficient terminal employer/ATS or verified native-application evidence, the vacancy and its containing source message are `PENDING_RECOVERY`, not a completed `DEGRADED` result. The bounded external recovery handoff (consuming the safe runtime recovery manifest and writing exact correlated evidence through the existing `Current Recovery Evidence` surface) belonged to the retired `LIFE OS Daily Runs` and is DISABLED — NOT DELIVERED; with no recovery executor the message stays `PENDING_RECOVERY` and is never finalized. The message remains queued until every vacancy in that message reaches a terminal disposition. Operational failures in recovery, evidence integrity, persistence, or finalization remain `DEGRADED` with a specific diagnostic.

Newsletter state precedence is deterministic: an operational error with a machine-readable code takes precedence over `PENDING_RECOVERY`, and `PENDING_RECOVERY` takes precedence over `PASS`. A run may therefore be `DEGRADED` while retaining a non-empty pending-recovery manifest. Every degraded Newsletter result must expose one or more specific safe error codes; aggregate `DEGRADED` is never the sole diagnosis. Missing terminal evidence without an operational failure remains `PENDING_RECOVERY`.
7. Never hardcode production message IDs or providers into permanent runtime behavior. No second scheduler/workflow/datastore.

### Atomic source publication and acceptance

Enumeration, required new/changed handoffs, manifest, and the Newsletter trigger must be published atomically under `docs/newsletter-atomic-source-publication.md`. Do not advance a trigger separately from its required source evidence.

Newsletter PASS requires exact-trigger ingestion acceptance and successful canonical Job Ledger persistence/accounting for every accepted mutation required by that trigger. Incomplete census, classification, continuity, manifest, handoff, projection, accounting, qualification, persistence, or correlation remains Newsletter DEGRADED and preserves known-good state.

### Post-PASS mailbox cleanup

Only after exact-trigger reconciled Newsletter PASS and accepted Job Ledger persistence:

Gmail — for the exact accepted automated source IDs only:
- add existing label `J Newsletters`;
- remove `INBOX`;
- remove `UNREAD`;
- if already in TRASH, do not untrash automatically;
- verify each accepted accessible message has `J Newsletters` and lacks `INBOX` / `UNREAD`;
- retry only failed IDs within budget.

Outlook — for the exact accepted automated source IDs only:
- re-verify automated-source identity;
- move to Deleted Items using normal Delete behavior;
- verify the exact message is no longer in the source Inbox/current location and is in Deleted Items when exposed.

Direct recruiter, hiring-manager, staffing, referral, interview, application-status, and other human-hiring mail is excluded from all Newsletter cleanup. New messages after the accepted source cutoff remain untouched until a later run accepts them.

Accepted ingestion remains durable if cleanup later fails, but the current branch is DEGRADED with the cleanup blocker.

#### Gmail retained Newsletter cleanup

Accepted `J Newsletters` messages are retained, not immediately deleted. When a Newsletter run executes (no scheduled executor currently does):

- retention age uses only the authoritative Gmail received/internal message timestamp for that exact message; Job Ledger dates, First Surfaced, Newsletter ingestion date, crawl time, and run time are never substitutes for Gmail message age;
- identify exact Gmail Message IDs that are labeled `J Newsletters`, are at least 60 days old by that authoritative timestamp, and are not already in Trash — a query equivalent to `label:"J Newsletters" older_than:60d -in:trash`, or an equivalent immutable-ID plus timestamp implementation, over that exact narrow source set only; never broaden the search to arbitrary older Gmail messages;
- for each exact eligible ID: move it to Trash (never a permanent/hard delete); re-read/verify the same exact ID is now in Trash; leave an already-trashed message untouched; do not remove the `J Newsletters` label before trashing;
- a message never accepted/filed under `J Newsletters` is never eligible, regardless of how it looks;
- direct recruiter, hiring-manager, staffing, referral, interview, application-status, and other human-hiring mail remains permanently excluded from this and all Newsletter cleanup;
- a failure to trash one eligible message must never trigger speculative trashing of unrelated messages; retry only the exact failed Message ID(s) within the existing Gmail reliability budget;
- if a Gmail trash action remains unavailable after that retry budget, report the exact Gmail dependency/action failure truthfully rather than a fabricated PASS.

This 60-day retention obligation is mailbox maintenance, not a Newsletter ingestion acceptance gate: it never becomes part of exact-trigger current Newsletter PASS. A message that fails to trash at 61+ days old does not retroactively invalidate the Job Ledger persistence already accepted when that message was originally filed.

### Exact-trigger September 9 shared-routing recovery — NARROW AUTHORITY

For this exact historical trigger only:

`2026-09-09T01:00:00-05:00|life-os-hourly-mail|attempt=1`

Canonical affected census:
`data/mail-routing-census-current.json`

Authorized recovery evidence:
- path `data/newsletter-source-enumeration.json`
- exact Git blob SHA `890db8e2e805861bfabc9d878677c724cb486b23`

The historical producer generated 1 AM classification chunks but staged the midnight chunk path, leaving the committed 1 AM census with references whose files were absent.

`scripts/hiring_mail_routing_gate.py` may reconstruct the classification ledger from the exact recovery evidence **only when every classification chunk referenced by both Gmail and Outlook for this exact affected census is absent**.

Recovery fails closed unless:
- the recovery file computes to the exact blob SHA above;
- trigger and query-window start/end exactly match the affected census;
- Gmail/Outlook observed counts match;
- each mailbox's ordered observed immutable-ID list matches;
- each mailbox's ordered classification-ID list equals that same observed-ID list;
- normal shared-census validation passes after expansion;
- Hiring Mail reconciliation independently passes.

Partial chunk presence, malformed references, later triggers, changed recovery evidence, count/ID/order/window mismatch, or routing mismatch remain DEGRADED.

This reconstructs historical classification proof only. It does not authorize mailbox cleanup, new Job Ledger writes, retroactive human-hiring mutation, or fabricated continuity PASS. Once continuity legitimately advances beyond this trigger, normal chunk-first behavior remains the production default.

## Daily Report — RETIRED RENDERING AUTHORITY

**Status: V7 only (the Daily Command Center is V7's `dcc-card` stage, D175); the run stamp and audit history are DISABLED — NOT DELIVERED.** This contract no longer defines how the Daily Report is built, rendered, refreshed, or verified; V7's decisions do. The command-center layout, hourly presentation render, decision-first hierarchy, freshness and component-trust rules, relative-date wording, bounded degraded diagnostics, Daily Jobs List navigation checks, linked-table fit checks, midnight Newsletter status/delta, 12 PM / 6 PM change markers, and audit-history disclosure were all procedures of the retired `LIFE OS Daily Runs` and are void. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

Still binding on any actor:
- **Region ownership is exclusive.** A Daily Report region has one owner. This contract grants V7 the `Mail Alerts` region (see `MAIL ALERTS OWNERSHIP OVERRIDE`) and the Interview OS pages (see `INTERVIEW CANON OWNERSHIP OVERRIDE`) and recognizes the other V7-owned regions listed in `OWNERS`. It grants no region to the retired `LIFE OS Daily Runs` or to any other actor.
- **Protected weekly Notes.** The book-icon Notes callout is Jim's. No executor under this contract may write, reset, archive, or restructure it. Any actor that writes to the Daily Report must fetch the complete page first, uniquely identify the Notes callout, preserve it verbatim, and stop (DEGRADED) rather than perform a broad rewrite if it cannot.
- **Trust is never faked.** A failed, missing, stale, or uncorrelated component is shown as unavailable or degraded, never as `0`, `none`, or an empty list, and a healthy page-level timestamp never upgrades a stale component. Relative-date wording (`today`, `tomorrow`, `overdue`) is computed at write time from absolute dates in America/Chicago and never carried forward.
- **No backend diagnostics in the human cockpit.** Provider errors, trigger tokens, workflow IDs, and continuity internals stay in backend evidence.

## Persistent Recruiters & Human Outreach

**Status: ownership not asserted here** (V7's `recruiters` stage writes the Recruiters data source, but the Daily Report region's owner is not recorded in `OWNERS`); the weekly-persistent maintenance performed by the retired `LIFE OS Daily Runs` is void. Canonical source: `collection://NOTION-COLLECTION-01`. Durable rules that survive for any actor: only direct human recruiter, staffing, hiring-manager, referral, or comparable person-to-person hiring outreach belongs here (automated alerts do not; human outreach is Saturn-exempt); `Done` is Jim's state and is never auto-cleared; resolve by the natural key in the global invariants before creating, and if absence cannot be proven do not create a possible duplicate. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Persistent Attention

**Status: V7 only** for the Daily Report Attention region (V7's `attention` stage); the weekly maintenance and Monday carry-forward procedure was the retired `LIFE OS Daily Runs`' and is void. Canonical source: `collection://NOTION-COLLECTION-02`. Durable rules: Attention is an exception queue, not a second Jira/task list; `Done` is Jim's state; resolve by Week Ending + Category + normalized Item and never create a possible duplicate; Physical Mail is owned by Mail Alerts and is never admitted to Attention (Jim, October 4, 2026). The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Calendar

**Status: V7 only** (V7's `agenda` card); the retired `LIFE OS Daily Runs` rendering is void. Google Calendar remains the sole fixed-time authority and the Calendar region is read-only for every actor governed here. Never fabricate event state. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Bills: This Week

**Status: V7 only** for the Daily Report Bills region (V7's `bills` card); the summary, correlated Job + Bill snapshot trigger, and Paid processor were driven by the retired `LIFE OS Daily Runs` and are void. Canonical Bill Tracker: `collection://NOTION-COLLECTION-08`. Durable rules: missing/stale/mismatched/incomplete Finance evidence is DEGRADED and never a zero or empty summary; an unset amount is never silently $0; unattended work must not use metered MCP Notion rows/SQL as a substitute for Public API snapshots. The Runtime's Notion snapshot scripts and the Paid Bills snapshot module (`data/notion-bill-paid-snapshot-trigger.txt`) remain in this repository and have no scheduled producer; Paid-row transitions, `stale_due` rollover, and Bill Tracker mutation are not performed by anything governed here. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Mail Alerts

The Daily Report `Mail Alerts` region is owned exclusively by V7. `LIFE OS Daily Runs` is retired and is not a source reader or writer for this region.

Routing destinations (the routing itself is DISABLED — NOT DELIVERED outside V7's Mail Alerts):
- direct human hiring outreach → Recruiters;
- accepted hiring-stage evidence → Hiring Pipeline;
- actionable/risk notices → Attention;
- job alerts/newsletters → job acquisition;
- Physical Mail → V7-owned Mail Alerts.

The former rule requiring `LIFE OS Daily Runs` to query unread `anytimemailbox.com` messages and render one bullet per unread message is retired. V7 owns the accepted Physical Mail source read, deterministic state, persistence, rendering, and read-back.

Until V7 has accepted fresh evidence, prior Mail Alerts content may remain visible only as last accepted state. No consumer may independently reacquire the source or mutate the callout. Missing, stale, partial, failed, or unaccepted V7 evidence is DEGRADED and may not be replaced with “none”, zero, or a fallback unread-mail scan.

## Hiring Pipeline

**Status: the Daily Report Hiring Pipeline region is V7 only (V7's `hiring` card).** The retired `LIFE OS Daily Runs` projection procedure (stage normalization, promotion rules, pre-rebuild Hiring Mail gate, stale-projection check, and relative-date handling) is void. Interview OS page ownership is as granted in `INTERVIEW CANON OWNERSHIP OVERRIDE`. Durable rules for any actor: the projection is a current-state view, not a second status database; cold-lead status comes from the canonical Job Ledger Applied/Application lifecycle; human-led status comes from accepted Gmail/Outlook evidence plus Google Calendar; Notion opportunity/interview pages own context and notes, never hiring status; a staffing partner is context on the end-client opportunity, not a duplicate opportunity; an interview date earlier than today is never shown as scheduled or upcoming. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

### Interview information architecture

Hiring Pipeline is the only interview-related Daily Report surface (ownership as stated above). Do not create a separate Interview Brief/Prep/Cockpit/Post-Interview panel.

Resolve the unique opportunity parent by Company + Role. On one unique parent, the Source cell contains one link labeled `Opportunity Notes`. If not uniquely resolved, show `Needs Notes`; never guess a URL. Do not render a separate `Interview Notes · Opportunity Notes` pair in the Daily Report.

Real interview rounds remain child pages under the opportunity parent; canonical user-authored notes remain there. Interview children follow the established `Interview <N>: <Interviewer> (M/D/YY)` convention when the evidence supports those fields.

Interview Archive is a company-level reference/index with exactly one row per company. It may reference existing opportunity/interview pages for browsing, but it never moves or duplicates canonical notes and has no unattended row-maintenance authority unless separately approved.

### Interview Canon writer

`INTERVIEW_CANON_WRITE_AUTHORIZED: false`

**RETIRED October 20, 2026 CT.** Write ownership moved to V7; see `INTERVIEW CANON OWNERSHIP OVERRIDE — October 20, 2026 CT` above. The old implementation (`interview_canon/runtime.py`, via `scripts/interview_canon_runtime.py`) stays in this repository as history and as a `plan`-mode reader only. It checks the marker above before any apply, so it can not write while the marker is `false`.

The accepted live baseline pages (`SH/FT — Technical Program Manager (Contract)` with child `Interview 1: JP Fellenberg (9/10/26)`, and the other Hiring Pipeline pages) are preserved as they are. V7 does not adopt, normalize or rewrite a page that is not its own machine page, unless Jim adds the explicit adoption line. A replay of unchanged accepted evidence writes nothing.

Interview Canon, on either side, may not send/mutate email, mutate Calendar, mutate Jira, mutate Job Ledger hiring status, perform unattended external-web enrichment, or rewrite Jim-authored Raw Notes/freeform sections.

## Amazon Orders

`AMAZON_ORDER_INGESTION_AUTHORIZED: true`

Canonical supporting authority: `docs/amazon-orders-production-contract.md`  
Implementation: `amazon_orders/runtime.py` (semantic domain implementation) via the thin `scripts/amazon_order_runtime.py` / `scripts/amazon_order_production.py` entrypoints, + `scripts/amazon_gmail_routing_gate.py`

**Status: the ingestion below is retained specification; its scheduled invoker was the retired `LIFE OS Daily Runs` and no scheduled executor runs it now (DISABLED — NOT DELIVERED).** The authorization marker above gates the repository runtime only and does not mean anything schedules it.

For each accepted Amazon-flow candidate:
1. read actual Gmail source evidence; do not infer order identity from subject alone;
2. extract only supported bounded fields;
3. require exact Amazon order identity and deterministic lifecycle/event validation;
4. resolve the canonical production row by exact Order ID;
5. duplicate rows, ambiguous identity, lifecycle regression/conflict, conflicting canonical values, incomplete evidence, or tool failure are DEGRADED;
6. create/update only when desired normalized state differs;
7. re-fetch and verify the exact production row before Gmail mutation;
8. identical replay is zero-write.

Never persist raw email bodies, full delivery addresses, payment details, or tracking tokens.

After successful Notion persistence/read-back only, for the exact accepted source Gmail messages:
- add existing label `Amazon`;
- remove `INBOX` / archive;
- never create a new label;
- never Trash/delete;
- re-read and verify `Amazon` present, `INBOX` absent, `TRASH` absent.

Rejected/unsupported/ambiguous messages remain untouched.

The Daily Report `Amazon Orders` callout is the only report projection, and V7 alone renders it (V7's `amazon` card); the retired `LIFE OS Daily Runs` rendering is void. Show only active ORDERED/SHIPPED plus DELIVERED orders whose supported delivery event is the report date/today. Keep source-grounded and compact. It is not a second order store. When current accepted Amazon evidence proves no active or newly delivered order is relevant today, render one compact accepted-state line; missing, stale, partial, or failed Amazon evidence renders DEGRADED rather than a trustworthy empty state.

Accepted live baseline: order `114-8984667-0839421`, DELIVERED, Grand Total `$13.63`, with accepted ORDERED/SHIPPED/DELIVERED source identities and verified Gmail post-persistence routing.

Amazon Orders ↔ Finance classification/evidence mutation remains separately gated; Amazon ingestion does not authorize Finance/tax classification.

## Jira Execution, GTV, and Accountability

**Status: the Jira card is V7 only (V7's `jira` card). The Jim & Matt Accountability Dashboard sync is DISABLED — NOT DELIVERED: that is the retired legacy sync into the old Accountability Dashboard (`docs/accountability-dashboard-sync.md`, `scripts/accountability_dashboard_gate.py`), and it stays retired. It is separate from the active V7 V3 page writer: V7's own accountability stages, run by its existing `v3-pages.yml` workflow, write the Jim & Matt V3 page (weekly sprint views and the Quarterly Goals & Review regions, legacy figures official, v2 figures preview only). This contract records that writer as V7's; it gives it no new schedule or credential and does not revive the retired sync. Sprint rollover and daily reconciliation for JFM and MEJ are owned by V7, approved by v2.15.18 and ACTIVE since the LIFE-918 activation on November 25, 2026, 2:30 PM CT (see `V7 JIRA SPRINT ROLLOVER AND DAILY RECONCILIATION`; daily reconciliation delivered, weekly rollover natural-boundary acceptance pending). Quarterly metric promotion is approved by v2.15.19 and DISABLED — NOT DELIVERED (see `V7 QUARTERLY METRIC PROMOTION`).** Bucket presentation, This Week hierarchy, the Daily GTV Action, and the accountability sync were driven by the retired `LIFE OS Daily Runs`; rollover orchestration by that retired executor is void, and its replacement is the V7 workload below. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

Retained repository components (manual or separately-governed use only; nothing here schedules them): `.github/workflows/jira-report-snapshot.yml` (trigger `data/jira-snapshot-trigger.txt`, snapshot `data/jira-report-snapshot.json`), `.github/workflows/jira-sprint-rollover.yml` (trigger `data/jira-sprint-rollover-trigger.txt`, status `data/jira-sprint-rollover-status.json`), `scripts/accountability_dashboard_gate.py`, and `docs/accountability-dashboard-sync.md`. Their only producer, the retired `LIFE OS Daily Runs`, is gone, so they are dormant (they are not the V7 rollover and remain dormant), and a manual `workflow_dispatch` of that repository workflow remains dry-run only.

Durable rules for any actor: never mutate Jira planning data unattended (the one exception is the approved V7 workload below, for JFM and MEJ only); Jira evidence is accepted only when PASS, exactly correlated to its trigger, at most 30 minutes old, and structurally valid, and an older PASS is never current; missing or invalid Jira evidence is DEGRADED and never a false zero or zero-capacity state; Notion supplies GTV strategy/context only; the accountability dashboard preserves Weekly 5 Must-Wins, Archive & Reference, user-authored Epics toggles/order, protected links, and all unrelated user content.

### V7 JIRA SPRINT ROLLOVER AND DAILY RECONCILIATION — approved v2.15.18 (LIFE-915); activated under LIFE-918 (recorded v2.15.19)

Approved by Jim on November 24, 2026 (ChatGPT scoping session; LIFE-915 owner decisions 1–17 and the field matrix at `https://app.notion.com/p/NOTION-PAGE-17`). v2.15.18 recorded the approval only. v2.15.19 records the activation below; it changes no schedule.

- **Activation (recorded v2.15.19).** Activated by Jim's approval "918 activate" on November 25, 2026, 2:30 PM CT, for both boards as one unit, through V7 Runtime Configuration switch rows: `JIRA_WRITERS_VERIFIED` = yes, `MEJ_AUTOMATION_SINCE` = 2026-11-25T14:30:00-06:00, `MEJ_SPRINT_AUTOMATION` = on (V7 LIFE-918). The two parts have separate status:
  - **Daily reconciliation: ACTIVE and delivered.** First live run 45700362347 (November 25, 2026, 3:01 PM CT; both boards ok, 11 sprints renamed, none created); first natural daily run 45844720396 (November 26, 2026, 9:01 AM CT; both boards ok); 24-hour acceptance V7 LIFE-982.
  - **Weekly rollover: ACTIVE, natural-boundary acceptance pending.** The first natural Monday boundary after activation (Monday, November 30, 2026) is observed by V7 LIFE-1028. Until that observation passes, the weekly rollover is not recorded as delivered.
- **Owner and writer.** V7 owns weekly sprint rollover and daily reconciliation for the JFM board (5) and the MEJ board (38) only, and is the one writer for that work (`lifeos/jira/rollover.py` and the existing recurrence owner, one engine, no second writer). Every other Jira board, project, field and surface is unchanged.
- **Workflow and trigger.** No new workflow, cron or scheduler. The weekly rollover and the daily reconciliation are separate steps of the existing `jira` job of `domains.yml`. The weekly rollover keeps its existing boundaries unchanged: the existing scheduled `jira-rollover-scheduled` step, which waits for the sprint to end and the first hourly tick after Monday midnight America/Chicago, and the existing 7/14-day cadences. The daily reconciliation (`jira-reconcile-daily`) runs in that same existing job on the first hourly tick at or after 8:00 AM America/Chicago, once per Chicago calendar date, seven days a week. Neither step performs the other's work.
- **Time and daylight saving.** All boundaries are America/Chicago local time (never fixed UTC offsets): the 8:00 AM daily slot, the Monday boundary, and the Monday–Sunday sprint weeks (short ISO week-year names `PROJECT W{week} {year}`, for example `MEJ W49 2026`; Jim, November 25, 2026, replacing the earlier `PROJECT S{week} W{week} {year}`). Daylight-saving changes, year ends and ISO week 53 move with local time and are tested.
- **What daily reconciliation may do.** Repair sprint inventory (exactly eight contiguous future weekly sprints per board, set by the existing Runtime Configuration row `JIRA_FUTURE_SPRINTS`, default 8, V7 LIFE-998; extra existing future sprints are reported on the summary line and never deleted; rename active and future sprints in place only), recurrence and drift for configured or labeled recurring series. It never performs an early rollover, never renames or alters historical or completed work, and never closes a sprint before its end or before its carry-forward is read back.
- **Dependencies and freshness.** Reads the existing Jira provider access and the existing V7 hourly tick; the Jira inventory it acts on is read fresh in the same run. A stale, partial or ambiguous inventory is DEGRADED and makes no change.
- **Budgets and contention.** No new credential, permission, paid service, provider quota, store or datastore. It uses the existing Jira grant, the existing Jira job's time budget and bounded Jira calls; contention with the existing Jira card and snapshot steps is avoided by running in the same job after them, one writer per board.
- **Bounds, retries, checkpoint.** Bounded retries (one per failure boundary); every step is idempotent and inspects actual Jira state before resuming; a timeout, partial write or restart replans from read-back; a missed, late or duplicate run recovers on the next eligible tick without a second rollover or duplicate occurrence. The daily checkpoint is the existing private state row mechanism already used by the Jira rollover watch; it adds no store.
- **Receipts.** Complete, degraded and incomplete outcomes, with run ID, SHA and authoritative Jira read-back; the existing Daily Report gets one concise summary line and failure alerts are deduplicated phone pushes on the existing alert path (Jim, "918 push ok", November 25, 2026; no inbox credential is widened); diagnostics are private (counts and fixed codes only).
- **Natural-run evidence, pilot, UAT, rollback.** Both boards are validated by synthetic test and preview first; activation is simultaneous for both boards under LIFE-918, same-head CI on both date variants, preflight, a rollback matrix and the first live outcome read-back are required before the workload is recorded as delivered. Rollback is turning the workload's gate off (no schedule change) and reverting its PR.
- **Activation status.** v2.15.18 did not enable the workload; LIFE-918 activated it on November 25, 2026, 2:30 PM CT, as recorded above. Rollback stays as stated (switch rows off, PR revert); closed sprints and completed renames are not undone by rollback.
- **Retired executor and other authorities.** `LIFE OS Daily Runs` stays retired and disabled and acquires nothing from this workload. No additional scheduler, credential, permission, paid service or datastore is authorized. Matt's approval is not required for LIFE-915 only (Jim, November 24, 2026); he is informed after verified completion. All unrelated authorities, ownership and statuses in this contract are unchanged.

### V7 QUARTERLY METRIC PROMOTION (JFM and MEJ) — approved v2.15.19 (LIFE-1026); DISABLED — NOT DELIVERED

Approved by Jim on November 26, 2026 (ChatGPT; REQ-63 remediation decisions R01–R05 at `https://app.notion.com/p/NOTION-PAGE-18`, and the preparation decisions recorded on V7 LIFE-1026 and LIFE-983). This amendment records authority and timing only. It changes no schedule and enables nothing new.

- **What it decides.** Whether the corrected (v2) quarterly goal metric becomes official for JFM and MEJ together, replacing the legacy (v1) metric for the new quarter. Legacy stays official until a promotion succeeds, and legacy figures stay available for history.
- **Quarter calendar.** Quarters start on the first Wednesday of January, April, July and October, America/Chicago. Q4 2026 runs Wednesday, October 7, 2026 through Tuesday, January 5, 2027; Q1 2027 starts Wednesday, January 6, 2027. January 1–5, 2027 are Q4 2026: no promotion evaluation runs then.
- **Where it runs.** Inside the existing `jira-reconcile-daily` step only (once per Chicago date at or after 8:00 AM CT, see the section above). No new workflow, cron, scheduler, credential, permission, paid service or datastore. Its stability state uses the existing private state row mechanism of the Jira rollover watch.
- **When it may promote.** Evaluations start at the first existing daily reconciliation at or after Wednesday, January 6, 2027, 8:00 AM CT. Promotion requires two consecutive passing daily evaluations at least 24 hours apart, so the earliest promotion is Thursday, January 7, 2027, and only if that much time has actually elapsed. A failed evaluation, a missed daily evaluation (a Chicago date with no evaluation) or a material change in the evaluated inputs (frozen membership, counted keys or completion evidence) resets the count.
- **What must pass.** For both boards as one unit: an explicitly approved, frozen individual Task/Story membership list for the quarter (approved by December 31, 2026, 11:59 PM CT for Q1 2027; no attribution inherited from Jira parent or project Epic); 100% of eligible members counted; zero duplicate attribution; completion dates from Jira transition history; administrative cancellations excluded from numerator and denominator; manual delivery, automatic recurring closure, administrative cancellation and unknown provenance kept distinct; historical parity with legacy; at most one unexplained Task discrepancy per board per quarter. A missing, placeholder, ambiguous, duplicate or wrong-date input is a hard fail, and the tolerance never overrides a hard fail.
- **The only writes.** On success, the existing V7 one-row setter (LIFE-976) sets the Runtime Configuration row `V2_OFFICIAL` to on, with read-back. Before that can happen in production, the setter's write permission must pass the setter proof below. Nothing else is written.
- **Setter proof (approved Option A).** One separately authorized, manually started same-value update of the `V2_OFFICIAL` row: the request resends the row's current `off` value through the existing V7 integration. It passes only with provider success and an independent read-back showing `V2_OFFICIAL` still `off`, `Revision` unchanged and every unrelated property unchanged. `V2_OFFICIAL` stays `off` throughout; the proof never sets it on. The actual off-to-on state change is proved by synthetic tests only, never in production before promotion. The proof path is never invoked by a schedule. Inconclusive evidence or any drift is UNVERIFIED: stop, and the setter stays unproven. Option B (a live off-to-on-to-off write) is rejected.
- **Current configuration is not promotion.** Since the LIFE-918 activation (November 25, 2026), the Runtime Configuration row `V2_PROMOTION` is configured on and `V2_OFFICIAL` is off. `V2_PROMOTION` on only lets the evaluation code path run (today it returns "not due" or fails closed); it does not authorize or deliver official promotion. v2 is not official, official promotion is not delivered, and nothing may set `V2_OFFICIAL` to on until every condition in this section is met.
- **Failure and kill switch.** A failed evaluation keeps legacy official on both boards and sends one deduplicated alert naming the failing board (phone push on the existing alert path). A retry never promotes twice, writes twice or alerts twice. The existing Runtime Configuration row `V2_PROMOTION` set to off stops every evaluation.
- **Status.** DISABLED — NOT DELIVERED. It may promote only after V7 LIFE-1024, LIFE-1025 and LIFE-1026 are shipped and accepted, the setter proof above has passed, and the frozen membership is approved. Acceptance is V7 LIFE-983 (not before Thursday, January 7, 2027, 8:10 AM CT). Until then legacy is the only official metric.

## Centralized Job Engine

### Architectural boundary

Acquisition lanes determine how vacancies are discovered and may supply route-specific evidence. Shared `jobs/*` owns identity/dedupe, common qualification, persistence, lifecycle, Saturn, Applied handling, closure, and rendering.

Shared production components:
- `jobs/identity.py`
- `jobs/qualification.py`
- `jobs/canonical_engine.py`
- `jobs/notion_repository.py`
- `jobs/panel_config.py`
- `jobs/lane_adapter.py`
- `jobs/view_spec.py`
- `jobs/fit_scoring.py`
- `docs/job-fit-scoring-model.md`
- `config/job-engine.json`

Source absence never deletes a known-good job. Definitive vacancy closure/removal evidence is required.

### Notion query conservation and correlated job snapshot

Use the lowest-cost reliable Notion path:
1. exact fetch by known page/data-source/view ID;
2. saved view;
3. current-run GitHub Python / official Notion Public API snapshot;
4. metered MCP rows/SQL only as an exception.

An entitlement/quota failure is DEGRADED, never zero, and must not replace accepted lower-cost evidence.

For any run that may read/write Job Ledger state, write a unique trigger to `data/notion-job-snapshot-trigger.txt` and consume the Runtime-produced job snapshot. It is valid only when:
- schema >=1;
- status PASS;
- exact current trigger;
- generated_at <=30 minutes old;
- `row_count == unique_stable_job_keys` and duplicates empty;
- config proves exactly three canonical lanes: US Remote, Scale-Up, Skilled Worker, with Scale-Up enabled and Skilled Worker explicitly quarantined/disabled rather than treated as active acquisition; Newsletter and US Web are front-end acquisition paths for US Remote and must not appear as independent lanes in the Job Config;
- source IDs match canonical Job Ledger and Job Lane Configuration.

Snapshot is read-only. Invalid evidence degrades Jobs and preserves known-good state.

### Job configuration authority

`config/job-engine.json` owns immutable shared safety invariants/defaults. The Notion Job Lane Configuration panel owns approved lane controls only. Live panel values may override configured controls only through `jobs/panel_config.py` validation. Invalid, missing, or duplicate configuration is DEGRADED and authorizes no policy-changing write.

The current validated baseline keeps US Remote enabled/acquisition-enabled and keeps Scale-Up `enabled=true` and `acquisition_enabled=true` (Jim, V7 Phase 1), and keeps Skilled Worker explicitly `enabled=false`, `acquisition_enabled=false`, and `show_in_daily_report=false` while quarantined. Newsletter (Gmail/email acquisition) and US Web (public-web acquisition) are both front-end acquisition paths that write to the US Remote lane via `LANE_ALIASES` canonicalization. The canonical downstream lane is US Remote in both `config/job-engine.json` and the Job Lane Configuration Notion panel. Do not infer a lane disabled/enabled from stale history; validate current configuration for each run that depends on it.

### Stable Job Key and append-only ledger

Every distinct cold lead has one Stable Job Key. New-key preference:
1. existing Stable Job Key;
2. company + stable provider job ID;
3. canonicalized application URL stripped of common tracking parameters;
4. normalized company|role|location/work-mode fallback.

Never rewrite an existing production key merely to adopt a newer algorithm.

Current snapshot is the bulk exact-key index. Existing key → update known page. Accepted snapshot proves absence → create one row. Duplicate/ambiguous key or uncertain absence → DEGRADED; never auto-delete duplicates.

The Job Ledger is the sole persistence source for cold leads. Four job pages are filtered presentation views only.

### Qualification

Shared admission floor = 72%.

Target Company review floor = 68%; target candidates at 68–77 are Passed / Review, not admitted.

Newsletter/Curated exception: Target review band does not apply. Newsletter candidate below validated production fit floor is Excluded, never Passed / Review. Provider Score contributes zero. Re-run shared qualification whenever LIFE OS Fit changes.

Compensation:
- US: hard floor $80,000 annual equivalent only when pay is explicitly posted;
- Scale-up: no compensation floor;
- Skilled Worker Phase 2 policy: hard floor £65,000 only when explicitly posted;
- missing/undisclosed pay allowed.

Geography:
- Newsletter and US Web: US Remote only;
- Scale-up and future Skilled Worker Phase 2: London/Greater London, any work mode.

Freshness:
- Newsletter: vacancy-level when possible; no hard max-age; email receipt date never substitutes for Posting Date;
- US Web: new admission <=7 calendar days inclusive; unresolved age → Review;
- Scale-up: posting age informational for existing rows (V7 applies a 30-day new-admission age gate by Jim's decision of 2026-10-01; its LANES.md is the detailed record);
- Skilled Worker Phase 2 policy: new admission <=14 days; unresolved age → Review;
- age after admission does not remove a still-live row.

Visa routing:
- Scale-up page requires positive vacancy-specific Scale-up evidence.
- Future Skilled Worker Phase 2: verified current membership of the **actual employing legal entity** on the UK approved sponsor list is sufficient positive route evidence and sponsorship is assumed. Vacancy silence/negative wording does not override verified employer-list membership.
- An intermediary cannot use its own licence when actual client employer is unresolved.
- If both routes validate after Skilled Worker re-entry, Scale-up wins visible routing; ledger may preserve both.
- Otherwise-plausible London roles with unresolved employer/sponsor identity remain Review.

### Acquisition lanes

Newsletter uses the Newsletter section above and `docs/newsletter-job-discovery-contract.md`.

US Web uses `docs/us-remote-job-discovery-contract.md` + `config/us-remote-source-registry.json`, with the established guaranteed population of 30 Tier-1 employers + 10 staffing agencies + 3 discovery helpers, plus defensible broader gap discovery under the configured 200-new-general-web-candidate/day ceiling.

Scale-Up is ACTIVE in V7 Phase 1 (Jim; see the `NEWSLETTER + US REMOTE OPERATING OVERRIDE`). It acquires through V7's `web-scale-up` stage and other V7 sources and writes to the shared Job Ledger under the Scale-Up rules in this section. **Verification observed October 20–21, 2026 (not a standing acceptance gate):** a successful V7 hourly run executed `web-scale-up` for lane Scale-Up (2 due, 2 complete, 2 admitted, 0 failed), and the Job Ledger holds live Visible Lane Scale-up rows surfaced October 4–20 (including Admitted rows from Greenhouse sources) with Fit Authority "Authoritative" and current Updated At. Known quality gaps seen in the same sample: Public Web rows that are not vacancies (a remote-working marketing page; a visa-statistics article) reached Passed / Review, and some rows have an empty Role. **Coverage observed October 21, 2026:** the Ledger's Scale-up rows come from 10 companies, and only 8 of the registry's 36 listable boards have ever produced one (the other 28, and the 12 boards with no discoverable ATS, have none); "no row" can mean no role that passes Fit or a reader that is not working, and V7's per-board diagnostic (`diag` stage `scale-up-boards`) is the instrument that tells them apart. Jim's keep list names specific Revolut roles that must reach the board in Review whatever the model scores them (V7 D115, extended in D247). This contract records Scale-Up as running, with those gaps open; it does not certify the lane as defect-free.

Skilled Worker is quarantined — Phase 2. There is no current production acquisition, Runtime trigger/module, QA5/QA6 release prerequisite, source-driven lifecycle refresh, or current acceptance artifact requirement. Preserve existing known-good Job Ledger rows and the persistent Skilled Worker reference page. The approved Phase 2 direction is `LIFE OS Daily Runs → Google Cloud Run Jobs acquisition → non-canonical Google employer/acquisition index → existing LIFE OS Job Engine → existing Notion LIFE OS Job Ledger → existing Skilled Worker presentation surface`. Cloud Run is compute, not a scheduler; the Google Sheet is not a canonical job datastore or business-rule authority.

Target Companies retain metadata regardless of visible lane. US targets require US Remote; London targets any mode; active visa pages require positive route evidence.

### Visible lane precedence

Exactly one visible lane per newly admitted vacancy among active lanes. Current active-lane precedence is:
1. Newsletter
2. US Web

Scale-Up participates in visible routing as its own lane: a vacancy that validates for both UK routes is one job and Scale-Up is the visible lane, with both routes preserved in Eligible Lanes. Skilled Worker does not participate while quarantined; if it is re-enabled in a future authorized re-entry, the applicable precedence must be updated by that authority before activation.

While Skilled Worker is quarantined, it does not participate in new visible routing. Existing known-good Skilled Worker rows are not reclassified, closed, deleted, or expired merely because the lane is quarantined. A source/route improvement may move the same Stable Job Key upward only through an active accepted lane; never duplicate one key across visible pages.

### Saturn lifecycle and Applied

Cold one-sleep applies only to automated/newsletter, proactive web, Target Company, and active visa discovery. Direct human outreach is exempt.

- First Surfaced is immutable once set.
- Saturn Ready defaults to First Surfaced + 1 America/Chicago calendar day.
- Same-day before ready = Today.
- On ready date = Yesterday.
- After ready: blank decision remains Yesterday; unresolved Review/Apply becomes Sticky.
- Skip remains visible on decision day and later becomes Removed.
- Applied checkbox is sole canonical indication of real submission.
- When Applied is first checked and Applied On is empty, stamp once; preserve later manual edits.
- Applied rows become Lifecycle Application, leave cold-lead views, and feed Hiring Pipeline.
- Legacy `Saturn Decision=Submitted` is compatibility-only: bridge once to Applied and clear the legacy decision; never create a new Submitted decision.
- Definitive closure/removal becomes Expired/Removed; source absence alone never closes a row.

Age display:
- First Surfaced today → `Today`;
- one America/Chicago calendar day ago → `Yesterday`;
- two or more days ago → `>2 Days`.

### One-table job pages

Each production job page contains exactly one linked filtered Job Ledger table.

Default action-oriented filter for active US Web and Scale-Up, and a future re-enabled Skilled Worker:
- Visible Lane = page lane;
- Admission Status = Admitted;
- Applied = false;
- Liveness = Live OR Unknown;
- Lifecycle = Today OR Yesterday OR Sticky.

Newsletter persistent historical projection:
- Visible Lane = Newsletter;
- Admission Status = Admitted OR Passed / Review;
- LIFE OS Fit >= validated production floor;
- do not remove solely because lifecycle ages, Applied changes, or liveness changes;
- sort First Surfaced DESC, LIFE OS Fit DESC, Company ASC.

Standard visible job fields center on Applied, Today/>2 Days, Saturn Decision, LIFE OS Fit, Apply URL/Source, Role, Company, Location / Work Mode, Compensation, Posting Date, Freshness Status, Tailored Sentence, Why It Fits, and the required Notion title/Job field.

Linked views are the renderer. Executors update Job Ledger records for enabled lanes; do not rebuild static job tables. The quarantined Skilled Worker page remains a reference view over preserved canonical state and is not refreshed by source acquisition.

### Job run procedure

For each job-enabled run of an authorized executor (the retired `LIFE OS Daily Runs` is not one):
1. accept the current exact-trigger Notion Job snapshot;
2. validate Job Engine config/panel;
3. read manual Applied/Saturn/lifecycle state;
4. acquire enabled-lane observations only;
5. normalize identity by Stable Job Key;
6. compute/recompute LIFE OS Fit where owned and apply common qualification + lane route evidence;
7. merge append-only through the shared lifecycle engine;
8. create/update Notion only for changed/new exact keys;
9. verify affected writes by exact fetch or accepted saved view when practical;
10. verify no duplicate Stable Job Keys or multi-lane visible duplicates;
11. verify Newsletter exposes zero rows below the validated fit floor;
12. never delete rows merely to manufacture parity.

Quarantined Skilled Worker is skipped before acquisition and cannot be turned into an empty/closed lane from source absence.

### Passed / Review Notes

Surface plausible review/excluded candidates with exact reasons when useful, including the Target Company 68–77 band, US-not-remote, explicit US pay below floor, US Web stale/unresolved freshness, future Skilled Worker stale/unresolved freshness after re-entry, unresolved/negative visa route, definitive closure, or another exact gate. Newsletter candidates below the shared fit floor are Excluded rather than routed to Passed / Review. Missing compensation alone is never rejection; Scale-up compensation is never rejection.

## Drive Index

**Status: DISABLED — NOT DELIVERED** under this contract (indexing ran in the retired `LIFE OS Daily Runs` full branches). Canonical store: `collection://NOTION-COLLECTION-07`; natural key is the exact Drive File ID (global invariants). Drive Index is metadata only: never copy Drive file contents into Notion. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Weekly Reports Archive and Notes lifecycle

**Status: DISABLED — NOT DELIVERED** under this contract. The 6 PM Daily Report archive, Sunday 6 PM final Notes freeze, and Monday 6 AM Notes reset were Daily Runs boundaries and are void; they leave no obligation. The archive hub (`https://app.notion.com/p/NOTION-PAGE-11`) and existing archived pages are historical evidence only and are never a second source of truth; nothing governed here creates, edits, or deletes them. Live Notes are Jim's and no executor under this contract may reset them. `docs/weekly-reports-archive-contract.md` is a subordinate specification of the retired boundaries and carries no current authority. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Weekly Review

**Status: DISABLED — NOT DELIVERED** under this contract. The Sunday 6 PM Weekly Review belonged to the retired `LIFE OS Daily Runs`. Existing Weekly Reviews rows (natural key: Week Ending) are preserved, and any Decision Queue source mutation still requires Jim's approval. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Full-report branch procedures

**Status: RETIRED — DISABLED — NOT DELIVERED.** The 6 AM, 9 AM, 12 PM, and 6 PM branch procedures were the retired `LIFE OS Daily Runs` run book. No branch exists, none is required for acceptance, and nothing may reconstruct them from git history. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Coverage & System Health

**Status: DISABLED — NOT DELIVERED** under this contract. Whatever an executor reports about itself, a failed or unknown source/query/tool state is never translated into `0`, and QA Self-Check never appears in a human-facing report.

## Executor response

This contract defines no scheduled-task response format. The retired `LIFE OS Daily Runs` completion message is void.

## Provenance — NON-EXECUTABLE

This v2.15.11 file preserves the complete v2.15.10 production authority and adds Jim's explicit September 28, 2026 ownership rule for Newsletter terminal browser recovery evidence: `LIFE OS Daily Runs` (ChatGPT/browser) owns one bounded current exact-vacancy recovery read and the existing private `Current Recovery Evidence` handoff; the `life-os-v2` Runtime owns exact-vacancy correlation, freshness validation, deterministic terminal-evidence resolution, shared qualification, guarded canonical Job Ledger persistence, authoritative read-back, and safe Gmail finalization, and never runs a browser.

This v2.15.10 file preserves the complete v2.15.9 production authority and adds Jim's explicit September 25, 2026 ownership rule for US Remote terminal browser recovery evidence: `LIFE OS Daily Runs` (ChatGPT/browser) owns the bounded current browser/provider recovery read and approved private `Current Recovery Evidence` handoff; the `life-os-v2` Runtime owns validation/consumption, deterministic terminal-evidence resolution, shared qualification, canonical Job Ledger persistence, and authoritative read-back, and never runs a browser.

This v2.15.5 file preserves the complete v2.15.4 production authority except for Jim's explicit Skilled Worker quarantine. Skilled Worker v1 is removed from current Runtime acquisition, current required-lane/continuity obligations, cloud acceptance requirements, and active Daily Report lane output while known-good Job Ledger state and the persistent reference page are preserved. The approved future Phase 2 direction is Google Cloud Run Jobs for high-volume acquisition compute with a Google Sheet only as a non-canonical employer/acquisition index; the existing shared Job Engine, canonical Notion Job Ledger, and sole `LIFE OS Daily Runs` scheduler remain unchanged.

**No historical Production Contract revision or historical contract blob is required, incorporated, or authorized as a runtime prerequisite under v2.15.15.**

This v2.15.15 file records Jim's October 20, 2026 CT retirement of `LIFE OS Daily Runs`: LIFE OS Daily Runs is RETIRED, disabled, and has no current LIFE OS production execution authority. V7's existing GitHub scheduled tick and domain workflows are the sole scheduled LIFE OS executor. The `DAILY RUNS RETIREMENT OVERRIDE` supersedes all body text that describes Daily Runs as enabled or as holding scheduled production authority; that body text is retained as provenance only. The retired Interview Canon write gate is unchanged.

This v2.15.16 file records Jim's November 16, 2026 grant of the Followup lane to V7 as a single `followup` stage in V7's existing hourly tick, with one new `v7_followup_*` table family in V7's existing MySQL store. The lane stays DISABLED — NOT DELIVERED until its natural scheduled-run acceptance; the grant creates no scheduler, workflow family, orchestration layer or other datastore.

This v2.15.17 file records Jim's November 18, 2026 CT Approval B (LIFE-837): one scheduling authority (this contract) with multiple individually approved, bounded V7 GitHub Actions schedules. It replaces the exclusive hourly-tick wording in the header, the retirement override, the global invariants and the platform summary, preserves every domain grant and the retirement of `LIFE OS Daily Runs`, and is non-activating: it preserves existing authorized V7 scheduling, adds no workflow, schedule, runner, credential, permission or datastore, and new or changed schedules need the per-workload approval above.

This v2.15.19 file records Jim's November 26, 2026 approvals (V7 LIFE-910 remediation, LIFE-1026): the LIFE-918 activation of the V7 Jira sprint rollover and daily reconciliation for JFM and MEJ on November 25, 2026, 2:30 PM CT, with the daily reconciliation delivered and the weekly rollover's natural-boundary acceptance pending; the weekly rollover and daily reconciliation as separate steps of the existing `jira` job; exactly eight contiguous future weekly sprints (`JIRA_FUTURE_SPRINTS`, default 8); short sprint names `PROJECT W{week} {year}`; phone-push failure alerts; and authority for JFM and MEJ quarterly metric promotion on the first-Wednesday calendar (first evaluation at or after Wednesday, January 6, 2027, 8:00 AM CT; two consecutive passing daily evaluations at least 24 hours apart, reset on failure, missed evaluation or material input change; earliest promotion Thursday, January 7, 2027), which is DISABLED — NOT DELIVERED (`V2_PROMOTION` is configured on, `V2_OFFICIAL` stays off, and official promotion is neither delivered nor authorized), with the setter's write permission proved only by one separately authorized same-value update that keeps `V2_OFFICIAL` off (Option A; Option B rejected); and the distinction between the retired legacy accountability sync and the active V7 V3 page writer. It adds no workflow, schedule, runner, credential, permission or datastore.
