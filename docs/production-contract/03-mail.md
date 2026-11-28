# Production Contract part 3: Mail census and newsletter acquisition

<!-- Part of docs/life-os-production-contract.md (the index). Section text moved unchanged. -->

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
