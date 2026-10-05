# LIFE OS V7: the one plan (plain words)

Last updated: October 5, 2026 (afternoon). This file is the source; Notion shows the same operating plan.

## Roadmap contract

- **Done means:** merged where code is involved, run live once, a counts-only or equivalent acceptance check passes, and Jim says it is done.
- **Status values:** 🔵 To-Do, 🟡 In Progress, 🔴 Blocked, 🟢 Done.
- **Live, awaiting Jim:** keep the row 🟡 In Progress until Jim explicitly confirms Done; say `Live, awaiting Jim` in the row so production-live work is not confused with unfinished implementation.
- **One owner per WBS element:** every tracked row is owned by exactly one of `ChatGPT`, `Jim`, or `Claude Code`. If ownership changes between steps, split the work into separate WBS elements in dependency order.
- **WBS contract:** every Item starts with a stable WBS element using `<DOMAIN>-<STREAM>.<ELEMENT>`. WBS IDs are semantic identifiers, not row numbers: never renumber them merely because delivery order changes. New work gets the next unused child within its domain. References should use the WBS ID first.
- **Order rule:** the roadmap is topological: prerequisite work appears before the work it unlocks. Blocked dependent work sits immediately after its unmet prerequisite where practical.
- **Privacy rule:** never commit source-derived personal identifiers to this public roadmap: no personal email addresses, full personal names, phone numbers, or street/postal addresses. Use neutral system/account/role labels instead. The fixed owner labels `Jim`, `ChatGPT`, and `Claude Code` are allowed.
- **Safety rules:** private data never goes in this public repository; a failed step says so instead of showing old numbers as fresh; consequential database/secret/schema changes retain their existing approval gates.

## Tracking table

| WBS | Item | Status | What it is / delivers | Owner | Next step | Needs Jim | Proof |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MEGI-1.1 | MegIBOW — Monday rollover + final acceptance | 🟡 In Progress | **Live, awaiting Jim.** Hourly accountability table, cumulative totals, warnings, review queue and fail-closed degraded state are live. | ChatGPT | After the first Monday rollover, verify the freeze, complete one real review item end to end, and run the 16 real-dashboard acceptance scenarios. | Yes — final Done confirmation after the remaining proof. | D134; PRs 150, 151, 152. Live hourly. Warnings accepted. First rollover/freeze proof due Mon Oct 12; Wednesday 1:30/2:30 PM CT phone alert is failure-only. |
| NET-1.2 | Network importer — NET-1b | 🟢 Done | Imports deterministic network people and positions from the approved private attachment; ambiguous identity rows are held rather than guessed. Inspector evidence is folded into this WBS element and is not tracked separately. | Claude Code | None. | No. | **Jim confirmed Done Oct 5.** D135; PRs 154, 155. Live read-back: 3,453 source rows / 3,346 people / 3,244 positions / 107 held. Replay wrote 0 new. No email address stored; 7 `@` values were job-title text. The 107 held rows contain only a connection date, so there is nothing else safe to import. |
| ATTN-0.1 | Outlook tagging — real tagged fixture exists | 🟢 Done | The intended mailbox already contains real mail carrying the LIFE OS Attention category; no manual tagging setup remains. | Jim | None. | No. | Real tagged fixture existed before the V7 acceptance run and was independently confirmed. |
| ATTN-1.1 | Outlook tagging — V7 detects the tag | 🟢 Done | V7 reads the real Outlook category spellings, classifies the tagged mail and writes/read-backs Attention state. | ChatGPT | None. | No. | **Jim confirmed Done Oct 5.** PR 156. Dry: `outlook_accounts=2`, `outlook=34`, `sources_failed=0`. Live: `gmail=1`, `admitted=35`, `created=19`, `sources_failed=0`, `verified=true`, push sent. |
| ATTN-1.2 | Attention improvements — received date, action categories and source links | 🟢 Done | Rows carry Received date; categories express what action is owed; titles and row pages carry source links; the Daily Report view is reduced to four working columns. | Claude Code | None. | No. | **Jim confirmed Done Oct 5.** D136, D137; PRs 158, 159. First classification run: FYI 23 / Reply needed 11 / Security 1 / Review-decide 0. Received dates and category behavior confirmed. Known native Notion limitation: a database title cell still represents the Notion row/page; browser new-tab targeting is not configurable on that title click. |
| DOC-1.1 | Scoping documents and roadmap housekeeping | 🟢 Done | Keeps current scopes, decisions and the master roadmap aligned with accepted product direction. | ChatGPT | None. | No. | **Jim confirmed Done Oct 5.** Scoped documents merged (PRs 149, 153, 157); decisions D132–D138 recorded. Future documentation belongs to the feature that changes it. |
| OPS-1.1 | Scheduled runs since Fit clean-up — production verification | 🟢 Done | Verifies scheduled production behavior after Fit cleanup from authoritative run evidence. | ChatGPT | None. | No. | **Jim confirmed Done Oct 5.** Run 319 (outside-timer tick, the real scheduler per D31) passed the gate, 1,065 tests and every stage; Scale-Up live reconciled by D138 (PR 162). |
| BILL-1.1 | Bills recurrence — real weekly / bi-weekly / quarterly / yearly cases | 🟢 Done | Proves recurrence acceptance across weekly, bi-weekly, quarterly and yearly behavior. | ChatGPT | None. | No. | **Jim confirmed Done Oct 5.** Yearly was proven LIVE on a real Paid transition. Weekly and Quarterly are covered by deterministic recurrence acceptance tests. Bi-Weekly is covered by deterministic tests and a controlled LIVE canonical Bills fixture: the production Bills Paid processor reported `paid_rows=1`, `advanced=1`, `failed=0`, `writes=2`; read-back showed Due Date advanced from Oct 5 to Oct 19, Last Paid set to Oct 5, and Paid cleared. The fixture was then removed from the Bills data source and an exact-name query returned zero canonical rows. The proof run used the same production Bills code as current main; intervening changes touched only the roadmap and recurrence test file. |
| BILL-1.2 | Bills overdue count — reconcile 24 overdue bills | 🟢 Done | Reconciles the dashboard overdue count against canonical bill rows. | ChatGPT | None. | No. | **Jim confirmed Done Oct 5.** Live Bill Tracker reconciliation matched 24 stale-due rows against the canonical active bill set. |
| AMZ-1.1 | Amazon missing-field behavior — real orders with no date / total | 🟢 Done | Proves missing date/total values render truthfully without invented data. | ChatGPT | None. | No. | **Jim confirmed Done Oct 5.** Two real canonical Amazon Orders had no `Ordered At` and no `Grand Total`; live read-back preserved both fields as missing. The Daily Report rendered each as `amount unknown` rather than `$0` or a guessed value, while showing the real source-backed `Latest Event At` delivery date. Current deterministic coverage also asserts missing totals render `amount unknown` and never `$0`. |
| SAFE-1.1 | Safety net — weekly hardening slice | 🔵 To-Do | Small fixed reliability slice: Fit re-check after a rule change, real-driver SQL smoke test, saved representative Fit job set, and phone alert when a job source stops delivering. | Claude Code | Implement the fixed hardening slice only; no generic reliability framework. | No. | Required proof: representative Fit re-check; SQL smoke against real DB driver; saved real-job Fit set; source-stop phone alert. |
| MAIL-1.1 | Mail Alerts — Notion presentation surface | 🔵 To-Do | Creates/restores the Daily Report physical-mail presentation region without changing canonical mail state. | ChatGPT | Create or restore the approved surface and verify its structure. | No. | Surface exists in the approved location; protected regions unchanged. |
| MAIL-1.2 | Mail Alerts — V7 runtime wiring | 🔴 Blocked | Wires accepted physical-mail lifecycle state into the Mail Alerts surface with authoritative read-back. | Claude Code | Implement after MAIL-1.1 exists. | Yes — final Done confirmation after live proof. | Blocked on MAIL-1.1. |
| HIRE-1.1 | Hiring Pipeline — Notion presentation surface | 🔵 To-Do | Creates/restores the Daily Report hiring-pipeline presentation region without changing hiring canonical state. | ChatGPT | Create or restore the approved surface and verify its structure. | No. | Surface exists in the approved location; protected regions unchanged. |
| HIRE-1.2 | Hiring Pipeline — V7 runtime wiring | 🔴 Blocked | Wires accepted hiring state into the Hiring Pipeline surface with authoritative read-back. | Claude Code | Implement after HIRE-1.1. | Yes — final Done confirmation after live proof. | Blocked on HIRE-1.1. |
| RECR-1.1 | Recruiters — `Needs review` property and view | 🔵 To-Do | Adds the Notion review field/view needed to hold uncertain recruiter classifications. | ChatGPT | Add `Needs review` and configure the recruiter view. | No — this connector-capable schema/view step is agent-owned. | Property/view exist and uncertain classifications can be held for review. |
| RECR-1.2 | Recruiters — V7 runtime wiring | 🔴 Blocked | Routes accepted recruiter/human-outreach state into the interactive recruiter surface. | Claude Code | Implement after RECR-1.1. | Yes — final Done confirmation after live proof. | Blocked on RECR-1.1. |
| DEL-1.1 | Delivery tracking — tracking-number census from mail | 🔵 To-Do | Finds and counts real tracking numbers before any carrier/API persistence work. | Claude Code | Implement the mail census with no database change. | No. | Live mail census works; counts checked; no unintended DB mutation. |
| DEL-1.2 | Delivery tracking — carrier credentials / developer access | 🔴 Blocked | Supplies carrier developer credentials only if DEL-1.1 proves they are actually required. | Jim | Wait for DEL-1.1 to determine the real carrier/API requirement. | Yes — credential action if required. | Blocked on DEL-1.1. |
| DEL-1.3 | Delivery tracking — carrier-aware lifecycle | 🔴 Blocked | Completes carrier lookup, in-transit state, delivery state and presentation. | Claude Code | Proceed after DEL-1.1 and DEL-1.2 where credentials are required. | Yes — final Done confirmation after live proof. | Blocked on delivery prerequisites. |
| NET-2.1 | Network next slice — change events | 🔵 To-Do | Adds temporal change-event handling on top of the accepted NET-1 state. | Claude Code | Implement the smallest change-event slice from current canonical network state. | No. | NET-1.2 is Done; this is the first next network slice. |
| NET-2.2 | Network next slice — matching | 🔴 Blocked | Matches useful network contacts to current jobs/interviews from accepted temporal state. | Claude Code | Proceed after NET-2.1. | Yes — final Done confirmation after live proof. | Blocked on NET-2.1. |
| NET-2.3 | Network next slice — surfacing | 🔴 Blocked | Surfaces the small ranked network shortlist without creating a second CRM. | Claude Code | Proceed after NET-2.2. | Yes — final Done confirmation after live proof. | Blocked on NET-2.2. |
| JOBS-1.1 | Jobs OS clean-up — ranking / data-quality defects | 🔵 To-Do | Fixes the accepted cleanup list for work mode/date, company edge cases, Fit regressions and stale company labels. | Claude Code | Fix the named cleanup package and verify each case live. | No. | Each accepted defect verified live; counts/results checked. |
| RECR-2.1 | Recruiters / Hiring Pipeline — mail-evidence routing | 🔴 Blocked | Routes accepted mail evidence into recruiter and hiring projections without making email the canonical workflow store. | Claude Code | Proceed after MAIL-1.2, HIRE-1.2 and RECR-1.2. | Yes — final Done confirmation after live proof. | Blocked on recruiter/hiring presentation/runtime prerequisites. |
| MAIL-2.1 | Physical Mail tracking — forwarding / tracking / delivered lifecycle | 🔴 Blocked | Tracks physical-mail items through action, forwarding, tracking and delivered state. | Claude Code | Proceed after delivery/mail tracking prerequisites. | Yes — final Done confirmation after live proof. | Blocked on delivery/mail prerequisites. |
| COCK-1.1 | Company cockpit — Notion operating surface / product presentation | 🟡 In Progress | Defines the operating cockpit presentation without creating a second source of truth. | ChatGPT | Finish current source mapping and presentation boundaries. | Final product acceptance if the cockpit meaning changes. | Product/surface contract still in progress. |
| COCK-1.2 | Company cockpit — V7 live synthesis | 🔴 Blocked | Populates the cockpit from accepted Jira, Outlook, Calendar and Notion evidence. | Claude Code | Implement after COCK-1.1 and required source paths are accepted. | Yes — final Done confirmation after live proof. | Blocked on COCK-1.1. |
| EDGE-1.1 | Edge Gateway — VM prerequisite | 🔵 To-Do | Provides the approved VM prerequisite for the edge gateway. | Jim | Provision the prerequisite when this work reaches execution. | Yes. | Required prerequisite not yet completed. |
| EDGE-1.2 | Edge Gateway — implementation | 🔴 Blocked | Implements the approved edge/gateway role on the prerequisite VM. | Claude Code | Proceed after EDGE-1.1. | Yes — final Done confirmation after live proof. | Blocked on EDGE-1.1. |
| JOBS-2.1 | Lensa product/source decision | 🔵 To-Do | Decides whether and how the source belongs in the approved Jobs architecture. | Jim | Make the product/source decision when reached. | Yes. | Decision pending. |
| JOBS-2.2 | Reed parser | 🔵 To-Do | Adds the bounded parser capability in the existing Jobs architecture. | Claude Code | Implement when its turn is reached. | No. | Pending. |
| ACCT-1.1 | Accountability — product/surface definition | 🔵 To-Do | Defines the accountability product/surface before runtime work. | ChatGPT | Define the bounded product/surface contract. | Final product acceptance if meaning changes. | Pending. |
| ACCT-1.2 | Accountability — runtime implementation | 🔴 Blocked | Implements the accepted accountability behavior. | Claude Code | Proceed after ACCT-1.1. | Yes — final Done confirmation after live proof. | Blocked on ACCT-1.1. |
| DRIVE-1.1 | Drive Index | 🔵 To-Do | Implements the accepted Drive Index work in V7. | Claude Code | Implement when reached. | No. | Pending. |
| RECR-2.2 | Recruiter-mail brief | 🔴 Blocked | Produces the recruiter-mail brief from accepted routed evidence. | Claude Code | Proceed after RECR-2.1. | Yes — final Done confirmation after live proof. | Blocked on RECR-2.1. |
| INT-1.1 | Interview OS promotion | 🔴 Blocked | Promotes the accepted Interview OS lifecycle after prerequisite live proof. | Claude Code | Proceed when prerequisite lifecycle proof is complete. | Yes — final Done confirmation after live proof. | Blocked on prerequisite lifecycle proof. |
| GOV-1.1 | Privacy incident — optional GitHub Support purge request | 🔵 To-Do | Optional follow-up for the already-corrected public-roadmap privacy incident. | Jim | If desired, ask GitHub Support to purge any remaining server-side/cached reachability of the old object. | Yes — optional. | PR 155 replaced the identifier; Jim-approved history rewrite removed it from current `main` history. GitHub may still serve the old object by ID until purged. |

## Surfaces

- **MegIBOW:** accountability block plus Review database on the shared accountability dashboard.
- **Attention:** `Received` date, `Reply needed` / `Review / decide` / `FYI` category options, source-link formula, and updated views. The Daily Report presentation is reduced to the working four-column view; the database is locked.

## Incident record — public roadmap privacy

A direct connector roadmap commit introduced a source-derived personal identifier into the public repository and the privacy test caught it only after the fact. PR 155 replaced it; Jim then approved a history rewrite that removed it from current `main` history. GitHub may still serve the old object by ID through cached or historical surfaces until its own purge completes. The standing rule is now explicit: ChatGPT must never put personal email addresses, full personal names, phone numbers, or street/postal addresses into public repo files.

## Execution order

After the active proof/acceptance rows, the locked delivery order is:

`SAFE-1.1 → MAIL-1.1/1.2 + HIRE-1.1/1.2 → RECR-1.1/1.2 → DEL-1.1/1.2/1.3 → NET-2.1/2.2/2.3 → JOBS-1.1`

Do not reorder that chain unless Jim explicitly changes it.

## Jim's own to-dos

- Check MEGI-1.1 after Monday's rollover and confirm Done only after the remaining proof passes.
- Answer MegIBOW review items as they appear.
- GOV-1.1 is optional.
- Other Jim-owned rows above remain the only manual roadmap work; agent-capable Notion/configuration/review work must not be pushed to Jim.

## On hold — do not do

| WBS | Item | Status | Owner | Reason |
| --- | --- | --- | --- | --- |
| GOV-9.1 | Contract branch in `life-os-automation` — reactivate or keep frozen | 🔴 Blocked | Jim | Explicit hold. |
| NET-9.1 | LI Connection page scope-clause change — reactivate or keep frozen | 🔴 Blocked | Jim | Explicit hold. |
| NET-9.2 | ChatGPT Network Lookups module text — reactivate or keep frozen | 🔴 Blocked | Jim | Explicit hold. |
| GOV-9.2 | Any other production-contract change outside an approved package | 🔴 Blocked | Jim | Requires explicit authorization. |