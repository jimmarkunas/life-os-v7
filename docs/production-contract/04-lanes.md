# Production Contract part 4: Daily Report, recruiters, attention, calendar, bills, mail alerts, hiring pipeline, Amazon

<!-- Part of docs/life-os-production-contract.md (the index). Section text moved unchanged. -->

## Daily Report — RETIRED RENDERING AUTHORITY

**Status: V7 only (the Daily Command Center is V7's `dcc-card` stage, D175); the run stamp and audit history are DISABLED — NOT DELIVERED.** This contract no longer defines how the Daily Report is built, rendered, refreshed, or verified; V7's decisions do. The command-center layout, hourly presentation render, decision-first hierarchy, freshness and component-trust rules, relative-date wording, bounded degraded diagnostics, Daily Jobs List navigation checks, linked-table fit checks, midnight Newsletter status/delta, 12 PM / 6 PM change markers, and audit-history disclosure were all procedures of the retired `LIFE OS Daily Runs` and are void. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

Still binding on any actor:
- **Region ownership is exclusive.** A Daily Report region has one owner. This contract grants V7 the `Mail Alerts` region (see `MAIL ALERTS OWNERSHIP OVERRIDE`) and the Interview OS pages (see `INTERVIEW CANON OWNERSHIP OVERRIDE`) and recognizes the other V7-owned regions listed in `OWNERS`. It grants no region to the retired `LIFE OS Daily Runs` or to any other actor.
- **Protected weekly Notes.** The book-icon Notes callout is Jim's. No executor under this contract may write, reset, archive, or restructure it. Any actor that writes to the Daily Report must fetch the complete page first, uniquely identify the Notes callout, preserve it verbatim, and stop (DEGRADED) rather than perform a broad rewrite if it cannot.
- **Trust is never faked.** A failed, missing, stale, or uncorrelated component is shown as unavailable or degraded, never as `0`, `none`, or an empty list, and a healthy page-level timestamp never upgrades a stale component. Relative-date wording (`today`, `tomorrow`, `overdue`) is computed at write time from absolute dates in America/Chicago and never carried forward.
- **No backend diagnostics in the human cockpit.** Provider errors, trigger tokens, workflow IDs, and continuity internals stay in backend evidence.

## Persistent Recruiters & Human Outreach

**Status: ownership not asserted here** (V7's `recruiters` stage writes the Recruiters data source, but the Daily Report region's owner is not recorded in `OWNERS`); the weekly-persistent maintenance performed by the retired `LIFE OS Daily Runs` is void. Canonical source: `collection:recruiters_human_outreach`. Durable rules that survive for any actor: only direct human recruiter, staffing, hiring-manager, referral, or comparable person-to-person hiring outreach belongs here (automated alerts do not; human outreach is Saturn-exempt); `Done` is Jim's state and is never auto-cleared; resolve by the natural key in the global invariants before creating, and if absence cannot be proven do not create a possible duplicate. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Persistent Attention

**Status: V7 only** for the Daily Report Attention region (V7's `attention` stage); the weekly maintenance and Monday carry-forward procedure was the retired `LIFE OS Daily Runs`' and is void. Canonical source: `collection:attention`. Durable rules: Attention is an exception queue, not a second Jira/task list; `Done` is Jim's state; resolve by Week Ending + Category + normalized Item and never create a possible duplicate; Physical Mail is owned by Mail Alerts and is never admitted to Attention (Jim, October 4, 2026). The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Calendar

**Status: V7 only** (V7's `agenda` card); the retired `LIFE OS Daily Runs` rendering is void. Google Calendar remains the sole fixed-time authority and the Calendar region is read-only for every actor governed here. Never fabricate event state. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

## Bills: This Week

**Status: V7 only** for the Daily Report Bills region (V7's `bills` card); the summary, correlated Job + Bill snapshot trigger, and Paid processor were driven by the retired `LIFE OS Daily Runs` and are void. Canonical Bill Tracker: `collection:bill_tracker`. Durable rules: missing/stale/mismatched/incomplete Finance evidence is DEGRADED and never a zero or empty summary; an unset amount is never silently $0; unattended work must not use metered MCP Notion rows/SQL as a substitute for Public API snapshots. The Runtime's Notion snapshot scripts and the Paid Bills snapshot module (`data/notion-bill-paid-snapshot-trigger.txt`) remain in this repository and have no scheduled producer; Paid-row transitions, `stale_due` rollover, and Bill Tracker mutation are not performed by anything governed here. The former `LIFE OS Daily Runs` procedure for this area is removed as authority; it survives only in git history (through v2.15.14) as provenance.

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
