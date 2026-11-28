# Production Contract part 7: Drive index, weekly reports, coverage, executor response, provenance

<!-- Part of docs/life-os-production-contract.md (the index). Section text moved unchanged. -->

## Drive Index

**Status: DISABLED — NOT DELIVERED** under this contract (indexing ran in the retired `LIFE OS Daily Runs` full branches). Canonical store: `collection:drive_index`; natural key is the exact Drive File ID (global invariants). Drive Index is metadata only: never copy Drive file contents into Notion. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Weekly Reports Archive and Notes lifecycle

**Status: DISABLED — NOT DELIVERED** under this contract. The 6 PM Daily Report archive, Sunday 6 PM final Notes freeze, and Monday 6 AM Notes reset were Daily Runs boundaries and are void; they leave no obligation. The archive hub (`notion:weekly_reports_archive_hub`) and existing archived pages are historical evidence only and are never a second source of truth; nothing governed here creates, edits, or deletes them. Live Notes are Jim's and no executor under this contract may reset them. `docs/weekly-reports-archive-contract.md` is a subordinate specification of the retired boundaries and carries no current authority. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Weekly Review

**Status: DISABLED — NOT DELIVERED** under this contract. The Sunday 6 PM Weekly Review belonged to the retired `LIFE OS Daily Runs`. Existing Weekly Reviews rows (natural key: Week Ending) are preserved, and any Decision Queue source mutation still requires Jim's approval. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Full-report branch procedures

**Status: RETIRED — DISABLED — NOT DELIVERED.** The 6 AM, 9 AM, 12 PM, and 6 PM branch procedures were the retired `LIFE OS Daily Runs` run book. No branch exists, none is required for acceptance, and nothing may reconstruct them from git history. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Coverage & System Health

**Status: DISABLED — NOT DELIVERED** under this contract. Whatever an executor reports about itself, a failed or unknown source/query/tool state is never translated into `0`, and QA Self-Check never appears in a human-facing report.

## Executor response

This contract defines no scheduled-task response format. The retired `LIFE OS Daily Runs` completion message is void.

## Provenance — NON-EXECUTABLE

This v2.15.11 file preserves the complete v2.15.10 production authority and adds Jim's explicit September 28, 2026 ownership rule for Newsletter terminal browser recovery evidence: `LIFE OS Daily Runs` (ChatGPT/browser) owns one bounded current exact-vacancy recovery read and the existing private `Current Recovery Evidence` handoff; the retired runtime owns exact-vacancy correlation, freshness validation, deterministic terminal-evidence resolution, shared qualification, guarded canonical Job Ledger persistence, authoritative read-back, and safe Gmail finalization, and never runs a browser.

This v2.15.10 file preserves the complete v2.15.9 production authority and adds Jim's explicit September 25, 2026 ownership rule for US Remote terminal browser recovery evidence: `LIFE OS Daily Runs` (ChatGPT/browser) owns the bounded current browser/provider recovery read and approved private `Current Recovery Evidence` handoff; the retired runtime owns validation/consumption, deterministic terminal-evidence resolution, shared qualification, canonical Job Ledger persistence, and authoritative read-back, and never runs a browser.

This v2.15.5 file preserves the complete v2.15.4 production authority except for Jim's explicit Skilled Worker quarantine. Skilled Worker v1 is removed from current Runtime acquisition, current required-lane/continuity obligations, cloud acceptance requirements, and active Daily Report lane output while known-good Job Ledger state and the persistent reference page are preserved. The approved future Phase 2 direction is Google Cloud Run Jobs for high-volume acquisition compute with a Google Sheet only as a non-canonical employer/acquisition index; the existing shared Job Engine, canonical Notion Job Ledger, and sole `LIFE OS Daily Runs` scheduler remain unchanged.

**No historical Production Contract revision or historical contract blob is required, incorporated, or authorized as a runtime prerequisite under v2.15.15.**

This v2.15.15 file records Jim's October 20, 2026 CT retirement of `LIFE OS Daily Runs`: LIFE OS Daily Runs is RETIRED, disabled, and has no current LIFE OS production execution authority. V7's existing GitHub scheduled tick and domain workflows are the sole scheduled LIFE OS executor. The `DAILY RUNS RETIREMENT OVERRIDE` supersedes all body text that describes Daily Runs as enabled or as holding scheduled production authority; that body text is retained as provenance only. The retired Interview Canon write gate is unchanged.

This v2.15.16 file records Jim's November 16, 2026 grant of the Followup lane to V7 as a single `followup` stage in V7's existing hourly tick, with one new `v7_followup_*` table family in V7's existing MySQL store. The lane stays DISABLED — NOT DELIVERED until its natural scheduled-run acceptance; the grant creates no scheduler, workflow family, orchestration layer or other datastore.

This v2.15.17 file records Jim's November 18, 2026 CT Approval B (LIFE-837): one scheduling authority (this contract) with multiple individually approved, bounded V7 GitHub Actions schedules. It replaces the exclusive hourly-tick wording in the header, the retirement override, the global invariants and the platform summary, preserves every domain grant and the retirement of `LIFE OS Daily Runs`, and is non-activating: it preserves existing authorized V7 scheduling, adds no workflow, schedule, runner, credential, permission or datastore, and new or changed schedules need the per-workload approval above.

This v2.15.19 file records Jim's November 26, 2026 approvals (V7 LIFE-910 remediation, LIFE-1026): the LIFE-918 activation of the V7 Jira sprint rollover and daily reconciliation for JFM and MEJ on November 25, 2026, 2:30 PM CT, with the daily reconciliation delivered and the weekly rollover's natural-boundary acceptance pending; the weekly rollover and daily reconciliation as separate steps of the existing `jira` job; exactly eight contiguous future weekly sprints (`JIRA_FUTURE_SPRINTS`, default 8); short sprint names `PROJECT W{week} {year}`; phone-push failure alerts; and authority for JFM and MEJ quarterly metric promotion on the first-Wednesday calendar (first evaluation at or after Wednesday, January 6, 2027, 8:00 AM CT; two consecutive passing daily evaluations at least 24 hours apart, reset on failure, missed evaluation or material input change; earliest promotion Thursday, January 7, 2027), which is DISABLED — NOT DELIVERED (`V2_PROMOTION` is configured on, `V2_OFFICIAL` stays off, and official promotion is neither delivered nor authorized), with the setter's write permission proved only by one separately authorized same-value update that keeps `V2_OFFICIAL` off (Option A; Option B rejected); and the distinction between the retired legacy accountability sync and the active V7 V3 page writer. It adds no workflow, schedule, runner, credential, permission or datastore.
