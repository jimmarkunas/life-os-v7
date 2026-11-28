# Production Contract part 1: Overrides (retirements and ownership)

<!-- Part of docs/life-os-production-contract.md (the index). Section text moved unchanged. -->

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
| Drive Index, Weekly Reports Archive, Notes lifecycle, Jim & Matt accountability sync (the retired legacy dashboard sync, not the V7 V3 page writer) | DISABLED — NOT DELIVERED |
| Weekly LIFE OS Review (Weekly Reviews row per Friday week ending) | V7 only; DELIVERED (LIFE-945, trimmed by LIFE-1108; amended by Jim's permission, November 28, 2026); see `Weekly Review` in the reports section |
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
- Any consumer of the latest accepted V7-owned Mail Alerts state may not refresh or repair the region itself.
- Missing, stale, partial, failed, or unaccepted V7 Mail Alerts evidence is `DEGRADED` for any consuming ChatGPT presentation. Do not fall back to the retired unread-mail query and do not present a trustworthy empty state.
- Physical Mail remains excluded from Attention. No second writer is permitted.
- Runtime target: the existing Daily Report callout headed exactly `Mail Alerts`; V7 addresses it through its configured `MAIL_ALERTS_CARD_BLOCK_ID`.

## INTERVIEW CANON OWNERSHIP OVERRIDE — October 20, 2026 CT

- **V7 is the sole live owner and writer of the Hiring Pipeline Interview OS pages** (opportunity parents and interview children) at the canonical root, Hiring Pipeline hub `notion:hiring_pipeline_hub`. Jim authorized the single-writer transition on October 20, 2026 CT (LIFE-88, PR #408 in `jimmarkunas/life-os-v7`).
- The prior Interview Canon writer (`scripts/interview_canon_runtime.py` and `interview_canon/runtime.py`) has **no write authority**. Its production gate reads the marker below from this file and refuses every apply while the marker is `false`. Nothing (including the retired `LIFE OS Daily Runs`) may run it in `qa-apply` or `apply` mode against the canonical root, must not assemble its input payload, and must not create, update, reorder or normalize any Interview OS page. The Smith/Willie layouts and the creation gates in `docs/interview-canon-production-contract.md` are superseded by the approved UI 3c / UI 4 pages and V7's identity and progression rules.
- V7 owns the evidence, identity, page creation and in-place refresh, and the authoritative read-back, through its existing hourly tick and `domains.yml` execution path (`lifeos.run interview`). This creates no new scheduler, workflow family, orchestration layer, or datastore. Each side stays the sole scheduler in its own scope, as the October 2026 scheduler-scope rule already records.
- Missing, stale, partial, failed or unaccepted V7 Interview OS output is `DEGRADED` for any consumer; it never falls back to the retired writer or the retired executor.
- **Activation order (one writer at a time):** (1) this contract is merged on the retired system's main branch, which retires the old writer; (2) only then Jim sets the V7 repository variable `INTERVIEW_OS_WRITER=v7`. Until both have happened V7 runs the stage as a dry run and writes nothing. Jim alone may reverse the order of ownership, and only by editing this section.
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
- **Activation order (one step at a time):** (1) this contract is merged on the retired system's main branch; (2) Jim approves creating the `v7_followup_*` tables; (3) Jim approves each source credential grant; (4) the `followup` workflow step is added to V7 with no new `permissions`; (5) the natural run above; (6) only then Jim records delivery in the status table. Followup actions (confirm resolution, update checkpoint) and every Followup OS 8.1 roadmap capability (act now, drafting, email send, Calendar proposals, Jira/Todoist promotion) stay NOT DELIVERED.

## NEWSLETTER + US REMOTE OPERATING OVERRIDE — September 23, 2026

- **Newsletter acquisition is active.**
- **US Remote web acquisition is active.**
- **Scale-Up acquisition is ACTIVE in V7 Phase 1.** Preserve its scheduled acquisition and provider-specific source/recovery mechanics, and keep its downstream identity/Fit/persistence/lifecycle on the shared Jobs core.
- **Skilled Worker remains QUARANTINED and is Phase 2.** Preserve known-good/reference state only until Jim explicitly promotes it.
- US Remote runs through the existing shared V3 Jobs path: `Acquire → SourceVacancyObservation → Identify/Dedupe → Persist/Read Back → Terminal Evidence → Reconcile/Read Back → LIFE OS Fit → Qualify → Present`.
- **US Remote terminal browser-evidence ownership:** when deterministic/provider evidence cannot establish trustworthy terminal vacancy evidence, **[DISABLED — NOT DELIVERED: `LIFE OS Daily Runs` is retired and no V7 browser-recovery executor exists; the following describes the retired design only.]** `LIFE OS Daily Runs` (ChatGPT/browser) owned the bounded current browser/provider recovery read and the approved private handoff through the `Current Recovery Evidence` Files property on the exact `US Remote` row in Job Lane Configuration. The retired runtime owns validation/consumption of that handoff, deterministic terminal-evidence resolution, shared qualification, guarded canonical Job Ledger persistence, and authoritative read-back; Runtime never runs a browser. Browser recovery is exact-vacancy evidence only, not a second acquisition lane, datastore, scheduler, workflow family, or Jobs engine. Missing, stale, truncated, ambiguous, or uncorrelated browser evidence is `DEGRADED` and must not advance acceptance/checkpoints or fabricate terminal evidence. Where authoritative deterministic/provider evidence already exists, do not browser-refetch.
- **Newsletter terminal browser-evidence ownership:** when deterministic/provider evidence for an exact Newsletter vacancy is exhausted, **[DISABLED — NOT DELIVERED: retired design only.]** `LIFE OS Daily Runs` (ChatGPT/browser) owned one bounded current exact-vacancy recovery read and the approved private handoff through the existing `Current Recovery Evidence` channel used by US Remote. The retired runtime owns exact-vacancy correlation, freshness validation, deterministic terminal-evidence resolution, shared qualification, guarded canonical Job Ledger persistence, authoritative read-back, and safe Gmail finalization; Runtime never runs a browser. Evidence must remain exact-vacancy scoped and must still satisfy employer/ATS or verified native-application terminal authority. Missing, stale, truncated, ambiguous, or uncorrelated recovery evidence is `DEGRADED` / REVIEW and blocks Gmail finalization for that source message. This does not create a second acquisition lane, datastore, scheduler, workflow family, crawler, or Jobs engine.
- Current shared Jobs qualification owns the professional Fit floor: **72**. Do not restore historical US Remote 78 / Target 68–77 scoring policy. Provider scores contribute zero LIFE OS Fit points.
- Safe canonical identity permits persistence before terminal enrichment/Fit.
- US Remote source or accounting failure is `DEGRADED`, never zero/no-change.
- The first production execution after this re-entry must use the existing full-recovery-sweep path and account for every enabled configured source.
- Scale-Up is an active lane with its own production-shaped acceptance and source accounting. Skilled Worker is not an active acceptance dependency.
- Newsletter safe-cleanup semantics remain unchanged.
- V7 GitHub Actions workflows are the sole scheduled LIFE OS execution infrastructure, each on an individually approved schedule. No second scheduler service, duplicate workflow family, datastore, watchdog, dispatcher, or orchestration layer is authorized.
