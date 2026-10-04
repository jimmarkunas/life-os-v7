# Big features roadmap: six features, one sequence (scoping only, no code)

Status: proposal for Jim's review. Nothing here is built, and this document changes no schema, secret, workflow or `docs/DECISIONS.md`.
Scope: Recruiters and Human Outreach, Automated MegIBOW, Network Intelligence, Hiring Pipeline, Physical Mail, and Delivery tracking (the expansion of Amazon Orders). Delivery tracking has its own deep scope in `docs/DELIVERY_TRACKING_SCOPE.md`; this file covers the rest and, above all, what the six have in common.

Method: each feature handoff was checked against the current code on `main`, the claims that matter were re-verified directly, and the V1 and V2 repositories were read as references only. Real mailboxes were inspected by count and category only (`docs/PRIVACY.md`); no content is reproduced here.

## 1. Findings that apply to every feature

1. **Repository routing conflict.** `AGENTS.md` in V7 says V7 is the active implementation and V1 and V2 are read-only references. The `CLAUDE.md` files in V1 and V2 still say V2 is the sole active repository. All six handoffs target V7, and V7 is the newest, so this roadmap treats V7 as active. The stale statement in the other two repositories is Jim's to correct; nothing was changed there.
2. **The handoffs assume mail infrastructure V7 does not have.** There is no provider-neutral mail record and no classifier (the only mention of a `human_hiring` class is the roadmap prompt that asks for one). V7 never reads mail from a sender it has not allowlisted: Newsletters and Amazon Orders both read exact senders only. Gmail returns a dict with the body as text; Outlook returns Microsoft Graph dicts with no body and no headers, and the body needs a second call. Recruiters, Hiring Pipeline, Physical Mail and Delivery tracking all need the same shared record.
3. **Alert state has a hidden coupling.** `lifeos/jobs/alerts.py` loads every open row from `v7_alerts` and `platform/alerts.reconcile` resolves any key the Jobs detector does not list. A second detector run from another stage would therefore silently resolve the Jobs alerts, and the reverse. Every feature that wants alerts is blocked on one small shared design: a combined active set, or key namespaces that each detector reconciles separately.
4. **Daily Report regions are text-only and ownership is strict.** `platform/router.py` has five owned regions. "Hiring Pipeline" is explicitly pinned as unowned by `tests/platform/test_router.py`, and "Mail Alerts" has no owner entry. There is no table writer in `lifeos/`; `report_region.replace_text` replaces text blocks and leaves tables alone. Each existing card also lists the other cards' regions in a protected tuple and digest-checks them, so adding a region means touching every existing card.
5. **Layering shapes where code can live.** OS packages import only `platform`; only `lifeos/sources/` may import across OS packages (`tests/contracts/test_boundaries.py`). So MegIBOW cannot import Interview or Jobs, Hiring Pipeline cannot import the Job Ledger or Interview readers, and anything that composes two OS packages sits in a `sources` adapter. Shared clients that two features need (for example carrier tracking) belong in `platform`.
6. **Every schema change stops work for Jim's approval.** Section 5 lists every table and secret the six features would add, so the approvals can be given together.
7. **`domains.yml`, not `hourly.yml`, is where new scheduled work goes.** It runs after each hourly tick, isolates secrets per job (enforced by `tests/contracts/test_workflow.py`, which needs an entry for each new job), and has room for more inputs. `hourly.yml` is at its 25-input limit.
8. **Two naming collisions to resolve.** Roadmap item 5 "Accountability" is the Jira load dashboard (sprints, target 8, ceiling 10); it is a different product from MegIBOW, which is job-search activity. And the only Wednesday found in V1 or V2 in this context is the Jira accountability meeting, so the MegIBOW brief's "Wednesday 3 PM check" may be a conflation.
9. **A serial rule meets parallel briefs.** `docs/ROADMAP.md` says one PR per item and do not start the next until the last is merged. The MegIBOW and Network Intelligence briefs ask for parallel tracks. Section 6, decision 1.

What the mailboxes showed, by count and category: Gmail held no direct mail from UPS, FedEx, DHL or USPS in 180 days, and the connected Outlook mailbox held none. The mail-forwarding vendor's "new mail" and "completed action" notices are in Gmail, but there is no tracking-number message from that vendor in 365 days of Gmail or in Outlook. A shipping-keyword search in Gmail returned about 200 threads in a year and the sample was mostly not shipping mail.

## 2. Shared primitives, built once inside their first consumer

No standalone framework PRs: each primitive ships with the first feature slice that needs it. Delivery tracking's first slices use the existing Gmail record unchanged so they never wait on the shared record.

| Primitive | What it is | Consumers |
|---|---|---|
| P1 Provider-neutral mail record | One record shape for Gmail and Outlook (id, time, sender, subject, body text, labels or folder, and the list and auto-reply headers that Gmail fetches and discards today), plus HTML-to-text promoted out of `gmail.py` and an Outlook body read | Recruiters (first owner), Delivery tracking (Outlook), Hiring Pipeline mail evidence, Physical Mail, MegIBOW sent mail |
| P2 Alert active-set | Namespaced or combined reconcile so detectors in different stages cannot resolve each other | Recruiters, Delivery tracking alerts, Physical Mail, Phase E |
| P3 Carrier clients | `lifeos/platform/carriers/`: number validators and `track(carrier, number)`, policy-free | Delivery tracking, Physical Mail |
| P4 Calendar event normalization | Lifted from the Agenda package into `platform`; plus a stable logical key for rescheduled events in the Outlook-to-Google bridge (today a reschedule becomes a new event because the id hashes the start time) | MegIBOW, Hiring Pipeline |
| P5 Region helpers | Owner entries and tests, a shared "DEGRADED heading, keep last rows" helper now copied in two cards, and optionally a table writer | Delivery tracking, Hiring Pipeline, Physical Mail |
| P6 Store range query | `snapshot_store.Store` can upsert and load one key but cannot list a range | MegIBOW (weeks) |

## 3. Feature by feature

### 3.1 Recruiters and Human Outreach

**V7 today.** Classifier: missing. The Recruiters data source and any secret for it: missing (verified, no references). Recruiter mail is protected only by omission, because the newsletter senders are exact allowlists. Gmail and Outlook reads exist but with different shapes. Automatic detection of "hiring-manager interview scheduled": missing. Presentation needs no V7 code, because the linked Notion view is Notion-native and V7 only writes rows.

**Where the handoff and the repo differ.**
- "Reuse the existing bounded mail flow" and "existing MailMessage": neither exists; a thin new read loop over platform primitives is unavoidable.
- The live Notion schema is not verified here. Only property names are known; the types of Done, Active and Week Ending are not, and no field is known to hold a message identity.
- V7 keeps data-source identifiers as secrets and the repository is public, so the handoff's identifier goes in `NOTION_RECRUITERS_DATA_SOURCE_ID`, never in code.
- "What they want" has no deterministic source because V7 has no model at runtime (D54). It must be a fixed phrase from detected intent, or blank and flagged.
- The handoff leaves out V1's six routing classes and its job-board relay exception. V2's classifier lets subject-term hits override automation signals, which is right for protection and wrong for deciding whether to write a CRM row.
- "Hiring Pipeline safely rendered first" cannot be satisfied automatically today: the interview creator refuses to create a parent and a round in one run, and human-owned parents are never written.

**Smallest first slice, PR 1.** Cases A, B, C, E and H, dry run by default, no mail mutation: shared mail record, a three-class deterministic classifier, direct-human qualification, then the current-week natural-key upsert (query, create only when absence is proven, read back, `Done` preserved). Expected files: `lifeos/platform/mail.py`, edits to `gmail.py` and `outlook.py`, `lifeos/recruiters/{classify,qualify,identity,notion,stage}.py`, one `run.py` line, a manual `recruiters.yml`, tests and fakes, a contract test that the package never calls a move, relabel or folder call. **PR 2:** the hiring-manager handoff, the alert path, and manual Daily Report acceptance. Its orchestration belongs in `lifeos/sources/` because it spans OS packages.

**Approvals.** No Notion schema change planned (stop and report if the live database lacks a needed field). No Hostinger table (dedupe compares `Last Contact`). New secrets `NOTION_RECRUITERS_TOKEN` and `NOTION_RECRUITERS_DATA_SOURCE_ID`. Hourly gets no input.

**Decisions for Jim.** (1) Scan breadth: an inbox-window read of non-allowlisted mail in memory with counts only, or a narrower query, and whether Gmail beyond the Inbox and Outlook Junk or subfolders are read. (2) The direct-human rule without headers or a model, and whether private-message relays from job boards qualify. (3) "What they want": fixed phrases or blank. (4) Where ambiguous identity surfaces when no schema change is allowed: an alert and counts, or a row. (5) What counts as hiring-manager-interview evidence: a calendar event, interview mail, or Jim. (6) Whether a later message may ever reopen a handled row (default: never).

**Size.** PR 1 is M and trends L if Outlook body and header work grows. PR 2 is L. The alert path is S but waits on P2.

### 3.2 Automated MegIBOW

**V7 today.** Nothing: no code and no references. Calendar acquisition is partial: one shared Google calendar is read, Outlook reaches it through the bridge for the `personal` account only, the bridge copies no attendees, and a reschedule breaks logical identity. Gmail sent-mail acquisition is partial: a bounded sent query works, but there is no metadata-only reader, no recipients and no thread id, and no job-search label or filter exists. Classifier, dedupe, review path, weekly aggregation, freeze, warnings and reconcile command: missing. Persistence is partial (a JSON snapshot store without range queries). The dashboard renderer is missing; the "Jim's Copy" surface cannot be verified from the repository and has no V7 region, secret or table writer.

**What V1 and V2 prove.** Only two direct hits exist: V2's rebuild plan lists "Megibow activity dashboard" as phase 9, and V1's Jira snapshot carries a backlog task to automate it. None of the five definitions, the counts, the scheduled-to-completed rule, cancellation handling, the canonical surface name, the 8-week view or the Wednesday check appears in code or documents. **MEG-0 cannot be reconstructed from the repositories; it needs a short session with Jim.**

**Where the brief and the repo differ.** The brief assumes attendee and organizer evidence (not available), one logical event across reschedules (broken by the bridge), existing labels and filters (none), and typed columns (V7's convention is JSON payload per key). A `megibow` package cannot import Interview or Jobs, so classification context comes through a `sources` adapter.

**Smallest first slice.** MEG-1a: Chicago Monday-to-Sunday and eight-week windows plus a Calendar-only dry-run census (timed, all-day, declined, cancelled, recurring, with-attendee, bridged, future, past counts; no classification). One PR, no schema: `lifeos/megibow/{week,calendar}.py`, a `megibow-census` stage, a manual workflow, tests and a calendar fake. Then MEG-1b: Gmail sent census with a metadata reader. The brief's MEG-1 as written is too large for one PR.

**Approvals.** Later: a table (smallest is `v7_megibow_week` through the snapshot store, keyed by week start, JSON payload with observation hashes and refs, counts, OPEN or FROZEN and source status), a Notion token and surface, `DOMAIN_SECRETS` edits, a bridge change for a reschedule key (needs a one-time backfill), and an input or env var for `--week` reconcile.

**Decisions for Jim.** (1) Confirm the five definitions and the canonical dashboard. (2) Is Scheduled counted in the week it is booked or the week it occurs? (3) The Outreach inclusion rule: a Jim-applied Gmail label, a recipient allowlist, or keywords; is Outlook sent mail in? (4) Is the work Outlook mailbox bridged? (5) Is the Wednesday check MegIBOW or the Jira meeting, and does it alert by push or only on the dashboard? (6) No-show, tentative, and "completed means the time has passed". (7) Approve the table and the Notion surface. (8) Approve the bridge change.

**Size.** MEG-0 S (a decision session), MEG-1a S, MEG-1b M, MEG-2 L, MEG-3 M, MEG-4 M, MEG-5 M, MEG-6 S.

### 3.3 Network Intelligence

**V7 today.** No `lifeos/network/` package, no people or contact code, no person identity logic. Reusable: Hostinger access (`platform/db.py`, tunnel with retry and fixed codes; autocommit, no transaction helper and no upsert helper), per-OS schema ownership with an `ensure_schema` pattern (stages call it even on dry runs, so a network stage must create tables only when live), `platform/names.py` company comparators (no key generator exists, and two company normalizers already coexist). The LinkedIn resolver is job-only and never logs in. "Admitted" exists as `v7_job_fit.admission` (ADMIT, REVIEW, EXCLUDE). The Ledger publisher creates each page once and later syncs patch properties only, so there is no body-append writer; the nearest is `report_region`'s owned-block insert with read-back.

**Where the brief and the repo differ.**
- The brief says Jobs consumes the network domain. `tests/contracts/test_boundaries.py` lets an OS package import `platform` only, so `jobs` cannot import `network`. The glue lives in `lifeos/sources/` or a stage in `run.py`, or D17 and that test are amended.
- Producers live in `lifeos/sources/<name>` (D17), not `network/sources/`.
- The status chain NEW, RESOLVED, ENRICHED, FIT, READY is not the real one. Real statuses are NEW, RESOLVED, READY, PUBLISHED and terminals. The exact trigger is a published job with a page id, a verified time and `admission = ADMIT`; REVIEW jobs also publish, so ADMIT-only versus ADMIT-plus-REVIEW is a decision.
- **The "connected LinkedIn app" is a chat connector.** V7 must run with chat unavailable (D54) and GitHub Actions cannot reach a chat connector. V7 has no people-evidence adapter, so the brief's targeted live refresh has no permitted runtime source today.
- TinyFish Fetch and Search are shared with Jobs under hard caps and limited to job pages (D11, D12 amendment). A people source needs its own decision.
- The repository is public, so workflow inputs and artifacts are world-readable. The connections export cannot travel through them, and a secret holds at most 48 KB. How the seed file reaches Hostinger privately is an open design question.

**Smallest first slice.** NET-0 is needed first but is small and offline (fixtures only, no production mutation). It must prove: the real export's columns and counts through a local counts-only check; how the file reaches Hostinger privately; whether any runtime current-state source exists; and the hit rate of the company key on sample strings. Then NET-1: `lifeos/network/{identity,store,import_linkedin}.py`, a `network-import` stage, two tables (people and positions), dry run by default and `--live`, replay-safe, counts only. **If no live source exists, NET-2 becomes a re-export diff:** re-import a fresh export periodically and derive company-changed and title-changed events from the difference. It is free, deterministic and permitted, and it makes "living" mean a periodic refresh rather than per-person lookups. NET-4 attaches Network Leads as a marker-owned block appended to the Ledger page body (no new Notion property), from a step in the hourly `finish` job after publish, continue-on-error and non-fatal, because the Jobs Notion secrets exist only there. It needs a block-append-with-marker writer generalized from `report_region`.

**Approvals.** Two tables in NET-1 and an events table in NET-2; none for NET-4 unless the marker scan proves insufficient; no change to `v7_jobs`. No secret if the import runs locally; a runner import needs a seed secret and a `DOMAIN_SECRETS` edit. One added step in `hourly.yml` `finish`, no input.

**Decisions for Jim.** (1) Trigger on ADMIT only, or ADMIT and REVIEW. (2) The private path for the seed file: a local run, or split secrets. (3) Accept the re-export diff as the MVP refresh, or amend D12, D14 and the limits table for a people source; and whether chat-connector observations may ever be ingested, which needs a private channel. (4) Glue in `sources/`, or amend the D17 boundary. (5) Network Leads as an appended body block, a property, or a separate page. (6) Whether to store emails at all. (7) Erasure and retention policy for people.

**Size.** NET-0 S, NET-1 M, NET-2 M (L if a live adapter is approved), NET-3 M, NET-4 M, NET-5 L, NET-6 L.

### 3.4 Hiring Pipeline

**V7 today.** `lifeos/jobs/hiring_pipeline.py` is a read-only protection check for Jobs and stays as it is. Interview parent and round creation (identity, protection, read-back) exists but has no live evidence feeder; its only producer is a manual secret in the acceptance harness. `interview/stage.py` is the creation runner, not a stage vocabulary. None of V1's ten stages or its action classes exists in code, and Notion has no stage field. In the Job Ledger, Applied is a human checkbox plus a date, and the lifecycle field was removed, so "application lifecycle" is a binary Applied. There is no region owner, no table writer, and no calendar-to-opportunity matching.

**Where the handoff and the repo differ.** The handoff expects to reuse an Interview stage state; there is none. The V1 stage list exists only in contract text. The Recruiters boundary is a rule only, because Recruiters has no code. The no-renderer claim is verified: V1's four-column table lives only in the archive renderer.

**Smallest first slice, PR 1 (no mail).** Job Ledger Applied rows, Notion parents and rounds (read-only), a Calendar window and a prior-state snapshot feed one pure compiler, which feeds one card. It works on its own and Notion Hiring Pipeline pages stay read-only, so human notes are untouched by construction. Files: `lifeos/hiring/{models,compile,render,snapshot,card}.py`, a `lifeos/sources/hiring.py` adapter (the only layer allowed to import the Ledger and Interview readers), two `run.py` lines, a router entry, the protected tuple in each existing card, a `domains.yml` job with `needs:`, tests and a fake. **PR 2:** accepted-evidence acquisition (classifier, extractor, Recruiters boundary), which also finally feeds Interview creation.

**Approvals.** A `HIRING_CARD_BLOCK_ID` secret and a callout Jim creates; a derived snapshot table if carry-forward is stored (decision 3); `router.OWNERS` and its test; a `domains.yml` job and `DOMAIN_SECRETS` entry. No Notion schema change. `hourly.yml` stays unchanged.

**Decisions for Jim.** (1) Region owner: a V7-owned "Hiring Pipeline" callout (a router change) or hand the rows to the ChatGPT Daily Command Center. (2) The source of accepted mail evidence under the deterministic-only rule: a V7 parser, a ChatGPT insert-only queue, or none in version one. (3) A private snapshot table for carry-forward, or re-parse the previous callout. (4) Table rows or bullet rows (a table needs a new writer). (5) A time limit for waiting states, and whether a Retired Notion page means closed. (6) One acquire step carrying the Jobs, Interview and Calendar secrets together, or one step per secret.

**Size.** Compile, render and card S to M (M with a table writer). Acquisition adapter, workflow and router M. Mail acceptance and the Recruiters boundary L.

### 3.5 Physical Mail

**Terminology.** The handoff's "LIFE OS Daily Runs" is the ChatGPT task (D58). V7's runtime entry is the hourly tick followed by the `domains.yml` jobs. `tests/contracts/test_guardrails.py` allows only `hourly.yml` and `watchdog.yml` to carry a `schedule:`, so no cron is needed or allowed.

**V7 today.** No Mail Alerts owner (confirmed: `router.OWNERS` has five regions), parser, chain reconciler, derivative state or card. Reusable as they are: `Gmail.list_ids_complete` (fails closed on overflow, duplicates and token loops), `message_record`, `report_region.replace_text`, `db.connect`, `snapshot_store.Store`. Never use `Gmail.list_ids` (silently truncates) or `apply_amazon`. The existing Gmail grant already includes modify, so read-only use needs no scope change. `v7_alerts` does not fit: it is notification dedupe, has no payload column, and belongs to Jobs.

**Design the scope settles.**
- Events: subject "New Mail" with an item count; subject "Completed Action Requests" with one or more lines naming a mail number and a verb (Scan, Shred, Recycle, Forward); anything else from the sender is counted and ignored. An empty id, unknown verb or zero lines is REVIEW with no guessed identity. Gmail read state is never read.
- Chain fold per mail number, ordered by time: Shred or Recycle is DONE and absorbing; Forward is waiting-for-tracking; Scan is non-terminal; contradictory evidence (Shred plus Recycle, Forward after Shred) is REVIEW, following `amazon/orders.py`.
- Unidentified arrivals: each first-seen mail number consumes one unit from the oldest arrival notice, so binding is by count, not identity. That is order-independent and idempotent. The pending-unidentified count is the sum of arrival counts minus the bound count. A first-seen mail number with nothing left to bind is tolerated only before an anchor date plus a grace period, and is otherwise DEGRADED.
- **Pure replay is not viable for the aggregate count.** It is exact only if the window starts at a quiet point and holds every still-pending arrival, and replay cannot prove that. Silent failures: items that age out of the window, notices deleted or filed as spam (the list excludes those), duplicate notices (same count, different id), and vendor disposal with no email. So the state is persisted as a derivative snapshot, and replay is used only for the overlap re-read (the D113 pattern).

**Persistence.** One private row through `Store` at a single key, saved with read-back, rebuildable by a recovery replay from an anchor (like `AMAZON_SINCE`). Terminal decisions still come from the events, so it is a derivative cache. It is a new table and therefore a schema change that needs approval. The job must live in `domains.yml` only, because its concurrency group gives the single-writer guarantee.

**Tracking, and the blocker.** Physical Mail should call `platform/carriers` directly (the shared client in `docs/DELIVERY_TRACKING_SCOPE.md`) and keep only carrier, number and last accepted status per chain. **The number's source is unproven**: no tracking-number email exists in 365 days of Gmail or in Outlook. Until Jim says where he sees it, the forward-to-tracking stage is `RUNTIME_PATH_INCOMPLETE_STOP`. Reading the vendor portal would be session automation, which the handoff forbids unless Jim approves it; it is not recommended.

**Smallest first slice.** PR 1: the full vertical slice through waiting-for-tracking (parser, chains, snapshot, card, router owner, `domains.yml` job), labelled `PARTIAL CANDIDATE, BLOCKED ON TRACKING FIXTURE` as the handoff itself prescribes. It does not depend on delivery tracking. PR 2: tracking parse, carrier call and closure, once a sanitized example and the carrier slice exist. Separate package for `life-os-automation`: the Production Contract delta (replace the unread-state rule, remove the Attention duplication, move region ownership to V7). It is not made from this workspace.

**Constraints for the code.** Build the vendor sender address with `chr(64)` as `amazon/events.py` does, because the privacy test rejects the vendor domain as a literal. Take the portal link from the "New Mail" body at runtime, validated as https on the vendor domain, and store it in the row so no location-identifying literal enters the repository. Add a test that the package never calls modify, label or relabel.

**Approvals.** One table; one secret (`MAIL_ALERTS_CARD_BLOCK_ID`) and the callout; a `mail` entry in `DOMAIN_SECRETS`; a router owner; optionally a `physical_mail` boolean and a `physical_mail_since` string input in `domains.yml` (never in `hourly.yml`); the Production Contract delta.

**Decisions for Jim.** (1) Where does he see the tracking number today (the vendor's email from another sender, another mailbox, or the portal only)? One sanitized example settles it. Is a portal read approved or denied? (2) Approve the table and private storage of tracking numbers (the V1 contract wording conflicts). (3) The anchor date after which the mailbox state is known; otherwise the first run is DEGRADED. (4) Aged items: display "older than N days, verify in the portal" (display only), or stay open indefinitely. (5) The exact live region heading (the handoff shows an emoji; V1 used plain text) and the card block id.

**Size.** Parser and chains M. Persistence and census M, plus approval. Card, router and job S to M. Tracking consume S once the example and engine exist. Contract delta S.

### 3.6 Delivery tracking (Amazon Orders expansion)

Fully scoped in `docs/DELIVERY_TRACKING_SCOPE.md`. In one line: carriers do not email Jim, so tracking numbers are discovered in merchant mail by a checksummed extractor and state comes from free carrier APIs polled to a terminal state; shipment state lives in domain-owned Hostinger tables; carrier clients live in `platform` so Physical Mail can reuse them. First slice: carrier access spike plus extractor and dry-run census, no schema.

## 4. Sequencing

Ordering rules used: ready before blocked; slices that unblock others first; each step is a vertical slice a person can see working, never a framework PR; non-code decision and spike work runs alongside because it has the longest lead time.

**Start now (no schema, no open product question blocks the slice)**

| Step | Slice | Why now | Size |
|---|---|---|---|
| 1 | Delivery tracking D0 carrier access spike, then D1 extractor and dry-run census | Carrier access is the one unproven dependency and it unblocks Physical Mail's tracking stage | S then M |
| 2 | MEG-0 decision session with Jim (definitions, canonical surface, Outreach rule) | The repositories cannot supply the definitions; every later MegIBOW slice waits on it | S (a conversation) |
| 3 | NET-0 offline spike | Longest lead: needs the real export, a private path and a refresh-source decision, none of which need production code | S |
| 4 | P2 alert active-set design | Small, and four features need it before any alert can ship | S |

**Then the vertical slices that need Jim's approvals first**

| Step | Slice | Needs from Jim | Unblocks |
|---|---|---|---|
| 5 | Physical Mail PR 1 (open chains through waiting-for-tracking) | Table approval, the callout, anchor date | Replaces the unread-state model; PR 2 later |
| 6 | Delivery tracking D2 (UPS end to end), then D3 carriers | Table approval, the callout, developer accounts, decisions 1 to 3 in the delivery scope | Physical Mail PR 2 |
| 7 | Hiring Pipeline PR 1 (the no-mail slice) | Region owner, snapshot table, row format | The visible pipeline; PR 2 later |
| 8 | Recruiters PR 1 | Scan breadth, qualification rule, a read-only check of the live Notion schema | P1 shared mail record for Outlook consumers |
| 9 | MegIBOW MEG-1a (Calendar census), then MEG-1b | MEG-0 outcome | MEG-2 onward |
| 10 | NET-1 seed import | NET-0 outcome, table approval | NET-2 re-export diff |

**Later, once their predecessors land:** Delivery tracking D4 (Outlook) and D5 to D7; Recruiters PR 2 and Hiring Pipeline PR 2 (mail evidence and the handoff between them); Physical Mail PR 2 (tracking); MegIBOW MEG-2 to MEG-6; NET-3 to NET-5.

**Why this order.** Delivery tracking goes first because it is the new request, needs no schema to start, and removes the blocker on Physical Mail. Physical Mail is next because it is small, fully specified and replaces behavior the owner has already said is wrong. Hiring Pipeline's no-mail slice gives the visible pipeline back without waiting for the mail classifier. Recruiters comes after the mail record is needed by more than one consumer. MegIBOW and Network Intelligence run as a second track because their first slices are independent and their open questions are product questions.

**Shared-file hotspots** if two slices are open at once: `lifeos/run.py`, `.github/workflows/domains.yml`, `platform/router.py`, `tests/contracts/test_workflow.py`, and the protected-region tuple in each existing card. These are one-line additions, so conflicts are cheap, but they are the reason the roadmap rule says one PR at a time.

## 5. Consolidated approvals

Each approval is asked when its slice starts, not all at once. This is the full list so nothing surprises anyone.

| Feature | New Hostinger tables | Notion or Daily Report | New secrets | Workflow and test edits | Other repository |
|---|---|---|---|---|---|
| Delivery tracking | `v7_shipments`, `v7_shipment_mail`, `v7_shipment_events`, `v7_carrier_calls` (added by slice) | A "Deliveries" callout Jim creates; router owner | Carrier credentials (UPS, FedEx, DHL, USPS), `DELIVERIES_CARD_BLOCK_ID` | `domains.yml` job, `DOMAIN_SECRETS` entry | Contract wording on tracking numbers |
| Physical Mail | One snapshot table | A Mail Alerts callout; router owner | `MAIL_ALERTS_CARD_BLOCK_ID` | `domains.yml` job, `mail` entry, optional inputs | Production Contract delta |
| Recruiters | None | Verify the live database schema; no change planned | `NOTION_RECRUITERS_TOKEN`, `NOTION_RECRUITERS_DATA_SOURCE_ID` | Manual `recruiters.yml`; contract test forbidding mail mutation | None |
| Hiring Pipeline | A derived snapshot table, if carry-forward is stored | A callout; the router test that pins the region unowned changes | `HIRING_CARD_BLOCK_ID` | `domains.yml` job, `DOMAIN_SECRETS`, protected tuple in four existing cards | None |
| MegIBOW | Later: weekly table through the snapshot store, then an accept table | A Notion surface and integration | A dashboard token and block id | `domains.yml` job, `DOMAIN_SECRETS`, an input or env var for week reconcile, a bridge change | None |
| Network Intelligence | People and positions (NET-1), events (NET-2) | A marker block appended to the Ledger page body (no property) | A seed secret only if the import runs on a runner | One step in `hourly.yml` `finish`; no input | None |

`hourly.yml` gains no input in any feature. `domains.yml` has five inputs and room for more.

## 6. Decisions Jim must make first

**Cross-cutting**

1. **Serial or two tracks.** Keep the rule of one PR at a time, or allow a mail-and-state track and a job-search-intelligence track (MegIBOW and Network Intelligence) to run side by side. Recommended: two tracks, because their first slices touch disjoint code.
2. **Which repository is active.** Confirm V7, and correct the statements in the V1 and V2 `CLAUDE.md` files. Both are Jim's edits in those repositories.
3. **Region ownership.** For each of Hiring Pipeline, Mail Alerts, Deliveries and the Recruiters view, does V7 own and write the region, or does the ChatGPT Daily Command Center? Recommended: V7 owns the four regions its code produces; Recruiters stays a Notion-native linked view with V7 writing only rows.
4. **Tracking numbers in private storage.** V1's production contract says never to persist "tracking tokens". Confirm carrier tracking numbers are allowed in the private database and have the wording clarified (a governance edit in `life-os-automation` that Jim must name explicitly).
5. **Shared primitives.** Agree that P1 (mail record) is created by the first slice that needs both providers (Recruiters PR 1), and that P2 (alert active-set) is designed before any feature adds a detector.

**The five I would answer first**, because they unblock the most:

- Delivery tracking: add USPS, and whether FedEx is worth creating an account for.
- Physical Mail: where Jim sees the forwarded tracking number today (one sanitized example settles it), and whether a portal read is allowed (recommended: no).
- MegIBOW: a short session on the five definitions, the canonical surface and the Outreach rule, including whether the "Wednesday 3 PM" check is MegIBOW at all.
- Hiring Pipeline: who owns the region.
- Network Intelligence: accept the periodic re-export diff as the refresh, and settle the private path for the seed file.

The remaining per-feature decisions are listed in each feature's section above and in `docs/DELIVERY_TRACKING_SCOPE.md`.

## 7. What was not verified

- Carrier developer pages were unreachable from this session's sandbox, so quotas and terms for UPS, FedEx, DHL and USPS come from search results and must be read on the live portals before any number is canonized (Delivery tracking slice D0).
- Mailboxes were inspected by count and category only. No message content, address or tracking number was read into any file. Extractor accuracy on real mail is therefore unmeasured; the design validates it indirectly, because a carrier that recognizes an extracted number proves the extraction.
- Nothing was run live. V1 and V2 production behavior is taken from their documents and code, not from observed runs.
- The per-feature inventories for Recruiters, MegIBOW, Hiring Pipeline, Network Intelligence and Physical Mail were produced by read-only code review. The claims this roadmap leans on (no classifier, no Recruiters secret, no MegIBOW code, no table writer, the unowned Hiring Pipeline region, the alert coupling) were re-checked directly.
