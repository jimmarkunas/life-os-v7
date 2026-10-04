# Big features roadmap: one sequence (scoping only, no code)

Status: proposal for Jim's review. Nothing here is built, and this document changes no schema, secret, workflow or `docs/DECISIONS.md`.
Scope: Recruiters and Human Outreach, Automated MegIBOW, Network Intelligence, Hiring Pipeline, Physical Mail, Delivery tracking (the expansion of Amazon Orders), the company operating cockpit, and the Communications and Meeting program. Delivery tracking and Network Intelligence have their own deep scopes in `docs/DELIVERY_TRACKING_SCOPE.md` and `docs/NETWORK_INTELLIGENCE_SCOPE.md`; this file covers the rest and, above all, what they have in common.

Method: each feature handoff was checked against the current code on `main`, the claims that matter were re-verified directly, and the V1 and V2 repositories were read as references only. Real mailboxes were inspected by count and category only (`docs/PRIVACY.md`); no content is reproduced here.

Settled since the first draft: USPS is added to the carrier list; Jim holds active UPS and FedEx accounts; he receives few tracking numbers; the MegIBOW definitions arrived in a new brief taken from the Notion product page; and the periodic re-export diff is approved as the Network Intelligence refresh. Two more briefs arrived and are covered below: the company operating cockpit (3.7) and a Communications and Meeting roadmap update (3.8).

## 1. Findings that apply to every feature

1. **Repository routing conflict.** `AGENTS.md` in V7 says V7 is the active implementation and V1 and V2 are read-only references. The `CLAUDE.md` files in V1 and V2 still say V2 is the sole active repository. All six handoffs target V7, and V7 is the newest, so this roadmap treats V7 as active. The stale statement in the other two repositories is Jim's to correct; nothing was changed there.
2. **The handoffs assume mail infrastructure V7 does not have.** There is no provider-neutral mail record and no classifier (the only mention of a `human_hiring` class is the roadmap prompt that asks for one). V7 never reads mail from a sender it has not allowlisted: Newsletters and Amazon Orders both read exact senders only. Gmail returns a dict with the body as text; Outlook returns Microsoft Graph dicts with no body and no headers, and the body needs a second call. Recruiters, Hiring Pipeline, Physical Mail and Delivery tracking all need the same shared record.
3. **Alert state has a hidden coupling.** `lifeos/jobs/alerts.py` loads every open row from `v7_alerts` and `platform/alerts.reconcile` resolves any key the Jobs detector does not list. A second detector run from another stage would therefore silently resolve the Jobs alerts, and the reverse. Every feature that wants alerts is blocked on one small shared design: a combined active set, or key namespaces that each detector reconciles separately.
4. **Daily Report regions are text-only and ownership is strict.** `platform/router.py` has five owned regions. "Hiring Pipeline" is explicitly pinned as unowned by `tests/platform/test_router.py`, and "Mail Alerts" has no owner entry. There is no table writer in `lifeos/`; `report_region.replace_text` replaces text blocks and leaves tables alone. Each existing card also lists the other cards' regions in a protected tuple and digest-checks them, so adding a region means touching every existing card.
5. **Layering shapes where code can live.** OS packages import only `platform`; only `lifeos/sources/` may import across OS packages (`tests/contracts/test_boundaries.py`). So MegIBOW cannot import Interview or Jobs, Hiring Pipeline cannot import the Job Ledger or Interview readers, and anything that composes two OS packages sits in a `sources` adapter. Shared clients that two features need (for example carrier tracking) belong in `platform`.
6. **Every schema change stops work for Jim's approval.** Section 5 lists every table and secret the six features would add, so the approvals can be given together.
7. **`domains.yml`, not `hourly.yml`, is where new scheduled work goes.** It runs after each hourly tick, isolates secrets per job (enforced by `tests/contracts/test_workflow.py`, which needs an entry for each new job), and has room for more inputs. `hourly.yml` is at its 25-input limit.
8. **MegIBOW's host and meeting.** The live MegIBOW page puts its table in one bounded block inside the Jim section of the weekly accountability dashboard, the page behind the Wednesday 3 PM CT sync. Roadmap item 5 ("Accountability", the Jira load view) is a different product, but it appears to write to the same dashboard (to confirm), so two V7 writers would share one Notion page, each needing its own bounded region and a page-level digest (primitive P5).
9. **A serial rule meets parallel briefs.** `docs/ROADMAP.md` says one PR per item and do not start the next until the last is merged. The MegIBOW and Network Intelligence briefs ask for parallel tracks. Section 6, decision 1.
10. **Briefs versus live Notion canon.** Reading the live pages showed that two briefs differ from the canon in ways that change the design: MegIBOW (the canon's own call-precedence wording is inconsistent, and the feature is marked not yet promoted) and Network Intelligence (the canon names a Google Sheet as the canonical dataset and forbids a second connection database). Where they differ, the live canon governs unless Jim says otherwise (sections 3.2 and 3.3).

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

**Source of truth.** The live Notion page "Automated Megibow Dashboard" (approved future roadmap, last edited in mid-September) governs. It is more complete than the brief: it already fixes the definitions, the evidence rules, the review flow, the warning thresholds, the write boundary and sixteen acceptance scenarios. Its status is "decomposed backlog feature, not yet promoted": work starts only when Jim explicitly promotes it.

**What it specifies.**
- **Five measures, Jim only, Monday to Sunday:** Outreach, Scheduled, Networking Calls, Recruiter Calls, Company Calls. His accountability partner reviews the table with him and is never measured.
- **Outreach:** a human-written, job-search-relevant message Jim sent from an approved mailbox that advances a relationship or process, counted by sent time. Inbound, automated, application-confirmation, ordinary client mail and duplicates never count.
- **Scheduled:** a new qualifying meeting counted once when it is first formally booked, attributed to the booking date from the event's creation or first-confirmation time. A reschedule never counts again, and if the booking date cannot be proven the item goes to review.
- **Completed calls** are mutually exclusive: a Company Call (someone employed by or acting for a target company), a Recruiter Call (an independent or agency recruiter), or a Networking Call (a network, mentor, peer or alumni contact who is neither), otherwise review. A passed calendar event does not prove a call happened; occurrence needs strong evidence, otherwise "review: occurrence".
- **Evidence:** Gmail (outreach, scheduling corroboration), Calendar (scheduled and completed calls), hiring and interview context (classification help), and Jim's explicit corrections. Every candidate ends COUNTED, EXCLUDED, REVIEW NEEDED or DEGRADED; review items never count provisionally; missing evidence is never zero. A resolved contact classification may be reused for the same stable contact.
- **The table is the product:** a total row and the five measures, eight weekly columns (current plus prior seven) and a cumulative column. It lives in one bounded, machine-owned block inside the Jim Organizer section of the two-person accountability dashboard, and nothing outside that block may be rewritten; if the block cannot be found confidently the run is DEGRADED and nothing is broadly rewritten.
- **Current week** is recomputed from source evidence on every refresh (counts can go down), **closed weeks** are frozen at the Monday rollover, and late evidence only suggests a historical review that needs Jim's approval.
- **Warnings:** fully specified v1 rules: a baseline of the median of the prior seven completed weeks, at most three warnings in a fixed precedence, one corrective suggestion each, a quiet healthy line, and trend rules suppressed until four trustworthy weeks exist.
- **Freshness:** a successful refresh must exist before the Wednesday 3 PM CT sync.
- **Refuses to build:** a standalone activity database or ledger, a CRM, a new scheduler, a generic language-understanding platform, charts that replace the table, any tracking of the partner's activity, and any consequential source write.

**What the current sheet shows (structure only).** One member, about ten weeks of filled history and empty forward-dated columns, with a cumulative column and a total row that is the sum of the five measures. Its other two tabs are empty template tabs for a coaching framework and are not part of the product. Its definitions are the older, looser wording. **Its weekly columns are labelled by midweek dates (a Wednesday, then Thursdays, with one eight-day gap), so they are the weekly sync dates, not Monday-to-Sunday weeks.** History cannot be re-bucketed because no event-level data exists for it.

**V7 today.** Nothing: no code and no references. Calendar acquisition is partial: one shared Google calendar is read, Outlook reaches it through the bridge for the `personal` account only, the bridge copies no attendees, and a reschedule breaks logical identity because the bridged id hashes the start time. Gmail sent-mail acquisition is partial: a bounded sent query works, but there is no metadata-only reader and no recipients or thread id. The classifier, dedupe, review path, weekly projection, closed-week handling, warning engine and reconcile command are missing. V7 has no Notion table writer and no page-level protection digest.

**Findings from reading the live page against the brief and the repo.**
1. **The canon's call precedence contradicts itself.** The top of the page lists an independent recruiter first, then a target-company representative, then networking. Step 4 lists Company first, then Recruiter, then Networking. Acceptance scenario 5 says an agency recruiter discussing a client is a Recruiter Call, not a Company Call. Step 4 as worded would call that agency recruiter a Company Call, and the brief copied Step 4's order. Recommended reading: an independent or agency recruiter is a Recruiter Call even when discussing a target company; a Company Call is someone employed by or acting directly for the target company. Jim should correct the page so an implementer does not pick the wrong order.
2. **The Wednesday 3 PM CT check is real.** It is a freshness requirement before the weekly sync, and the same sync supports roadmap item 5's dashboard. My earlier suggestion that it might be a conflation was wrong.
3. **Two V7 writers may share one page.** Item 5 (Jira accountability) and MegIBOW both target the two-person dashboard. Each needs its own bounded block, a page-level digest to prove everything else unchanged, and the page shared with a Notion integration. Jim must create the MegIBOW block, because V7 never creates a missing block.
4. **Scheduler wording.** The page says to use the `LIFE OS Daily Runs` runtime and the production contract for run times, and forbids a MegIBOW scheduler. V7 would run it from its own tick, which holds only if the scheduler-authority question (section 6, item 4) is settled that way.
5. **The review loop has no input path yet.** The page shows review items with resolution buttons. V7 cannot receive a click; it can only read back what Jim edits (a checkbox or select inside the block) on the next run. The input form, and how the rewritten block preserves his input, need a design decision.
6. **Evidence gaps.** Bridged events carry no attendees, so "external human participant" cannot be tested; the Outlook event read needs attendee and creation-time fields; a bridged event's Google creation time reflects when the bridge copied it, not when Jim booked it, so Scheduled should use the source event's own creation time or fall to review; and "approved Jim mailbox" is plural, so Outlook sent mail is in scope and needs a sent-items reader with recipients.
7. **Migration.** Jim agreed to move to Notion. The page treats backfill beyond what is needed for a trustworthy starting table as out of scope, and the sheet's columns cannot be re-bucketed. Recommended: start the Notion table fresh at a cut-over Monday, seed the Cumulative column from the sheet's totals as a legacy figure, and keep the sheet read-only for history. Trend warnings then stay suppressed until four trustworthy completed weeks exist, which the page already specifies.

**Smallest first slice (after Jim promotes it).** MEG-1a: Monday-to-Sunday and eight-week windows plus a Calendar-only dry-run census with no classification. Its counts: events with an external attendee, with a usable creation time, bridged versus native, cancelled, declined, recurring, passed versus future. By count, that shows whether Scheduled and the call rules are computable from today's calendar data. One PR, no schema. MEG-1b: the sent-mail census (a Gmail metadata reader and Outlook sent items).

**Approvals later.** The Jim Organizer block (created by Jim) and a Notion integration shared with that page; a Notion table writer and a page digest; the minimal "exception state" the page allows for Jim's corrections (a small derived store only if the block itself cannot hold them); `DOMAIN_SECRETS`; Outlook event-read field additions; and an input or environment variable for week reconcile.

**Decisions still open.** (1) Promote it now? The page says not until Jim chooses. (2) Correct the call-precedence wording (recommended reading above). (3) The review and correction input form. (4) Start the table fresh with a legacy cumulative seed (recommended) or backfill. (5) Which Jim mailboxes count for Outreach, and whether the work Outlook mailbox is bridged. (6) Tentative responses, no-shows and declines, which the page does not cover. (7) Scheduler authority (section 6, item 4). Settled: the definitions, the move to Notion, the block location (per the page) and the Wednesday freshness requirement.

**Size.** MEG-1a S, MEG-1b M, MEG-2 L, MEG-3 M, MEG-4 M, MEG-5 M, MEG-6 S.

### 3.3 Network Intelligence

Fully scoped in `docs/NETWORK_INTELLIGENCE_SCOPE.md`, **revised after reading the live canon page** "LI Connection Database & Integration" (under the career-operations roadmap row). It names Jim's existing Google Sheet of connections as the canonical dataset, forbids a second connection database, treats a recommendation as derived context that Jim can dismiss, and lists four triggers. That conflicts with the brief's Hostinger temporal graph and with the local-import seed path Jim approved, which I had scoped without it.

**Revised plan.** Phase 1 reads the Sheet read-only through a small platform reader (the same service-account pattern as Calendar, no new dependency), matches on demand, and writes one owned block with a dismissal checkbox on the Ledger page. It needs no tables and no import. Phase 2, the "who moved" history the brief wanted, needs a derived Hostinger cache that the canon does not yet allow; Jim decides. First slice: a Sheet inspector, no schema.

**What stays from Jim's approvals.** The periodic re-export still works as the refresh (he refreshes the Sheet from a new export). The local import is superseded for Phase 1. The first slice's triggers are a qualified job (`ADMIT`, not `REVIEW`, by default) and an applied job; the canon's other two triggers (an interview scheduled, an explicit ask) need a surface decision because Hiring Pipeline pages are human-owned.

**Decisions open.** Confirm the revision; allow a Phase 2 cache or stop at Phase 1; share the Sheet with the Google service account and supply its identifier; confirm the glue in `sources/` and the appended-block surface; which fields show in Notion; a Jim-maintained alias list; erasure and retention if Phase 2 happens.

**Size.** NET-1a S to M, NET-3 M, NET-4 M, then Phase 2: NET-1b M, NET-2 M, NET-5 M to L, NET-6 L.

### 3.4 Hiring Pipeline

**V7 today.** `lifeos/jobs/hiring_pipeline.py` is a read-only protection check for Jobs and stays as it is. Interview parent and round creation (identity, protection, read-back) exists but has no live evidence feeder; its only producer is a manual secret in the acceptance harness. `interview/stage.py` is the creation runner, not a stage vocabulary. None of V1's ten stages or its action classes exists in code, and Notion has no stage field. In the Job Ledger, Applied is a human checkbox plus a date, and the lifecycle field was removed, so "application lifecycle" is a binary Applied. There is no region owner, no table writer, and no calendar-to-opportunity matching.

**Where the handoff and the repo differ.** The handoff expects to reuse an Interview stage state; there is none. The V1 stage list exists only in contract text. The Recruiters boundary is a rule only, because Recruiters has no code. The no-renderer claim is verified: V1's four-column table lives only in the archive renderer.

**Smallest first slice, PR 1 (no mail).** Job Ledger Applied rows, Notion parents and rounds (read-only), a Calendar window and a prior-state snapshot feed one pure compiler, which feeds one card. It works on its own and Notion Hiring Pipeline pages stay read-only, so human notes are untouched by construction. Files: `lifeos/hiring/{models,compile,render,snapshot,card}.py`, a `lifeos/sources/hiring.py` adapter (the only layer allowed to import the Ledger and Interview readers), two `run.py` lines, a router entry, the protected tuple in each existing card, a `domains.yml` job with `needs:`, tests and a fake. **PR 2:** accepted-evidence acquisition (classifier, extractor, Recruiters boundary), which also finally feeds Interview creation.

**Approvals.** A `HIRING_CARD_BLOCK_ID` secret and a callout Jim creates; a derived snapshot table if carry-forward is stored (decision 3); `router.OWNERS` and its test; a `domains.yml` job and `DOMAIN_SECRETS` entry. No Notion schema change. `hourly.yml` stays unchanged.

**Decisions for Jim.** (1) Settled by Jim: V7 owns and fills the Hiring Pipeline callout (a router change); ChatGPT may add commentary elsewhere. (2) The source of accepted mail evidence under the deterministic-only rule: a V7 parser, a ChatGPT insert-only queue, or none in version one. (3) A private snapshot table for carry-forward, or re-parse the previous callout. (4) Table rows or bullet rows (a table needs a new writer). (5) A time limit for waiting states, and whether a Retired Notion page means closed. (6) One acquire step carrying the Jobs, Interview and Calendar secrets together, or one step per secret.

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

**Update from Jim.** He receives few tracking numbers, so the forward-to-tracking stage (PR 2) is the lowest priority; PR 1, the open-chain tracker, is the valuable part. The next time a mail item is forwarded, noting where the number appears (an email, the portal, or nowhere) settles the open question. Until then, forwarded items sit in waiting-for-tracking and show the aged-item note after the chosen number of days.

### 3.6 Delivery tracking (Amazon Orders expansion)

Fully scoped in `docs/DELIVERY_TRACKING_SCOPE.md`. In one line: carriers do not email Jim, so tracking numbers are discovered in merchant mail by a checksummed extractor and state comes from free carrier APIs polled to a terminal state; shipment state lives in domain-owned Hostinger tables; carrier clients live in `platform` so Physical Mail can reuse them.

**Settled by Jim:** USPS is in; he holds active UPS and FedEx accounts, so production credentials are available for both; and tracking numbers arrive rarely. Because volume is low, the schema starts at two tables, and the first slice (carrier access spike plus extractor and dry-run census) doubles as the go/no-go: its count of real tracking numbers over about 90 days decides whether the store and card are worth building. First slice: no schema.

### 3.7 Company operating cockpit

**What it is.** A Notion page that already exists, with three presentation callouts (client delivery, business operations, product), to be filled from deterministic evidence across Jira, Calendar, Outlook and Notion, written by V7 with no language model at runtime. The handoff asks for implementation now; this session is scoping, so I assessed it rather than built it. The employer and client are deliberately not named here: `docs/PRIVACY.md` forbids it in this public repository, and callout titles will need a neutral or configured form for the same reason.

**Verdict.** The runtime path is complete for a slice built from the saved Jira and Calendar snapshots. It is not complete for the handoff's literal "full-source branch at 6, 9, 12 and 6 Central, presentation-only between" Outlook acquisition. Those slots belong to the ChatGPT automation that the production contract names as its owner; V7's domain jobs run after every hourly tick, and no V7 job is told which slot it is. The two authorities also disagree: the production contract says one scheduler, while V7's D25 and D58 describe two independent ones. I did not pick a side. The handoff's own hard stop applies to that part only.

**Where the handoff and the repo differ.**
- A cockpit package cannot import `jira` or `agenda`; an adapter in `lifeos/sources/` is needed.
- Public-repo rules forbid names and addresses in code and tests, the privacy test rejects the mailbox domains, and `router.OWNERS` is keyed by literal heading text.
- The existing Notion card integration is shared with the Daily Report page only; the cockpit page needs an integration shared with it.
- `report_region.replace_text` needs each callout's first child to be a heading equal to its title, and its sibling-protection digest accepts callouts only, so a page-children digest is needed to prove the title, intro and operating-model text unchanged. The page's real structure is unverified.
- The Calendar snapshot covers only today and tomorrow and keeps no attendees. The Jira snapshot has no labels, flags or links; "blocked" means a status named blocked; the epic is only the immediate parent.
- Outlook's message field set has no recipients, sent time or preview, and the platform has no HTML-to-text. Listing Sent Items works unchanged, and an optional fields argument is safer than editing the constant the newsletter stage shares.

**What is credible without a language model.** Credible: sender and recipient classification against configured address and domain sets; Jira due, overdue and blocked rules; calendar title matches; health with DEGRADED handling; a fixed precedence for the single "needs Jim" item; and thread state (the last message in a conversation is from the client with no later reply in Sent Items, or the reverse). Not credible: extracting a promise or ask from email text (quoted replies, relative dates, negation), detecting that a promise was fulfilled, "promise with no Jira item" (a missing Jira key does not prove missing work, and fuzzy matching is forbidden), waiting-on-client from phrasing, disposition of client requests as product or client-specific, and open business decisions. Those need a human-owned marker in Jira or Notion.

**Smallest first slice (one PR, no email at all).** The client-delivery callout only, from the saved Jira and agenda snapshots: health (RED when overdue or blocked, DEGRADED when a snapshot is stale or missing, GREEN only from fresh complete evidence), the active sprint as the milestone, the nearest due open item, and one "needs Jim" chosen by precedence among the classes Jira can supply. "Waiting on the client" is reported as not resolved. The other two callouts are untouched and protected by a page digest. Files: a small cockpit package, a `lifeos/sources/` adapter, a page digest in `report_region`, a router entry, one `run.py` line, one `domains.yml` job (`needs: [jira, agenda]`), a `DOMAIN_SECRETS` entry, fakes and tests, a decision entry, SETUP. Slice 2 is Outlook thread-state evidence. Text commitment extraction is not recommended.

**Approvals.** Slice 1: no table, no workflow input; secrets for the callout block ids and one config secret (project keys, calendar keywords, titles); a Notion access decision. Slice 2: a snapshot table (a schema change) and secrets holding Jim's addresses and the client's domains, kept out of the repo. Jira lenses scoped by epic or label need a snapshot field addition and a schema-version bump. The handoff's contract delta belongs in `life-os-automation` and is not made from here.

**Decisions for Jim.** (1) Cadence: acquire Outlook on every tick like the other domains (recommended), or enshrine four daily hours through a contract change. (2) Neutral callout titles or a runtime title config. (3) Share the existing Notion integration with the page, or add a new one. (4) Whether each callout opens with a child heading. (5) Which Jira project or epic is each lens, and whether a "waiting on client" status exists. (6) Which Notion pages or markers count as an approved decision. (7) Calendar: title keywords, or extend the snapshot with attendees. (8) Which account label holds the mailbox. (9) Settled by Jim: scope only; another Claude session or Codex implements.

**Size.** Slice 1 M. Business and product callouts from Jira M (needs a snapshot bump). Slice 2 M to L, plus table approval. Text extraction L, not recommended. Reading Notion canon L, blocked on decision 6.

**Hand-off to the implementer.** Settled by Jim: this is scoped only, and another Claude session (or Codex) will build it. Give that session three things: the original handoff, this section, and the corrections listed above. Slice 1 needs no cadence answer, because it renders from saved snapshots and never branches; build it first and settle decision 1 before slice 2. The real names and addresses go in as configuration secrets at implementation time and never in the repository.

### 3.8 Communications and Meeting program (Notion roadmap update)

**What the brief is.** A roadmap and canon alignment package for the Notion Product Backlog, not V7 code: OpenClaw as a local-model and transport edge gateway; a Communications Attention Hub (channel-level attention and counts with deep links, not a cloned inbox); Telegram as the first two-way channel; People Identity as the cross-source foundation; content intelligence only for individually approved channels; Meeting Intelligence kept separate (a Granola bridge first, native capture with local Whisper later); and a Followup layer downstream.

**What it means for V7.**
1. **Runtime boundary.** V7 runs on GitHub Actions, so signals that exist only on Jim's Mac (Messages, FaceTime, WhatsApp, a local transcript cache, local speech-to-text) are out of its reach. An edge component must deliver them, and the brief assigns presentation to the ChatGPT Daily Command Center. How anything would cross into a V7-owned region without a V7 scheduler or datastore is unanswered, and only matters if V7 is to render it.
2. **Keep Mail Alerts separate.** Physical Mail's region is a lifecycle tracker for open items, not a channel counter. The Attention Hub may list mail as a channel only through its own proven attention adapters.
3. **Shared pieces.** The shared mail record (P1) is the natural input for any mail channel's Message Evidence. People Identity will later be the cross-source resolver, so Network Intelligence keeps its identity to profile addresses and its `person_key` stays linkable rather than becoming a second resolver.
4. **Meeting evidence helps MegIBOW later.** Occurrence evidence for completed calls includes transcripts and meeting notes, which the meeting pipeline would supply. MegIBOW can ship first on calendar-only occurrence rules and gain that evidence later.

**Notion update: applied and verified.** Jim approved it. An agent applied sixteen edits with a read-back after each, and I re-read the Product Backlog and the Communications canon myself afterwards. Only those two pages changed.
- **Product Backlog:** outcome edits to OPENCLAW-0, OPENCLAW-1, PEOPLE-1, COMM-INGEST-1, COMM-1 (now the Communications Attention Hub), MEET-1A (a note separating it from missed-call awareness) and TASK-1 (OpenClaw never owns the task store). COMM-1's gate changed in exactly one clause: the accepted-ingestion-channel requirement became "at least one channel with a proven attention-level entry in the channel capability matrix". Two new bullets in Current position (the boundary decision and the local-model line), and the Post-Jobs sequence bullet gained the intended priority order, marked as an order and not a hard chain. No status changed, no other gate changed, no row was added, and the table still has 28 rows and 5 columns.
- **Communications canon:** the Attention Hub framing; the COMM-1 gate wording in sections 1 and 22; a channel capability matrix header in section 7; new Messages, FaceTime and Discord subsections; FaceTime and Discord added to the channel list, deep-link rules and non-goals; and the local-model sentence in the OpenClaw boundary. The Platform canon and Interview OS were not changed.

**Left for Jim (not edited).** Two canon sentences now sit in mild tension with the new gate: section 22 still says presentation is "downstream of ingestion and never blocks it", and section 23 still states the older canonical sequence (`OPENCLAW-0/1 → PEOPLE-1 → COMM-INGEST-1 → COMM-1`). Also still open from the review: whether Gmail and Outlook stay the first proof channels; whether the iMessage donor can give counts without reading message bodies (unproven); whether TASK-1 gains capture scope; what "Discord" means in SEARCH-1; and whether the cockpit gets a backlog row. A pre-existing numbering slip (two sections numbered 24) is cosmetic.

## 4. Sequencing

Ordering rules: ready before blocked; slices that unblock others first; each step is a vertical slice a person can see working, never a framework PR; evidence-gathering slices run early because they cost nothing and decide whether later work is worth doing.

**Start now (no schema, and no open product question blocks the slice)**

| Step | Slice | Why now | Size |
|---|---|---|---|
| 1 | Delivery tracking D0 access spike, then D1 extractor and dry-run census | Carrier access is the one unproven dependency. With few tracking numbers expected, the census count is also the go/no-go for building D2 | S then M |
| 2 | NET-1a Sheet inspector (a read-only Sheets reader plus a dry-run parser) | No table; proves V7 can read the canonical connections Sheet and shows its real shape by count. Needs the Sheet shared with the Google service account | S to M |
| 3 | MEG-1a calendar census | Shows by count whether Scheduled and the call rules are computable from today's calendar data (attendees, creation times). Needs Jim's explicit promotion first: the live page is marked not yet promoted | S |
| 4 | P2 alert active-set design | Small, and every feature that wants an alert needs it | S |

**Then the vertical slices that need Jim's answers or approvals first**

| Step | Slice | Needs from Jim | Unblocks |
|---|---|---|---|
| 5 | Physical Mail PR 1 (open chains through waiting-for-tracking) | Table approval, the callout, an anchor date | Replaces the unread-state model |
| 6 | Hiring Pipeline PR 1 (no mail) | Snapshot table and row format (region owner settled: V7) | The visible pipeline |
| 7 | Company cockpit slice 1 (client-delivery callout from saved snapshots) | Decisions 2 to 5 in section 3.7 (scoped only; another session implements) | Slice 2 (Outlook thread state) |
| 8 | Recruiters PR 1 | Scan breadth, qualification rule, a read-only check of the live Notion schema | P1 shared mail record |
| 9 | NET-3 then NET-4 (match, then surface on the Ledger page) | NET-1a outcome and the revised-plan confirmation | Phase 2 (NET-1b, NET-2) only if Jim allows a derived cache |
| 10 | MEG-1b, then MEG-2 | Promotion, the review-input form, the mailbox decision and the call-precedence correction (section 3.2) | MEG-3 onward |
| 11 | Delivery tracking D2 and D3 | Only if the census justifies it; table approval and the callout | Physical Mail PR 2 |

**Later, once their predecessors land:** Delivery D4 to D7; Recruiters PR 2 and Hiring Pipeline PR 2 (mail evidence and the handoff between them); Physical Mail PR 2 (tracking; lowest priority and blocked on a real example); MEG-3 to MEG-6; NET-5 and the Phase 2 cache; cockpit slice 2. The Communications and Meeting program stays outside V7 until a design exists for crossing the Mac-to-GitHub boundary.

**Why this order.** The first four steps cost no schema and answer real questions by count: whether delivery tracking is worth building at current volume, what the connections export actually contains, and whether MegIBOW's rules can be computed from the calendar. Physical Mail, the Hiring Pipeline and the cockpit then restore visible daily surfaces. Recruiters follows because more than one consumer then needs the shared mail record. MegIBOW and Network Intelligence form a second track because their first slices are independent and their open questions are product questions.

**Shared-file hotspots** if two slices are open at once: `lifeos/run.py`, `.github/workflows/domains.yml`, `platform/router.py`, `tests/contracts/test_workflow.py`, `platform/report_region.py`, and the protected-region tuple in each existing card. They are small additions, so conflicts are cheap, but they are why the roadmap rule says one PR at a time.

## 5. Consolidated approvals

Each approval is asked when its slice starts, not all at once. This is the full list so nothing surprises anyone.

| Feature | New Hostinger tables | Notion or Daily Report | New secrets | Workflow and test edits | Other repository |
|---|---|---|---|---|---|
| Delivery tracking | `v7_shipments`, `v7_shipment_mail` (two to start) | A "Deliveries" callout Jim creates; router owner | UPS, FedEx and USPS credentials (DHL later), `DELIVERIES_CARD_BLOCK_ID` | `domains.yml` job, `DOMAIN_SECRETS` entry | Contract wording on tracking numbers |
| Physical Mail | One snapshot table | A Mail Alerts callout; router owner | `MAIL_ALERTS_CARD_BLOCK_ID` | `domains.yml` job, `mail` entry, optional inputs | Production Contract delta |
| Recruiters | None | Verify the live database schema; no change planned | `NOTION_RECRUITERS_TOKEN`, `NOTION_RECRUITERS_DATA_SOURCE_ID` | Manual `recruiters.yml`; contract test forbidding mail mutation | None |
| Hiring Pipeline | A derived snapshot table, if carry-forward is stored | A callout; the router test that pins the region unowned changes | `HIRING_CARD_BLOCK_ID` | `domains.yml` job, `DOMAIN_SECRETS`, protected tuple in four existing cards | None |
| MegIBOW | Possibly none (closed weeks live in the Notion table); a small derived store only if the block cannot hold Jim's corrections | One bounded block in the Jim Organizer section of the accountability dashboard, created by Jim, and a Notion integration shared with that page | A dashboard page or block id and token | `domains.yml` job, `DOMAIN_SECRETS`, a Notion table writer and page digest, Outlook event-read field additions, an input or env var for week reconcile | None |
| Network Intelligence | Phase 1 none; Phase 2 only (a derived cache: people, positions, events) | A marker block with a dismissal checkbox on the Ledger page body (no property); the connections Sheet shared with the service account | The Sheet identifier; the Google service-account secret in the `finish` job if absent | One step in `hourly.yml` `finish`; a read-only Sheets reader in `platform`; a transaction helper in `platform/db.py` for Phase 2 | None |
| Company cockpit | Slice 1 none; slice 2 one snapshot table | The cockpit page shared with an integration; callout structure confirmed | Three block ids, a config secret, a token decision | `domains.yml` job with `needs: [jira, agenda]`, `DOMAIN_SECRETS`, a page digest in `report_region` | Contract delta in `life-os-automation` |
| Communications and Meeting program | None in V7 | Notion roadmap edits only, pending Jim's go | None | None | None |

`hourly.yml` gains no input in any feature, except one non-input step in `finish` for Network Intelligence. `domains.yml` has five inputs and room for more.

## 6. Decisions Jim must make

**Settled since the first draft:** USPS in; UPS and FedEx accounts available; the Network Intelligence refresh by periodic re-export (its seed path is superseded by the revised plan); the Hiring Pipeline region is V7's; MegIBOW definitions supplied and the table moves to Notion; the Notion roadmap update approved; the company cockpit is scoped only.

**Cross-cutting**

1. **Serial or two tracks.** Keep one PR at a time, or allow a mail-and-state track beside a job-search-intelligence track (MegIBOW and Network Intelligence). Recommended: two tracks, because their first slices touch disjoint code.
2. **Which repository is active.** Confirm V7 and correct the statements in the V1 and V2 `CLAUDE.md` files (Jim's edits in those repositories).
3. **Region ownership** (the plain version): the Daily Report is one Notion page made of boxes, and each box has exactly one writer. V7's code writes Jira, Calendar, Bills and Amazon, and ChatGPT's Daily Command Center writes its own box. The Hiring Pipeline and Mail Alerts boxes have no writer today. For each of Hiring Pipeline, Mail Alerts and Deliveries: should V7 fill it, or should it hand data to ChatGPT? V7-owned works when ChatGPT is down and is built from rules, with plainer wording than the prose ChatGPT wrote in V1; ChatGPT-owned reads better but goes stale when ChatGPT is down. **Settled for the Hiring Pipeline box: V7 fills it** (Jim approved). The same rule is proposed for Mail Alerts and Deliveries, since V7's code produces them; Recruiters stays a Notion-native linked view.
4. **Scheduler authority.** The production contract says one scheduler (the ChatGPT automation); V7's D25 and D58 say two independent ones. Several slices (the cockpit's cadence especially) depend on which governs. Recommended: keep V7's own tick as the runtime and correct the contract wording.
5. **Tracking numbers in private storage.** V1's contract says never to persist "tracking tokens". Confirm carrier tracking numbers are allowed in the private database and have the wording clarified (a governance edit in `life-os-automation` that Jim must name explicitly).
6. **Shared primitives.** Agree that P1 (mail record) is created by the first slice that needs both providers (Recruiters PR 1), and that P2 (alert active-set) is designed before any feature adds a detector.

**What I need next:**
- Network Intelligence: confirm the revised two-phase plan (read the canonical Sheet directly; the local import you approved is no longer needed) and whether a derived Hostinger cache for "who moved" is allowed.
- MegIBOW: whether to promote it now (the page says not yet), the call-precedence correction, the review-input form, and a fresh start with a legacy cumulative seed.
- The two Communications canon sentences left in tension (sections 22 and 23).
- Scheduler authority (item 4 above), which several slices depend on.
- Physical Mail: an anchor date, and where the tracking number shows up the next time something is forwarded.
- Delivery tracking: carrier developer credentials, and the live-portal research results.

Per-feature decisions are listed in each section above and in the two deep scopes.

## 7. What was not verified

- Carrier developer pages were unreachable from this session's sandbox, so quotas and terms for UPS, FedEx, DHL and USPS come from search results and must be read on the live portals before any number is canonized (Delivery tracking slice D0). A research prompt for ChatGPT was supplied to Jim for that.
- Mailboxes were inspected by count and category only. No message content, address or tracking number was read into any file. Extractor accuracy on real mail is therefore unmeasured; the design validates it indirectly, because a carrier that recognizes an extracted number proves the extraction.
- The MegIBOW and LI Connection Notion pages were read directly, as last edited in September. I did not inspect the cockpit page, the real structure of the accountability dashboard, or the Sheet's sharing settings.
- The Notion roadmap update was applied by an agent with a read-back per edit. I re-read the Product Backlog and the Communications canon myself and confirmed the reported edits; I did not re-read the Platform canon or Interview OS, which were not edited.
- Nothing was run live. V1 and V2 production behavior is taken from their documents and code, not from observed runs.
- Per-feature inventories were produced by read-only code review. The claims this roadmap leans on (no classifier, no Recruiters secret, no MegIBOW code, no table writer, the unowned Hiring Pipeline region, the alert coupling, the Jobs trigger state, the scheduler-slot ownership, the Hiring Pipeline claims) were re-checked directly. One agent's line citations did not fit the files; its claims were re-verified and held.
