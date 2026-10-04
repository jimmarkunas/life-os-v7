# Network Intelligence: scope and roadmap (scoping only, no code)

Status: scoped for Jim's review. Nothing here is built, and this document changes no schema, secret, workflow or `docs/DECISIONS.md`.
**Settled by Jim:** the data lives in a **separate table in the Hostinger database already used for Jobs OS**; everything runs in the cloud and is automated, with no local files, no commands and no lists he maintains; **ChatGPT keeps the data fresh through its connected LinkedIn app and its Drive access (the brief's "living system")**; learned company aliases and "no correction command" are approved; V7 does **not** touch Drive. Assumed defaults, change on request: ChatGPT hands its findings to V7 through a small private Notion exchange (section N); the first slice's triggers are a qualified job (`ADMIT`) and an applied job. Still open: section O.

## 0. The shape of it in one paragraph

Network Intelligence is a small temporal people graph in Hostinger (tables beside the Jobs tables) that makes Jobs OS smarter: when a job is admitted, it lists the few people in Jim's network who currently or formerly list that employer, with the evidence and its age. **ChatGPT keeps the facts fresh; V7 keeps the tables and does the matching.** The brief names the fresh-data source: ChatGPT's connected LinkedIn app, which looks up a known person by name and returns their current professional state. ChatGPT can also read Jim's connections Sheet in Drive. V7 cannot call either one (it runs on GitHub Actions, and a chat app's connector is not reachable from there, which is why V7 must run with ChatGPT unavailable, D54). So the two hand off through a small private Notion page, the same pattern the production contract already uses for ChatGPT's browser evidence: V7 writes down who it wants checked (job-driven: people at companies in admitted jobs, plus stale top leads), ChatGPT looks them up and writes back what it found with the date, and V7 checks it, records it and any change (company changed, title changed), and matches. If ChatGPT is down, the tables simply age and every lead says how old it is. V7 never reads Drive, and nobody downloads a file, runs a command or edits a list. Every claim carries its source and the date it was observed; nothing is inferred about relationship strength.

What this fixes from earlier drafts: I had treated a fresh LinkedIn export as the only refresh and said no cloud job could get one. The brief's own answer was a live per-person lookup through ChatGPT's connected app, and I had set it aside because V7 cannot reach it directly. The handoff removes that objection without making ChatGPT the datastore (the brief: "Do not make ChatGPT the network datastore").

## 0a. The live Notion canon, re-read, and what I got wrong

I first said the Notion page "LI Connection Database & Integration" forbids a separate connection database and needs editing. **That overstated it.** Re-read in full, it says:

- **Canonical dataset:** Jim's existing Google Sheet of connections (a dated export, in his Drive). Still true: the Sheet stays where Jim's data lives. ChatGPT, which already has Drive access, is the one that reads it, as the page's original workflow assumed.
- **"LIFE OS may read and normalize the Sheet for matching/search"**, and **must not create "a second CRM or independently editable connection database merely for this feature."** Tables that V7 fills automatically from observations ChatGPT supplies, that nobody edits by hand, and that can be rebuilt by asking again are a normalization, not an independently editable database. This design keeps to that: Jim's only inputs are decisions (a dismissal, a "same company" confirmation), never data.
- **Four triggers:** a newly qualified open job in the US Remote or Scale-Up experience; Jim moves an opportunity into active pursuit or applied; an interview is scheduled or confirmed; Jim explicitly asks. Section H.
- **Matching:** normalized employer name and known aliases, ranked only by context actually in the data. Never infer closeness, influence or willingness to refer. Section H.
- **Output:** a compact shortlist: person, current role and company, why relevant, which opportunity, a recommended networking objective, a suggested next action (draft outreach, open LinkedIn, dismiss), and the data's freshness. Section J.
- **Lifecycle:** a recommendation is derived career context, not a new durable person record; dismissing one suppresses the same unchanged recommendation for the same opportunity; new evidence brings it back. Sections E and J.
- **Stale or unavailable data** is shown as a limitation. **Non-goals:** a second CRM, scraping beyond Jim's supplied data, an inferred social graph, automatic messaging, any automatic claim that a connection can refer him.

**What is still worth adding to that page.** The Platform Canon allows a "bounded noncanonical machine-state store" only when Jim explicitly approves it and **the owning domain canon defines its scope.** Jim has approved it in this session; the LI page does not yet define the scope. Suggested clause: "Network matching and change history run from derived Hostinger tables that V7 fills from observations ChatGPT supplies through a private exchange (the Sheet and the connected LinkedIn app). The Sheet stays canonical for who is connected; the tables are rebuildable by asking again except the change history, hold no hand-edited data, and store only dismissals and company-name confirmations as Jim's own decisions." **On hold at Jim's request**; nothing on the page changes until he says so.

**What the real file looks like (read through the Drive connector's preview only; no row content is kept anywhere in this repository).** The Sheet "LinkedIn Connections" for the 2026-09-07 export sits in Jim's LIFE OS Drive folder, about 180 KB, with LinkedIn's standard seven columns: first name, last name, profile URL, email address, company, position, connected on. Three things the preview showed that shape the design: names carry credentials, emoji and commas; some company values are the person's own name or "freelance / independent" wording (not employers, so they must never match a job); and a few rows carry an email address. The inspector slice measures each of these by count.

## A. Current-state architecture map (what exists today)

| Concern | Where | State |
|---|---|---|
| Network code | `lifeos/network/` | Does not exist. No people or contact code anywhere in V7. |
| Hostinger access | `lifeos/platform/db.py` | SSH tunnel plus MySQL with retry and fixed codes. Autocommit is on and there is no transaction or upsert helper. |
| Schema ownership | `lifeos/jobs/store.py` (Jobs), `lifeos/jira/store.py`, `platform/outlook_tokens.py` | Each OS owns its tables, with `CREATE TABLE IF NOT EXISTS` plus an information-schema check for added columns. Stages call `ensure_schema` even on dry runs, so a network stage must create tables only when live. |
| Company normalization | `lifeos/platform/names.py` | `core(name)` drops legal suffixes and trailing generic words (so "Acme Technologies, Inc." gives the key `acme`). It is a comparator toolkit with no key generator, and `same_company` uses a prefix rule that is not transitive, so it must not be used as a key. A second, simpler normalizer lives in `jobs/identity.py`. |
| Admission | `v7_job_fit.admission` (`ADMIT`, `REVIEW`, `EXCLUDE`) | Stored on every scored job even when the gate is off. |
| Publish | `lifeos/jobs/publish.py`, `readback.py`, `ledger.py`, `guard.py` | Creates each Ledger page once. `REVIEW` jobs also publish. Read-back checks only the first three body blocks; `ledger.verify` checks the target data source before any write. |
| Page-block write safety | `lifeos/platform/report_region.py` | Owned-block insert, read-back, and digest checks of neighbouring regions. The model for a Network Leads writer, but it is written for Daily Report callouts, not page bodies. |
| Scheduling | `.github/workflows/hourly.yml` `finish` job | Has a `publish` step (continue-on-error) and a lane-failure list that does not include unlisted steps, so an added step is non-fatal. `hourly.yml` is at its 25-input limit. |
| LinkedIn | job resolver in `lifeos/jobs/resolve/` | Job-only, guest endpoint by job id, never logged in. No profile or people fetch. |
| Layering | `tests/contracts/test_boundaries.py` | An OS package imports `platform` only; only `lifeos/sources/` may import across OS packages. So `jobs` cannot import `network`. |

## B. Gap analysis

| Capability | Status | Note |
|---|---|---|
| People and positions store | MISSING | |
| Evidence validator and importer (from the exchange) | MISSING | |
| Person identity (profile-URL key, collision handling) | MISSING | |
| Company key | PARTIAL | `core()` is reusable; a key built as the joined core tokens needs no platform change. |
| Transactional write | MISSING | A tiny platform helper is needed so a position change and its event commit together. |
| Change detection and events | MISSING | |
| Freshness policy | MISSING | |
| Live current-state source | MISSING in V7; exists in ChatGPT | The connected LinkedIn app (targeted lookups by name). V7 cannot call it, so it arrives through the Notion exchange (section N). |
| Job-to-network match | MISSING | |
| Surface on the job page | MISSING | No body-append writer exists; `report_region` is the nearest. |
| Trigger signal | EXISTS | Published job with a page id, a verified time and `admission = ADMIT`. |
| Non-fatal scheduling slot | EXISTS | The `finish` job after `publish`. |
| Alerts | PARTIAL | Layer exists; coupling with Jobs alerts needs the shared fix in `docs/BIG_FEATURES_ROADMAP.md`. |

## C. Final architecture

```text
ChatGPT (its own scope; its Drive access + connected LinkedIn app)
   reads the open REQUEST rows, searches Jim's connections Sheet by company, verifies current company and title of each match
   |  writes EVIDENCE rows and closes the request                    ^  V7 writes REQUEST rows (who to check, why, how many)
   v                                                                |
Private Notion "Network Exchange" database  <---------------------+
   |  V7 tick (`finish` job, non-fatal): reads new EVIDENCE rows
   v
validate (fixed rules; a full result or an error, never partial) -> identity (profile-URL key) + company key
   v
reconcile against stored positions  ->  same | title changed | company changed | new person | ambiguous
   v
Hostinger: v7_network_people, v7_network_positions, v7_network_events, v7_network_batches     (one transaction per person)
   |
   +----> change events -> one push line per import + private digest
   |
   +----> match(job)  <---- admitted, published jobs (read through a sources adapter; reads Hostinger only)
               v
        NetworkLead list (at most 5, each with reasons and freshness)
               v
   marker-owned "Network Leads" block at the end of the Ledger page body (Dismiss and Same-company ticks)
```

Placement: `lifeos/network/` (identity, store, exchange, reconcile, requests, match, render) imports `platform` only. The stage that reads admitted jobs and calls `network.match` and `network.requests` lives in `lifeos/sources/` (it may import `jobs` and `network`), so neither OS imports the other and the D17 boundary stays as it is. **No Google client of any kind is added to V7.** The Notion calls reuse `notion_client` and the property-checking pattern of the Amazon Orders rows; all three V7 steps (read evidence, write requests, surface leads) run in the existing hourly `finish` job as non-fatal steps, where the Notion and database settings already are.

## D. Source feasibility

| Source | Viable | Gives | Bulk or targeted | Auth and cost | MVP |
|---|---|---|---|---|---|
| ChatGPT's connected LinkedIn app, run by ChatGPT | Yes, per the brief ("targeted current-state resolver"); capability and limits unverified from here | Current company and title for a named person | Targeted, job-driven | Jim's existing ChatGPT connection; no new cost | **MVP: the live refresh** |
| Jim's LinkedIn connections Sheet in Drive, read by ChatGPT | Yes; the canon page's own workflow | Who is connected, with company, position and connection date as of the export | Searched by company, not copied wholesale | ChatGPT's existing Drive access | **MVP: the roster** |
| The Notion exchange (V7 and ChatGPT's private meeting point) | Yes; the same pattern as the contract's browser-evidence handoff | Requests down, evidence up | Small batches | A private Notion database shared with both | **MVP: the handoff** |
| LinkedIn through V7 directly | No | | | | Reject: V7 cannot reach a chat connector from GitHub Actions (D54), and browser paths are closed (D12, D14) |
| V7's LinkedIn job resolver | No | Jobs only | n/a | n/a | Reject: job-only, must not become a people crawler |
| Employer team or bio pages, personal sites, speaker pages | Possible per site | Current role evidence | Targeted | Free | Later, one adapter per site with its own authority rules |
| TinyFish Fetch or Search | No | | Shared job quota | Free quota, reserved | Reject: job pages only (D11, D12 amendment) |
| Email signatures and domains from Jim's mail | Possible | Employer change evidence | Targeted | Existing mail access | Later (NET-6, relationship evidence) |
| Address-book contacts | Possible | Company and title fields | Bulk | New OAuth scope | Reject for MVP: a new grant (People Identity may link a row to Apple Contacts later; the canon allows it) |
| Paid enrichment vendors | Yes | | | Recurring cost | Reject; optional and out of MVP |

**What each source can and cannot say.** The Sheet says who was connected at the export date and what each person listed then. The connected app, by the brief's own description, answers about a person you already name; it does not list who is newly connected. So **the roster changes only when the Sheet does** (a new export, however ChatGPT or Jim gets it); everything else refreshes live. V7 states the Sheet's age on the card so a missing new connection is never a silent gap. If ChatGPT also has a way to surface new connections, they arrive as ordinary evidence rows with their own source label and V7 treats them the same way. Whether the connector returns tenure dates or history is unknown; the design assumes it does not, so each stored fact says "listed as of date X", never "employed from, to", and a blank field never demotes a stored position.

## E. Hostinger schema (minimum, added by slice)

Added only after Jim approves each slice's tables. Table creation happens only on a live run. **No table is edited by hand.** Everything in them is written by V7 from validated evidence or copied from a tick Jim makes in Notion.

`v7_network_people` (NET-1)
- `id` (key), `person_key` unique (hash of the identity key), `url_key` unique and nullable (normalized profile address: scheme, host, case, query string and trailing slash removed), `display_name`, `connected_on` date, `first_seen`, `last_observed`, `status` (`ACTIVE`, `REMOVED`), `history_coverage` (`SEED_ONLY`, `PARTIAL`, `UNKNOWN`), timestamps.
- No email column; evidence that carries one has it dropped on import (decision 4). The brief's `refresh_state` and `refresh_due_at` become a derived "due for a check" computed from `last_verified` and job relevance, not a queue table.
- Identity rule: the URL key is the identity. With no URL, fall back to normalized name plus company key, and treat any collision as ambiguous and unmerged. Two people with one name never merge; a changed employer never creates a new person.

`v7_network_positions` (NET-1)
- `id`, `person_id`, `company_key` (joined `core()` tokens), `company_name`, `title` nullable, `position_state` (`CURRENT`, `SUPERSEDED`), `first_observed`, `last_observed`, `last_verified` (the latest observation that confirmed it), `source_kind` (`SHEET_VIA_CHATGPT`, `LINKEDIN_APP_VIA_CHATGPT`), `source_ref` (the evidence batch label, never a URL), `source_observed_at`, `material_hash` (unique per person, company, title and source), timestamps.
- `SUPERSEDED` means "no longer the listed position as of a later observation". It does not assert that employment ended, so no end date is invented.
- Index on (`company_key`, `position_state`) for matching.

`v7_network_batches` (NET-1)
- `id`, `batch_key` unique (a hash of the exchange rows consumed), `request_ref`, `observed_from` and `observed_to`, row and outcome counts, `status` (`COMPLETE`, `FAILED`), `imported_at`. This is the idempotency record ("have I already consumed these rows?") and feeds the "last evidence received" freshness line.

`v7_network_events` (NET-2)
- `id`, `person_id`, `event_type` (`PERSON_IMPORTED`, `CURRENT_POSITION_CONFIRMED`, `COMPANY_CHANGED`, `TITLE_CHANGED`, `PROFILE_UNRESOLVED`, `CONFLICT_DETECTED`), `old_position_id`, `new_position_id`, `observed_at`, `event_hash` unique, `created_at`. Append-only and idempotent. This is the history neither the Sheet nor ChatGPT keeps.

`v7_network_dismissals` (NET-4)
- `id`, `job_page_id`, `person_id`, `evidence_hash`, `dismissed_at`. Copied from the Dismiss tick in the owned Notion block so that rewriting or deleting the block cannot resurrect a dismissed lead. A lead returns only when its `evidence_hash` changes.

`v7_network_aliases` (NET-4, learned, never typed)
- `id`, `alias_key` unique, `company_key`, `confirmed_at`. A row appears only when Jim ticks "Same company" on a possible match (section H).

**The Notion exchange** is a new private database, "Network Exchange" (Jim creates it once; V7 never creates structure). One row per message, with: **Type** (select: `REQUEST`, `EVIDENCE`), **Request ID** (text), **Status** (select: `Open`, `Answered`, `Failed`, `Consumed`), **Company** (text), **Person URL** (url), **Name** (title), **Company observed** (text), **Title observed** (text), **Observed on** (date), **Source** (select: `SHEET`, `LINKEDIN_APP`), **Note** (text, fixed codes only on failure). V7 writes `REQUEST` rows and reads `EVIDENCE` rows; ChatGPT does the reverse. It is a mailbox, not a ledger: V7 archives rows 14 days after they are consumed, and it holds nothing the tables do not.

**How the tables stay up to date: automatically.** V7 asks, ChatGPT answers, V7 records. Nothing is typed in by hand. If a position is wrong, it is wrong at the source, and the next lookup fixes it.

Not added: a raw-observation table (positions already carry source and observed time), a recommendations table (matching is computed on demand), a refresh queue table (the exchange is the queue), a hand-correction path.

One platform addition: a small transaction context in `platform/db.py`. The connection is autocommit, and a position change must commit together with its event, otherwise a crash between them would lose the event for good on replay.

## F. Contracts (architecture level)

```python
@dataclass(frozen=True)
class Observation:                 # one validated EVIDENCE row
    url_key: str | None; display_name: str
    company_name: str | None; title: str | None
    source_kind: SourceKind; request_id: str; observed_on: date

@dataclass(frozen=True)
class WantRequest:                 # one REQUEST row V7 writes
    request_id: str; company_name: str; person_url_keys: tuple[str, ...]; reason: Reason   # NEW_JOB | APPLIED | STALE_TOP_LEAD

def plan_requests(admitted_jobs, state, today, caps) -> list[WantRequest]    # pure; caps keep it bounded
def validate_evidence(row, open_requests, today) -> Observation | Rejected  # fixed rules, no model
def identity_key(obs) -> IdentityKey | Ambiguous
def company_key(name) -> str                                                # " ".join(core(name))
def plan(existing: PersonState | None, obs) -> Plan                         # pure: writes + events, no I/O
def apply(connection, plan, live) -> Applied                                # one transaction, read back

@dataclass(frozen=True)
class NetworkLead:
    person_id: int; display_name: str; basis: Basis        # CURRENT_AT_COMPANY | FORMER_AT_COMPANY | POSSIBLE_SAME_COMPANY
    company: str; title: str | None
    reasons: tuple[str, ...]; observed_on: date; freshness: Freshness
    objective: str                                           # fixed template, not generated

def match(job, connection, limit=5) -> list[NetworkLead]   # stateless
def render_block(leads) -> list[NotionBlock]               # pure
```

The exchange reader returns rows and never writes to the tables; `apply` is the only writer to the database.

## G. Refresh algorithm (V7 asks, ChatGPT answers, V7 records)

**V7's side, each tick (bounded; non-fatal; most ticks do nothing):**
1. **Ask.** For each newly admitted job, applied job, or stale top lead (section H) whose company has no recent coverage and no open request, write one `REQUEST` row. Caps (named constants, defaults): at most 5 companies and 20 people a day, and at most one open request per company.
2. **Read.** Read `EVIDENCE` rows with Status `Open` or new, matched to an open request.
3. **Validate (fixed rules, no model).** Required fields present; profile address normalizes; observed date is not in the future and not older than the stated limit; source is one of the two allowed; company and title lengths are sane; the request exists and is open; the batch is under its row cap. A row that fails is counted and ignored, never partly applied; a bad batch raises one alert and leaves the previous data in use.
4. For each valid observation, compare with the stored current position:
   - same company key and same title: confirm (`last_verified` and `last_observed` advance); no event.
   - same company key, different title: previous position `SUPERSEDED`, new position `CURRENT`, one `TITLE_CHANGED` event. No promotion is inferred.
   - different company key: previous `SUPERSEDED`, new `CURRENT`, one `COMPANY_CHANGED` event.
   - blank company or title where a value was stored: no change and no event; the stored value simply ages.
   - a company value that is the person's own name or freelance, independent or self-employed wording is recorded as given but flagged `NOT_AN_EMPLOYER` and never matches a job.
   - person not seen before: create the person and the position, one `PERSON_IMPORTED` event.
   - two rows that resolve to one identity with conflicting positions, or a collision with no URL: counted as ambiguous, no write for that person. **Conflicts never silently overwrite** (the brief's rule): the last accepted state stays and the conflict is flagged.
   - **Trust:** evidence is an observation with provenance, never authority. A newer `LINKEDIN_APP` observation outranks an older `SHEET` one; an older observation never replaces a newer one.
5. Mark each consumed row `Consumed`, write the batch record, and read the tables back. Replaying the same rows is a no-op (unique hashes), so a second run creates zero people, zero positions and zero events.
6. After a successful import push one line (counts only: how many changes, how many at companies currently in the Job Ledger) through the existing alerts layer; the change events feed a private digest (NET-5).
7. **Freshness and stalls.** Freshness is the observation date, always shown (the canon's stale-data rule: a stale lead says so): `FRESH` up to 45 days, `AGING` to 120, `STALE` beyond, and `CONFLICTED` while an ambiguity flag is set (named constants). If open requests have sat unanswered for 24 hours, V7 raises one alert ("network lookups stalled"), once. With ChatGPT unavailable nothing else changes: V7 matches from the stored state and labels it.
8. I/O is Notion and the database. Notion calls use the shared retry helper with fixed error codes; an unreachable exchange is `DEGRADED`, never "no new evidence". Database failures use the existing fixed `StoreError` codes and roll the person back.

**ChatGPT's side (its own scope; the exact module text is in section N, and nothing in the contract changes until Jim says so):** read open `REQUEST` rows (capped); for each company, search Jim's connections Sheet for matching people, allowing for obvious name variants; for the few matches, use the connected LinkedIn app to verify the current company and title; write one `EVIDENCE` row per person (including "not found" as a fixed code), set the request to `Answered` or `Failed`; never message anyone, never write outside the exchange, never infer closeness; stop for the day when the connector refuses or the cap is reached.


## H. Job-to-network recommendation algorithm

Triggers (from the canon): (1) a newly qualified job, meaning a published job with a page id and a verified time whose stored `admission` is `ADMIT` (`REVIEW` jobs also publish but are not included by default); (2) Jim moves a job to active pursuit (the Ledger's Applied checkbox or Applied-on date); (3) an interview scheduled or confirmed; (4) Jim explicitly asks (a manual stage that takes a company). **The first slice covers 1 and 2**, because both surface on the Ledger page; 3 and 4 need a surface decision, since Hiring Pipeline pages are human-owned and never written. Matching is stateless and reads current Hostinger state only.

- Company match is by equal company key, after alias resolution. A person flagged `NOT_AN_EMPLOYER` never matches.
- **Possible matches, automatic, never silent.** When no key is equal but one company key is a whole-word prefix of the other ("meta" and "meta platforms"), the person is shown last, labelled "Name differs: same company?", with a Same company tick. Ticking it records a learned alias (`v7_network_aliases`) that applies to every later job and import. Leaving it alone shows nothing more. No other fuzzy matching exists, so a missed alias is a miss, never a wrong person, and nobody types an alias.
- Tier 1: a `CURRENT` position at the company. Tier 2: a `SUPERSEDED` position at the company ("previously listed there"). Tier 3: the possible matches above. Within a tier, order by function overlap between the stored title and the job's role (shared role tokens), then by freshness, then by name for a stable order. No numeric score is stored; every ordering factor is visible.
- A person with no company match is never shown, so weak signals cannot fill slots. At most five leads.
- Reasons are fixed templates built from stored facts, for example "Listed at Acme as Director of Product, as of September 20 (14 days)" or "Previously listed at Acme; later listed elsewhere". Nothing about closeness, referral likelihood or willingness is ever generated.
- A stale lead is shown with its age and a verify-first note, never as current. A stale top lead also becomes a `REQUEST` for ChatGPT to re-verify (section G).
- A company with no people observed yet writes no block and no empty placeholder; V7 has already asked ChatGPT, and the next tick after the evidence arrives writes the block. While a request is open and the block exists, it says "lookup pending". With ChatGPT unavailable V7 matches from stored state only and labels each lead with its age.
- A lead whose `evidence_hash` is in the dismissals table is not shown for that job; new evidence changes the hash and it returns.
- Objectives are fixed templates by basis (for example, learn how the team is organised for a current employee). Drafting message text is not part of V7; that stays a human or ChatGPT-side step.

Examples (synthetic):

1. Admitted job at Acme. Network holds Jane (current at Acme, fresh), Raj (superseded at Acme, aging) and Tom (current at WidgetCo). Result: Jane then Raj; Tom is omitted.
2. Admitted job at WidgetCo. The only match is Sam, current at WidgetCo but 200 days old. Result: Sam, labelled STALE with its date and a verify-first note.
3. Admitted job at a company nobody in the network lists. Result: nothing is written; no empty block is created.

## I. Roadmap (each slice has a product boundary of its own)

| Slice | Outcome | Likely files | Schema | Tests and UAT | Depends on | Risks | Size | PRs |
|---|---|---|---|---|---|---|---|---|
| **NET-0 ChatGPT-side spike (no V7 code)** | One real round trip is proven by hand and reported by count and field names only: ChatGPT finds one connection by company in the Sheet and verifies that person's current company and title through the connected LinkedIn app, and says which fields came back and any limit it hit. Settles what the connector really returns and the Sheet's real columns. | none (a ChatGPT run, using the prompt in section N) | none | Fields returned, whether tenure or history comes back, rate limits, failure modes; no name or row content written anywhere in this repository | Jim runs it, or says ChatGPT already has | The connector may return less than the brief hopes (no current title, or a daily cap) | S | 0 |
| **NET-1 Exchange, validator and importer** | V7 reads `EVIDENCE` rows from the exchange, validates them, and records people and positions without duplicates; replay creates nothing; dry run by default. | `lifeos/network/{identity,exchange,validate,store}.py`, `platform/db.py` transaction helper, `run.py` line, a step in `finish` (continue-on-error), `docs/SETUP.md` | `v7_network_people`, `v7_network_positions`, `v7_network_batches` | table-driven validator and identity tests (tracking parameters, case, trailing slash, same-name collision, blank fields, own-name company, future dates, unknown request, oversized batch), replay twice equals once, an unreachable exchange is DEGRADED and keeps the old data | NET-0, Jim creates the exchange database, table approval | Evidence quality depends on ChatGPT; the validator is the guard | M | 1 |
| **NET-2 Requests, change events, stall alert** | V7 asks for what it needs within the caps; a second observation yields exactly the right change events; the push line fires once; an unanswered request alerts once. | `lifeos/network/{requests,reconcile,freshness,alerts}.py` | `v7_network_events` | title change, company change, blank field, new person, absent person, conflicting rows; caps respected; one request per company; stall alert once | NET-1, the alert fix (P2), and ChatGPT's module live | Cadence is ChatGPT's hourly run, so a lookup takes about two hours end to end | M | 1 |
| **NET-3 Job match (no surface yet)** | A dry-run stage reports, by count, how many admitted jobs have leads and how many leads each. | `lifeos/network/match.py`, `lifeos/sources/network_leads.py`, `run.py` line | none | the three examples above, tier order, five-lead cap, no weak fill, possible-match tier, own-name company never matches | NET-2 | A missed alias gives a miss, not a wrong match | M | 1 |
| **NET-4 Surface** | Leads appear on the job page in one machine-owned block with Dismiss and Same-company ticks; unchanged leads cause no write; both ticks are remembered. | marker-block writer generalized from `report_region`, one step in the `finish` job after publish | `v7_network_dismissals`, `v7_network_aliases` | owned block replaced, nothing else touched, read-back, hash skip, page-gone and Ledger-target guards, tick read-back and persistence, "lookup pending" wording | NET-3, decision 3 | A human editing inside the owned block loses the edit; documented | M | 1 |
| **NET-5 Change digest** | "Who moved recently" and "who has listed company X", written automatically after each import as a private page or region. | `lifeos/network/queries.py`, a stage | none | query tests; region ownership decided before any report region | NET-2 | Region ownership is a product decision (roadmap decision 3) | M to L | 1 |
| **NET-6 Relationship evidence (later)** | Accepted mail and calendar evidence of real contact. | later | later | later | core value proven | Easy to overreach into relationship scoring, which is out of scope | L | later |

NET-0 from the brief is kept, and moved to where the uncertainty actually is: the connector's real behavior. Triggers 3 and 4 from the canon (an interview, an explicit ask) are later slices and wait on a surface decision.

## J. Surfacing mechanics (NET-4 in detail)

- One top-level toggle block at the end of the Ledger page body, titled "Network Leads (n)". Its first child paragraph begins with a marker (`v7-net:1`) followed by a short hash of the lead set. The marker, not position, is how ownership is decided.
- An unchanged hash means no write. A changed hash deletes the owned block and appends the new one, then reads the page back. Nothing outside the owned block is read for change or written.
- The existing description marker at the top of the body (`v7-jd:1`) and the properties the publisher and fit sync own are never touched, so `readback.py`'s first-three-blocks check keeps passing.
- Before any write: `ledger.verify` on the data source, page not archived or in trash, description marker still present. A failed guard writes nothing.
- Each lead has a Dismiss tick, and a possible match also has a Same company tick. V7 reads both before any rewrite and copies them to the dismissals and aliases tables. An unchanged dismissed lead stays hidden; it comes back only when new evidence changes its `evidence_hash` (the canon's rule).
- The block shows the observation date on every lead, per the canon, and each lead carries a link to the profile page for "open LinkedIn". "Draft outreach" stays a ChatGPT-side step, as the canon's suggested next action allows.
- Matching reads only Hostinger. The step runs in the hourly `finish` job after publish because the Jobs Notion and database settings are there. It is continue-on-error and absent from the lane-failure list, so a network failure cannot fail the Jobs run.

## K. Non-goals and rejected architecture

No second scheduler, workflow family or workflow input. No local files, commands or hand-maintained lists. No Google client in V7. ChatGPT never writes outside the exchange and never becomes the datastore. No graph database. No profile scraping, crawler, logged-in automation or browser. No TinyFish. No dependency on a chat connector at runtime. No automatic outreach, no connection requests, no drafted message text from V7. No relationship strength, referral likelihood or closeness scoring. No generic CRM framework. No paid enrichment vendor. No email addresses stored by default. No Jobs-to-Network import, and no new edit to the Jobs tables.

## L. Decision-log proposals (draft; `docs/DECISIONS.md` is not edited)

- Network Intelligence is its own OS package; the Jobs-to-Network glue lives in `lifeos/sources/`.
- The data lives in derived, automatically maintained Hostinger tables beside the Jobs tables (a bounded noncanonical machine-state store under the Platform Canon, approved by Jim); the Sheet stays canonical for who is connected, and V7 never reads it.
- ChatGPT acquires (its Drive access and connected LinkedIn app) and V7 validates, records and matches, joined by a private Notion exchange that follows the contract's existing browser-evidence handoff pattern. ChatGPT is never the network datastore, and V7 works, with aging data, when ChatGPT is unavailable (D54).
- Refresh is job-driven and bounded (the brief's Tier 0 and Tier 1 first): at most 5 companies and 20 people a day, one open request per company.
- Company aliases are learned from Jim's Same company ticks; there is no typed alias list and no other fuzzy matching in V7.
- Dismissals and confirmations are copied from ticks in the owned block into tables, so rewriting the block cannot resurrect a dismissed lead.
- A position is "listed as of an observation"; a superseded position does not assert that employment ended, and no tenure dates are invented.
- Evidence is an observation with provenance, never authority; conflicts never silently overwrite.
- Person identity is the normalized profile address; a name alone never merges two people.
- Matching is stateless, by equal company key after alias resolution, and shows at most five leads with visible reasons and freshness.
- Leads are written as one marker-owned block on the Ledger page body, with no new Notion property.

## M. The brief's twenty questions, answered

| # | Answer |
|---|---|
| 1 | New package `lifeos/network/`; cross-OS glue in `lifeos/sources/`. |
| 2 | `platform/db.connect`, `StoreError`, `names.core`, `notion_client`, `redact`, `alerts`, `limits`; plus one new transaction helper. |
| 3 | A dedicated `lifeos/network/store.py` with its own `ensure_schema`, created only on live runs. |
| 4 | Reuse `names.core`; the key is its joined tokens, with no platform change. Do not use `same_company` as a key. |
| 5 | Published job with a page id and verified time, `admission = ADMIT`, plus the canon's other three triggers (first slice: qualified and applied jobs; section H). The brief's status chain with ENRICHED and FIT does not exist. |
| 6 | One marker-owned toggle block at the end of the Ledger page body, from a `finish`-job step after publish. |
| 7 | Matching is database-only and at most about ten Notion calls; no network refresh runs on the tick at all. |
| 8 | `python -m lifeos.run network-*` stages, dry run by default; all three V7 steps (read evidence, write requests, surface leads) run in the hourly `finish` job as non-fatal steps; no new `hourly.yml` input, no new `domains.yml` job. |
| 9 | ChatGPT's connected LinkedIn app, run by ChatGPT, for targeted current-state lookups; V7 cannot call it, so it arrives through the Notion exchange. |
| 10 | Only forward accumulation, one export at a time. Retroactive history is not promised. |
| 11 | MVP adapters: the Sheet roster and the connected app, both via ChatGPT. Later: per-site public pages. No hand-correction path. |
| 12 | No export import: ChatGPT reads the Sheet already in Drive and returns the relevant people through the exchange (section N); nothing is local and V7 never reads Drive. |
| 13 | Profile-address identity; name-only collisions stay unmerged and are counted. |
| 14 | The schema allows several current positions per person; the export can only show one, so concurrent roles wait for a richer source. |
| 15 | Fresh to 45 days, aging to 120, stale beyond, always with the date. Defaults, tunable. |
| 16 | People, positions and the batch ledger in NET-1; events in NET-2; dismissals and aliases in NET-4. |
| 17 | Yes, stateless on demand; the page's marker hash is the only stored idempotency. |
| 18 | All of it deterministic in V7. Message drafting stays human or ChatGPT-side and outside V7. |
| 19 | Person rows and addresses stay in Hostinger. Only the lead's display name, listed company and title, the observation date and a fixed reason appear in Jim's private Notion block (decision 5 covers whether to show more or less). |
| 20 | First as counts from a stage and a push line after each import, then as a private page or a region once ownership is decided. |

## N. The Notion exchange and ChatGPT's side (cloud, automatic)

**There is no seed step, no local file and no Drive access in V7.** The first people V7 learns are the ones ChatGPT returns for the first company V7 asks about, and the table grows only with people who matter to a real job.

**One-time setup, in Notion only:**
1. Create the private database "Network Exchange" with the properties in section E and share it with the Notion integration V7 already uses, and with ChatGPT's Notion connection.
2. Add the database's identifier as a repository secret (an identifier, and this repository is public).
3. Approve the tables slice by slice.
Nothing in Google changes: ChatGPT already has Drive access, and the connected LinkedIn app is already on Jim's ChatGPT account.

**ChatGPT's module, in plain words (draft; not added to the production contract, which is on hold):** "Network Lookups", run in ChatGPT's own scope on its existing schedule. Each run: read up to 10 open `REQUEST` rows in the Network Exchange. For each, search Jim's LinkedIn connections Sheet for people at that company (allow for obvious variants such as a missing legal suffix); for at most 4 matches per company, use the connected LinkedIn app to check the person's current company and title; write one `EVIDENCE` row per person with the profile address, name, company and title found, today's date, and the source (`SHEET` if only the Sheet was used, `LINKEDIN_APP` if verified); if a person cannot be verified, write the Sheet's values with source `SHEET`; if the app is unavailable or refuses, set the request to `Failed` with a fixed code and stop for the day. Never message anyone, never write outside the exchange, never guess a title, never infer closeness, influence or willingness to refer, and never copy the whole Sheet.

**NET-0 prompt for ChatGPT** (to prove the round trip once, by hand, with nothing written anywhere): "Pick one company from my LinkedIn Connections sheet in Drive that has at least two people. Without writing anything and without printing names, tell me: (1) how many sheet rows matched that company and which column headings the sheet has; (2) for ONE of those people, using the connected LinkedIn app, whether you could verify their current company and title, and exactly which fields the app returned (field names only); (3) any limit, error or refusal you hit; (4) whether the app can list my newest connections, or only look up a person I name."

Rejected: any V7 access to Drive or a Sheets reader; any local import or download; an intake folder watched by a V7 job (the earlier draft of this section); a bulk copy of the whole Sheet through ChatGPT (it would invite transcription errors that V7 cannot detect); ChatGPT writing straight into Hostinger; workflow inputs and artifacts (world-readable on a public repository); committing any export.

## O. Decisions Jim must make

Settled by Jim: a separate Hostinger table; everything in the cloud and automated; no local sheet, commands or hand-kept lists; ChatGPT keeps the data fresh through its connected app and Drive access; V7 does not touch Drive; learned aliases and no correction command.

1. **The handoff, my reading of your vetoes.** I read "veto" on the Drive-watching job, the table sync from Drive and "never the Sheet" as: V7 touches no Drive at all, and ChatGPT does the reading and the LinkedIn lookups. The table stays the thing V7 matches from (your earlier instruction), filled from what ChatGPT returns through the Notion exchange. If you meant something else (for example ChatGPT also does the matching), say so in one line.
2. **The roster.** The Sheet lists who is connected as of its export. The connected app, by the brief, looks up a named person but does not list new connections. If ChatGPT has a way to find new connections, say how and it arrives as ordinary evidence; if not, the roster only changes when the Sheet does, and V7 will say the Sheet's age on the card.
3. **Triggers.** The canon's four; the first slice covers a qualified job (`ADMIT`) and an applied job. `REVIEW` jobs are not included by default.
4. **Fields and email.** Email addresses are not stored. The Notion block shows display name, listed company and title, observation date, a profile link and a fixed reason.
5. **Daily caps.** At most 5 companies and 20 people a day (defaults, tunable), because the connector's real limits are unknown until NET-0.
6. **The canon clause** (section 0a): on hold, as you asked.
7. **Erasure and retention for people** (recommended: a `REMOVED` status that excludes a person from matching, set when Jim asks).
8. **Approve the tables and the exchange database**, slice by slice.

## P. First build slice

```text
FIRST BUILD SLICE:
NET-0 ChatGPT-side spike, then NET-1 exchange, validator and importer

WHY FIRST:
NET-0 needs no V7 code and settles the one thing nobody here has verified: what ChatGPT's connected LinkedIn app really returns and what limits it has, plus the Sheet's real columns. NET-1 then builds the part V7 owns (validation, identity, positions) against the real field list.

EXPECTED FILES (NET-1):
lifeos/network/__init__.py, identity.py, exchange.py, validate.py, store.py, one line in lifeos/run.py, a step in the hourly finish job, tests/network/*, a docs/SETUP.md section

SCHEMA:
NONE for NET-0. NET-1: v7_network_people, v7_network_positions, v7_network_batches (after Jim's approval), plus the Notion exchange database Jim creates.

ACCEPTANCE:
NET-0: the four answers in the prompt, by count and field names only. NET-1: validator and identity tests pass on synthetic evidence (tracking-parameter and case variants of profile addresses, same-name collisions, blank fields, own-name companies, future dates, unknown request, oversized batch, commas and emoji in names); a dry run prints counts only; nothing is written anywhere; replay is a no-op; the full suite passes.

DO NOT BUILD YET:
The request generator, change events, aliases, the Notion block, the digest, any Google or Drive client, any local importer, relationship evidence.
```
