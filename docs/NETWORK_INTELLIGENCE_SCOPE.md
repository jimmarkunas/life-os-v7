# Network Intelligence: scope and roadmap (scoping only, no code)

Status: scoped for Jim's review. Nothing here is built, and this document changes no schema, secret, workflow or `docs/DECISIONS.md`.
**Settled by Jim:** the **whole network (about 5,000 connections, and where each one works) lives in the Hostinger database already used for Jobs OS**, so the apply side of Jobs OS can recommend contacts for any job; everything runs in the cloud and is automated, with no local files, no commands and no lists he maintains; **ChatGPT keeps the data fresh through its connected LinkedIn app and its Drive access**; V7 does not touch Drive; learned company aliases and "no correction command" are approved; and there are **no small caps**. Still open, and it is the one thing only ChatGPT can answer: which door ChatGPT's batches use to reach Hostinger (section N). Assumed defaults, change on request: the first slice's triggers are a qualified job (`ADMIT`) and an applied job.

## 0. The shape of it in one paragraph

Network Intelligence is a temporal people graph in Hostinger (tables beside the Jobs tables) that makes Jobs OS smarter: **Hostinger holds all of Jim's roughly 5,000 connections and where each works**, so when any job is admitted or applied to, the matching runs against the whole table at once and lists the few people who currently or formerly list that employer, with the evidence and its age. No lookup happens at job time, and nothing is sampled. **ChatGPT produces and refreshes the data; V7 stores, diffs and matches.** ChatGPT has what V7 cannot reach: the connected LinkedIn app, which the brief names as the fresh-data source, and Drive access to the connections Sheet. V7 runs on GitHub Actions and must work with ChatGPT unavailable (D54), so the two meet at a handoff: ChatGPT sends **batches** of observations (a full roster, or fresh checks of particular people, each with the date), and V7 validates each batch with fixed rules, applies it in chunks, and records what changed (company changed, title changed) in the history tables. If ChatGPT is down or slow, the tables simply age and every lead shows how old its evidence is. V7 never reads Drive, and nobody downloads a file, runs a command or edits a list. Every claim carries its source and the date it was observed; nothing is inferred about relationship strength.

What this fixes from earlier drafts: I had sized the refresh as a trickle (a few companies and people a day) and treated the roster as something that changes only with a new export. The feature needs the **whole** network known, with employers, so the design is bulk-first: V7 imposes only sanity limits on a batch, and the real throughput is whatever ChatGPT's connector allows, which the first slice measures instead of assuming.

## 0a. The live Notion canon, re-read, and what I got wrong

I first said the Notion page "LI Connection Database & Integration" forbids a separate connection database and needs editing. **That overstated it.** Re-read in full, it says:

- **Canonical dataset:** Jim's existing Google Sheet of connections (a dated export, in his Drive). Still true: the Sheet stays where Jim's data lives. ChatGPT, which already has Drive access, is the one that reads it, as the page's original workflow assumed.
- **"LIFE OS may read and normalize the Sheet for matching/search"**, and **must not create "a second CRM or independently editable connection database merely for this feature."** Tables that V7 fills automatically from observations ChatGPT supplies, that nobody edits by hand, and that can be rebuilt by asking again are a normalization, not an independently editable database. This design keeps to that: Jim's only inputs are decisions (a dismissal, a "same company" confirmation), never data.
- **Four triggers:** a newly qualified open job in the US Remote or Scale-Up experience; Jim moves an opportunity into active pursuit or applied; an interview is scheduled or confirmed; Jim explicitly asks. Section H.
- **Matching:** normalized employer name and known aliases, ranked only by context actually in the data. Never infer closeness, influence or willingness to refer. Section H.
- **Output:** a compact shortlist: person, current role and company, why relevant, which opportunity, a recommended networking objective, a suggested next action (draft outreach, open LinkedIn, dismiss), and the data's freshness. Section J.
- **Lifecycle:** a recommendation is derived career context, not a new durable person record; dismissing one suppresses the same unchanged recommendation for the same opportunity; new evidence brings it back. Sections E and J.
- **Stale or unavailable data** is shown as a limitation. **Non-goals:** a second CRM, scraping beyond Jim's supplied data, an inferred social graph, automatic messaging, any automatic claim that a connection can refer him.

**What is still worth adding to that page.** The Platform Canon allows a "bounded noncanonical machine-state store" only when Jim explicitly approves it and **the owning domain canon defines its scope.** Jim has approved it in this session; the LI page does not yet define the scope. Suggested clause: "Network matching and change history run from derived Hostinger tables that V7 fills from batches ChatGPT supplies through a private handoff (the Sheet and the connected LinkedIn app). The Sheet stays canonical for who is connected; the tables are rebuildable by asking again except the change history, hold no hand-edited data, and store only dismissals and company-name confirmations as Jim's own decisions." **On hold at Jim's request**; nothing on the page changes until he says so.

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
| Batch validator and chunked importer | MISSING | |
| Person identity (profile-URL key, collision handling) | MISSING | |
| Company key | PARTIAL | `core()` is reusable; a key built as the joined core tokens needs no platform change. |
| Transactional write | MISSING | A tiny platform helper is needed so a position change and its event commit together. |
| Change detection and events | MISSING | |
| Freshness policy | MISSING | |
| Live current-state source | MISSING in V7; exists in ChatGPT | The connected LinkedIn app. V7 cannot call it, so its output arrives as batches through a door to Hostinger (section N). |
| Job-to-network match | MISSING | |
| Surface on the job page | MISSING | No body-append writer exists; `report_region` is the nearest. |
| Trigger signal | EXISTS | Published job with a page id, a verified time and `admission = ADMIT`. |
| Non-fatal scheduling slot | EXISTS | The `finish` job after `publish`. |
| Alerts | PARTIAL | Layer exists; coupling with Jobs alerts needs the shared fix in `docs/BIG_FEATURES_ROADMAP.md`. |

## C. Final architecture

```text
ChatGPT (its own scope: connected LinkedIn app + Drive access)
   produces batches:  ROSTER (everyone, bulk)  |  CHECK (fresh company and title for named people)
   |  through a door to Hostinger (section N: A direct, or B a private file drop; C, Drive, is vetoed)
   v
Hostinger inbox (batch received, not yet applied)
   |  V7 tick (`finish` job, non-fatal): picks up a batch, validates it, applies it in chunks inside the runtime budget
   v
validate (fixed rules; a full result or an error, never partial) -> identity (profile-URL key) + company key
   v
reconcile against stored positions  ->  same | title changed | company changed | new person | ambiguous
   v
Hostinger: v7_network_people, v7_network_positions, v7_network_events, v7_network_batches   (one transaction per person)
   |
   +----> change events -> one push line per batch + private digest
   |
   +----> match(job)  <---- admitted, applied jobs (read through a sources adapter; reads Hostinger only, whole roster)
               v
        NetworkLead list (at most 5, each with reasons and freshness)
               v
   marker-owned "Network Leads" block at the end of the Ledger page body (Dismiss and Same-company ticks)
```

Placement: `lifeos/network/` (identity, store, inbox, validate, reconcile, match, render) imports `platform` only. The stage that reads admitted jobs and calls `network.match` lives in `lifeos/sources/` (it may import `jobs` and `network`), so neither OS imports the other and the D17 boundary stays as it is. **No Google client of any kind is added to V7.** The V7 steps (apply a batch, surface leads) run in the existing hourly `finish` job as non-fatal steps, where the database and Notion settings already are.

## D. Source feasibility

| Source | Viable | Gives | Bulk or targeted | Auth and cost | MVP |
|---|---|---|---|---|---|
| ChatGPT's connected LinkedIn app, run by ChatGPT | Yes, per the brief and per Jim; what it returns and how fast is **unmeasured** (slice NET-0) | Connections and where they work now; a named person's current company and title | Jim says it knows his connections and employers; the brief says targeted lookups. NET-0 settles which | Jim's existing ChatGPT connection; no new cost | **MVP: the fresh data** |
| Jim's LinkedIn connections Sheet in Drive, read by ChatGPT | Yes; the canon page's own workflow | Who is connected, with company, position and connection date as of the export | Bulk | ChatGPT's existing Drive access | **MVP: the first roster, and the fallback if the app cannot list connections** |
| The handoff door (section N) | Open: depends on what ChatGPT can write to | Batches into Hostinger | Bulk | See N | **MVP: the door** |
| LinkedIn through V7 directly | No | | | | Reject: V7 cannot reach a chat connector from GitHub Actions (D54), and browser paths are closed (D12, D14) |
| V7's LinkedIn job resolver | No | Jobs only | n/a | n/a | Reject: job-only, must not become a people crawler |
| Employer team or bio pages, personal sites, speaker pages | Possible per site | Current role evidence | Targeted | Free | Later, one adapter per site with its own authority rules |
| TinyFish Fetch or Search | No | | Shared job quota | Free quota, reserved | Reject: job pages only (D11, D12 amendment) |
| Email signatures and domains from Jim's mail | Possible | Employer change evidence | Targeted | Existing mail access | Later (NET-6, relationship evidence) |
| Address-book contacts | Possible | Company and title fields | Bulk | New OAuth scope | Reject for MVP: a new grant (People Identity may link a row to Apple Contacts later; the canon allows it) |
| Paid enrichment vendors | Yes | | | Recurring cost | Reject; optional and out of MVP |

**What the data can and cannot say.** Each observation carries one currently listed position and the date it was seen. It gives no tenure dates or history unless the app returns them (unknown), and some fields are blank. So history accumulates from the first batch forward, and each stored fact says "listed as of date X", never "employed from, to". A blank field never demotes a stored position. A batch that omits someone proves nothing about them (absence from one batch never removes or demotes anyone).

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
- `id`, `batch_key` unique (a hash of the batch consumed), `batch_kind`, `observed_from` and `observed_to`, row and outcome counts, the apply cursor, `status` (`RECEIVED`, `APPLYING`, `COMPLETE`, `FAILED`), `imported_at`. This is the idempotency record ("have I already consumed this batch?"), the chunk cursor, and the "last evidence received" freshness line.

`v7_network_events` (NET-2)
- `id`, `person_id`, `event_type` (`PERSON_IMPORTED`, `CURRENT_POSITION_CONFIRMED`, `COMPANY_CHANGED`, `TITLE_CHANGED`, `PROFILE_UNRESOLVED`, `CONFLICT_DETECTED`), `old_position_id`, `new_position_id`, `observed_at`, `event_hash` unique, `created_at`. Append-only and idempotent. This is the history neither the Sheet nor ChatGPT keeps.

`v7_network_dismissals` (NET-4)
- `id`, `job_page_id`, `person_id`, `evidence_hash`, `dismissed_at`. Copied from the Dismiss tick in the owned Notion block so that rewriting or deleting the block cannot resurrect a dismissed lead. A lead returns only when its `evidence_hash` changes.

`v7_network_aliases` (NET-4, learned, never typed)
- `id`, `alias_key` unique, `company_key`, `confirmed_at`. A row appears only when Jim ticks "Same company" on a possible match (section H).

`v7_network_inbox` (NET-1, only if the door is a direct write: section N, door A)
- `id`, `batch_id`, `row_no`, `received_at`, `profile_url`, `name`, `company`, `title`, `observed_on`, `source` (`SHEET`, `LINKEDIN_APP`), `batch_kind` (`ROSTER`, `CHECK`), `status` (`RECEIVED`, `APPLIED`, `REJECTED`). Insert-only for ChatGPT's side. V7 applies a batch in chunks across ticks, keeping a cursor in `v7_network_batches`, and clears applied rows after 14 days. It is a mailbox, not a ledger: it holds nothing the tables do not.

**How the tables stay up to date: automatically.** ChatGPT sends batches, V7 records them. Nothing is typed in by hand. If a position is wrong, it is wrong at the source, and the next lookup fixes it.

Not added: a raw-observation table (positions already carry source and observed time), a recommendations table (matching is computed on demand), a refresh queue table (ChatGPT chooses whom to check from the Job Ledger it can already read), a hand-correction path.

One platform addition: a small transaction context in `platform/db.py`. The connection is autocommit, and a position change must commit together with its event, otherwise a crash between them would lose the event for good on replay.

## F. Contracts (architecture level)

```python
@dataclass(frozen=True)
class Observation:                 # one validated batch row
    url_key: str | None; display_name: str
    company_name: str | None; title: str | None
    source_kind: SourceKind; batch_kind: BatchKind; observed_on: date

def next_batch(door, connection) -> Batch | None                         # the only door-specific code
def validate_batch(batch, previous_roster_size, today) -> list[Observation] | Rejected   # fixed rules, no model
def identity_key(obs) -> IdentityKey | Ambiguous
def company_key(name) -> str                                              # " ".join(core(name))
def plan(existing: PersonState | None, obs) -> Plan                       # pure: writes + events, no I/O
def apply_chunk(connection, plans, live) -> Applied                       # one transaction per person, read back

@dataclass(frozen=True)
class NetworkLead:
    person_id: int; display_name: str; basis: Basis        # CURRENT_AT_COMPANY | FORMER_AT_COMPANY | POSSIBLE_SAME_COMPANY
    company: str; title: str | None
    reasons: tuple[str, ...]; observed_on: date; freshness: Freshness
    objective: str                                           # fixed template, not generated

def match(job, connection, limit=5) -> list[NetworkLead]   # stateless, whole roster
def render_block(leads) -> list[NotionBlock]               # pure
```

The door returns batches and never writes to the tables; `apply_chunk` is the only writer to them.

## G. Refresh algorithm (ChatGPT sends batches, V7 records)

**ChatGPT's side (its own scope; the module text is in section N and nothing in the contract changes until Jim says so).** Two kinds of batch, no V7-imposed quota:
- **ROSTER:** everyone ChatGPT can see (all roughly 5,000 connections with company, title and the date observed), first from the Sheet and then from the connected app as far as it can list connections. Sent when it has a fresh one, and at least on a schedule it sets.
- **CHECK:** fresh company and title for named people, chosen by ChatGPT from the Job Ledger it already reads (people at companies of new, active or applied jobs first, then the stalest). Sent as often as the connector allows.
The pace is the connector's, which NET-0 measures. V7 does not ask, cap or queue.

**V7's side, each tick (non-fatal; most ticks do nothing):**
1. **Pick up** the oldest batch in `RECEIVED` or `APPLYING` state.
2. **Validate (fixed rules, no model; the whole batch or none).** Required fields present; profile address normalizes; observed date is not in the future and not older than the stated limit; source and batch kind are allowed; company and title lengths are sane; row count is under a sanity ceiling (default 20,000); and a ROSTER batch that is implausibly small against the previous roster (default under half) is held for a human look, not applied, because that pattern usually means a partial export. A bad batch raises one alert and leaves the previous data in use.
3. **Apply in chunks.** A few hundred people per tick, with the cursor kept in `v7_network_batches`, so a 5,000-row roster completes over a handful of ticks inside the runtime budget (the Platform Canon's 45-second benchmark and 5-minute limit) and a crash resumes where it stopped. For each person, compare with the stored current position:
   - same company key and same title: confirm (`last_verified` and `last_observed` advance); no event.
   - same company key, different title: previous position `SUPERSEDED`, new position `CURRENT`, one `TITLE_CHANGED` event. No promotion is inferred.
   - different company key: previous `SUPERSEDED`, new `CURRENT`, one `COMPANY_CHANGED` event.
   - blank company or title where a value was stored: no change and no event; the stored value simply ages.
   - a company value that is the person's own name or freelance, independent or self-employed wording is recorded as given but flagged `NOT_AN_EMPLOYER` and never matches a job.
   - person not seen before: create the person and the position, one `PERSON_IMPORTED` event. (The first ROSTER batch creates about 5,000 of these and no change events beyond that.)
   - two rows that resolve to one identity with conflicting positions, or a collision with no URL: counted as ambiguous, no write for that person. **Conflicts never silently overwrite** (the brief's rule): the last accepted state stays and the conflict is flagged.
   - **Trust:** evidence is an observation with provenance, never authority. A newer `LINKEDIN_APP` observation outranks an older `SHEET` one; an older observation never replaces a newer one.
4. People absent from a newer batch are left untouched. Absence proves nothing, so nothing is demoted or removed for it.
5. When the last chunk is applied, mark the batch `COMPLETE`, write the batch record, and read the tables back. Replaying a batch is a no-op (unique hashes), so a second run creates zero people, zero positions and zero events.
6. After a completed batch push one line (counts only: how many changes, how many at companies currently in the Job Ledger) through the existing alerts layer; the change events feed a private digest (NET-5).
7. **Freshness and stalls.** Freshness is the observation date, always shown (the canon's stale-data rule: a stale lead says so): `FRESH` up to 45 days, `AGING` to 120, `STALE` beyond, and `CONFLICTED` while an ambiguity flag is set (named constants). If no batch has arrived for 7 days (default), V7 raises one alert ("network data stalled"), once. With ChatGPT unavailable nothing else changes: V7 matches from the stored state and labels it.
8. I/O is the database (and, for door B, one file read). Failures use the shared retry helper and the existing fixed `StoreError` codes; an unreachable door is `DEGRADED`, never "no new batch". A failed chunk rolls the person back and the next tick resumes.

## H. Job-to-network recommendation algorithm

Triggers (from the canon): (1) a newly qualified job, meaning a published job with a page id and a verified time whose stored `admission` is `ADMIT` (`REVIEW` jobs also publish but are not included by default); (2) Jim moves a job to active pursuit (the Ledger's Applied checkbox or Applied-on date); (3) an interview scheduled or confirmed; (4) Jim explicitly asks (a manual stage that takes a company). **The first slice covers 1 and 2**, because both surface on the Ledger page; 3 and 4 need a surface decision, since Hiring Pipeline pages are human-owned and never written. Matching is stateless and reads current Hostinger state only.

- Company match is by equal company key, after alias resolution. A person flagged `NOT_AN_EMPLOYER` never matches.
- **Possible matches, automatic, never silent.** When no key is equal but one company key is a whole-word prefix of the other ("meta" and "meta platforms"), the person is shown last, labelled "Name differs: same company?", with a Same company tick. Ticking it records a learned alias (`v7_network_aliases`) that applies to every later job and import. Leaving it alone shows nothing more. No other fuzzy matching exists, so a missed alias is a miss, never a wrong person, and nobody types an alias.
- Tier 1: a `CURRENT` position at the company. Tier 2: a `SUPERSEDED` position at the company ("previously listed there"). Tier 3: the possible matches above. Within a tier, order by function overlap between the stored title and the job's role (shared role tokens), then by freshness, then by name for a stable order. No numeric score is stored; every ordering factor is visible.
- A person with no company match is never shown, so weak signals cannot fill slots. At most five leads.
- Reasons are fixed templates built from stored facts, for example "Listed at Acme as Director of Product, as of September 20 (14 days)" or "Previously listed at Acme; later listed elsewhere". Nothing about closeness, referral likelihood or willingness is ever generated.
- A stale lead is shown with its age and a verify-first note, never as current. A stale top lead is what ChatGPT's CHECK batches target first (section G).
- A company nobody in the roster lists writes no block and no empty placeholder. Because the whole roster is stored, matching needs no lookup at job time. With ChatGPT unavailable V7 matches from stored state only and labels each lead with its age.
- A lead whose `evidence_hash` is in the dismissals table is not shown for that job; new evidence changes the hash and it returns.
- Objectives are fixed templates by basis (for example, learn how the team is organised for a current employee). Drafting message text is not part of V7; that stays a human or ChatGPT-side step.

Examples (synthetic):

1. Admitted job at Acme. Network holds Jane (current at Acme, fresh), Raj (superseded at Acme, aging) and Tom (current at WidgetCo). Result: Jane then Raj; Tom is omitted.
2. Admitted job at WidgetCo. The only match is Sam, current at WidgetCo but 200 days old. Result: Sam, labelled STALE with its date and a verify-first note.
3. Admitted job at a company nobody in the network lists. Result: nothing is written; no empty block is created.

## I. Roadmap (each slice has a product boundary of its own)

| Slice | Outcome | Likely files | Schema | Tests and UAT | Depends on | Risks | Size | PRs |
|---|---|---|---|---|---|---|---|---|
| **NET-0 ChatGPT capability check (no V7 code)** | One ChatGPT run, nothing written, answers by capability and number only: can its LinkedIn app list all connections with employers, how many per call, any daily cap; what it can write to (Hostinger, Notion, Drive, GitHub), how large; the Sheet's real columns. Picks the door (section N). | none (the prompt in section N) | none | The answers; no name or row content written anywhere in this repository | Jim runs it | The connector may return less than hoped, and the door may need something new | S | 0 |
| **NET-1 Door, validator and chunked importer** | A batch reaches Hostinger through the chosen door; V7 validates it, applies it in chunks, and records people and positions without duplicates; replay creates nothing; dry run by default; a 5,000-row roster applies inside the runtime budget. | `lifeos/network/{identity,inbox,validate,store}.py`, `platform/db.py` transaction helper, `run.py` line, a step in `finish` (continue-on-error), `docs/SETUP.md` | `v7_network_people`, `v7_network_positions`, `v7_network_batches` (and `v7_network_inbox` for door A) | table-driven validator and identity tests (tracking parameters, case, trailing slash, same-name collision, blank fields, own-name company, future dates, oversized and implausibly small batches), a 5,000-row synthetic roster applied in chunks and resumed after a crash, replay twice equals once, an unreachable door is DEGRADED and keeps the old data | NET-0, the door, table approval | Evidence quality depends on ChatGPT; the validator is the guard | M to L | 1 |
| **NET-2 Change events, digest line, stall alert** | A second observation yields exactly the right change events; the push line fires once per batch; a week without a batch alerts once. | `lifeos/network/{reconcile,freshness,alerts}.py` | `v7_network_events` | title change, company change, blank field, new person, absent person, conflicting rows; stall alert once | NET-1, the alert fix (P2), ChatGPT's module live | Changes are only as fresh as ChatGPT's batches | M | 1 |
| **NET-3 Job match (no surface yet)** | A dry-run stage reports, by count, how many admitted jobs have leads and how many leads each, over the whole roster. | `lifeos/network/match.py`, `lifeos/sources/network_leads.py`, `run.py` line | none | the three examples above, tier order, five-lead cap, no weak fill, possible-match tier, own-name company never matches, a 5,000-person roster matched fast | NET-2 | A missed alias gives a miss, not a wrong match | M | 1 |
| **NET-4 Surface** | Leads appear on the job page in one machine-owned block with Dismiss and Same-company ticks; unchanged leads cause no write; both ticks are remembered. | marker-block writer generalized from `report_region`, one step in the `finish` job after publish | `v7_network_dismissals`, `v7_network_aliases` | owned block replaced, nothing else touched, read-back, hash skip, page-gone and Ledger-target guards, tick read-back and persistence | NET-3, decision 3 | A human editing inside the owned block loses the edit; documented | M | 1 |
| **NET-5 Change digest** | "Who moved recently" and "who has listed company X", written automatically after each batch as a private page or region. | `lifeos/network/queries.py`, a stage | none | query tests; region ownership decided before any report region | NET-2 | Region ownership is a product decision (roadmap decision 3) | M to L | 1 |
| **NET-6 Relationship evidence (later)** | Accepted mail and calendar evidence of real contact. | later | later | later | core value proven | Easy to overreach into relationship scoring, which is out of scope | L | later |

NET-0 from the brief is kept, and moved to where the uncertainty actually is: what ChatGPT's connector really does, and how its output reaches Hostinger. Triggers 3 and 4 from the canon (an interview, an explicit ask) are later slices and wait on a surface decision.

## J. Surfacing mechanics (NET-4 in detail)

- One top-level toggle block at the end of the Ledger page body, titled "Network Leads (n)". Its first child paragraph begins with a marker (`v7-net:1`) followed by a short hash of the lead set. The marker, not position, is how ownership is decided.
- An unchanged hash means no write. A changed hash deletes the owned block and appends the new one, then reads the page back. Nothing outside the owned block is read for change or written.
- The existing description marker at the top of the body (`v7-jd:1`) and the properties the publisher and fit sync own are never touched, so `readback.py`'s first-three-blocks check keeps passing.
- Before any write: `ledger.verify` on the data source, page not archived or in trash, description marker still present. A failed guard writes nothing.
- Each lead has a Dismiss tick, and a possible match also has a Same company tick. V7 reads both before any rewrite and copies them to the dismissals and aliases tables. An unchanged dismissed lead stays hidden; it comes back only when new evidence changes its `evidence_hash` (the canon's rule).
- The block shows the observation date on every lead, per the canon, and each lead carries a link to the profile page for "open LinkedIn". "Draft outreach" stays a ChatGPT-side step, as the canon's suggested next action allows.
- Matching reads only Hostinger. The step runs in the hourly `finish` job after publish because the Jobs Notion and database settings are there. It is continue-on-error and absent from the lane-failure list, so a network failure cannot fail the Jobs run.

## K. Non-goals and rejected architecture

No second scheduler, workflow family or workflow input. No local files, commands or hand-maintained lists. No Google client in V7. No small caps imposed by V7. ChatGPT writes only into the one handoff and never becomes the datastore. No graph database. No profile scraping, crawler, logged-in automation or browser. No TinyFish. No dependency on a chat connector at runtime. No automatic outreach, no connection requests, no drafted message text from V7. No relationship strength, referral likelihood or closeness scoring. No generic CRM framework. No paid enrichment vendor. No email addresses stored by default. No Jobs-to-Network import, and no new edit to the Jobs tables.

## L. Decision-log proposals (draft; `docs/DECISIONS.md` is not edited)

- Network Intelligence is its own OS package; the Jobs-to-Network glue lives in `lifeos/sources/`.
- The whole network (about 5,000 connections and their employers) lives in derived, automatically maintained Hostinger tables beside the Jobs tables (a bounded noncanonical machine-state store under the Platform Canon, approved by Jim); the Sheet stays canonical for who is connected, and V7 never reads it.
- ChatGPT acquires (its Drive access and connected LinkedIn app) and sends batches; V7 validates, applies in chunks, records and matches. ChatGPT is never the network datastore, and V7 works, with aging data, when ChatGPT is unavailable (D54).
- V7 imposes sanity limits on a batch, not throughput quotas; the pace is the connector's.
- Company aliases are learned from Jim's Same company ticks; there is no typed alias list and no other fuzzy matching in V7.
- Dismissals and confirmations are copied from ticks in the owned block into tables, so rewriting the block cannot resurrect a dismissed lead.
- A position is "listed as of an observation"; a superseded position does not assert that employment ended, and no tenure dates are invented.
- Evidence is an observation with provenance, never authority; conflicts never silently overwrite; absence from a batch proves nothing.
- Person identity is the normalized profile address; a name alone never merges two people.
- Matching is stateless over the whole roster, by equal company key after alias resolution, and shows at most five leads with visible reasons and freshness.
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
| 7 | Matching is database-only over the whole roster and at most about ten Notion calls; applying a batch is chunked to fit the runtime budget. |
| 8 | `python -m lifeos.run network-*` stages, dry run by default; the V7 steps (apply a batch in chunks, surface leads) run in the hourly `finish` job as non-fatal steps; no new `hourly.yml` input, no new `domains.yml` job. |
| 9 | ChatGPT's connected LinkedIn app, run by ChatGPT; V7 cannot call it, so its output arrives as batches through a door to Hostinger (section N). |
| 10 | Only forward accumulation, one export at a time. Retroactive history is not promised. |
| 11 | MVP adapters: the Sheet roster and the connected app, both via ChatGPT. Later: per-site public pages. No hand-correction path. |
| 12 | No export import by V7: ChatGPT reads the Sheet already in Drive and sends it as the first ROSTER batch (section N); nothing is local and V7 never reads Drive. |
| 13 | Profile-address identity; name-only collisions stay unmerged and are counted. |
| 14 | The schema allows several current positions per person; the export can only show one, so concurrent roles wait for a richer source. |
| 15 | Fresh to 45 days, aging to 120, stale beyond, always with the date. Defaults, tunable. |
| 16 | People, positions, the batch ledger (and the inbox for door A) in NET-1; events in NET-2; dismissals and aliases in NET-4. |
| 17 | Yes, stateless on demand; the page's marker hash is the only stored idempotency. |
| 18 | All of it deterministic in V7. Message drafting stays human or ChatGPT-side and outside V7. |
| 19 | Person rows and addresses stay in Hostinger. Only the lead's display name, listed company and title, the observation date and a fixed reason appear in Jim's private Notion block (decision 5 covers whether to show more or less). |
| 20 | First as counts from a stage and a push line after each import, then as a private page or a region once ownership is decided. |

## N. The door to Hostinger, and ChatGPT's side (cloud, automatic)

**Fastest path to the import (October 5, Jim: "we need the fastest solution for the import").** The original LinkedIn export, `LI_Connections_20260907.csv`, is already attached to the Notion page "LI Connection Database & Integration". That file is the whole roster, in the cloud, in LinkedIn's own clean format, and V7's existing Notion integration can read it. So door B needs **no new setup beyond sharing that one page**, no ChatGPT step, no Drive and no new component:
1. **Done (code, tested on invented data, pushed):** `python -m lifeos.run network-inspect` (manual workflow `network.yml`) reads that attachment in memory and prints counts only. No table, no write.
2. **Jim, about two minutes:** share the page with the Jobs Notion integration, add the page identifier as the secret `NETWORK_HANDOFF_PAGE_ID` (steps in `docs/SETUP.md`), run the workflow. The one line it prints is the real file's shape: rows, blanks, duplicate profile keys, employer-less companies, distinct employers.
3. **Jim, one word:** approve the three tables (`v7_network_people`, `v7_network_positions`, `v7_network_batches`).
4. **Next code (the importer):** reuse the same reader and parser, apply the roster in chunks inside the runtime budget, resumable and replay-safe. First live run is a manual workflow (dry first, then live): about 5,000 people in Hostinger.
5. **Refresh after that:** attach a newer CSV to the same page (Jim in ten seconds from his phone, or ChatGPT if it can attach a file) and the next tick imports it; ChatGPT's CHECK batches use the same door later.
This is the fastest because it removes every unknown from the critical path: no Google setup, no dependence on what ChatGPT's connector can do, no Hostinger write path to build. The data still lives only in Hostinger; Notion is the doorway for a moment. NET-0's ChatGPT prompt remains useful for the *refresh* (pace and limits), but the import no longer waits for it.


**The one open technical question.** ChatGPT must get its batches into Hostinger. Nothing in the existing contracts lets ChatGPT write to the Hostinger database: the database is reached by V7 through a locked SSH tunnel whose key is "for the database tunnel only" (D51 amendment), and in the existing design ChatGPT hands evidence to a runtime that persists it. Three doors, and the choice turns on a fact only ChatGPT can report:

| Door | How it works | What it needs | Verdict |
|---|---|---|---|
| **A. Direct** | ChatGPT inserts batch rows into `v7_network_inbox` in Hostinger itself | A way for ChatGPT to write to Hostinger. None exists today; building one means a small token-protected, insert-only HTTPS endpoint on the Hostinger hosting beside the database (a new component and secret, to be approved), or a ChatGPT connector that already reaches the database (if you have one, tell me what it is) | Best if it exists: V7 then reads only its own database. |
| **B. File drop** | ChatGPT attaches the batch as one file to a private Notion page; V7's tick downloads the attachment and loads it into Hostinger. The data lives only in Hostinger; Notion is the doorway for a moment | Nothing new: it is the production contract's existing browser-evidence handoff pattern (a private page with a Files property), and the Notion connection ChatGPT and V7 already share. One file holds a whole roster; no per-row writes | **Recommended default.** Works with what exists. |
| **C. Drive** | ChatGPT drops a file in Drive; V7 reads it through a service account | A Google client in V7 | **Vetoed by Jim.** |

Whichever door, V7's importer is the same: `next_batch(door)` is the only door-specific code, and the validator, chunked apply and history tables do not change.

**What no door fixes:** how ChatGPT assembles 5,000 rows without copying them by hand through its own context (a transcription risk V7 cannot detect row by row). V7 guards against it with the validator and the implausibly-small-roster hold, but the real answer is mechanical file handling on ChatGPT's side. NET-0 asks.

**One-time setup:** Notion only for door B (a private "Network Handoff" page with a Files property, shared with V7's integration and ChatGPT, and its identifier as a repository secret); for door A, the endpoint or connector instead. Nothing in Google changes. Tables are approved slice by slice.

**ChatGPT's module, in plain words (draft; not added to the production contract, which is on hold):** "Network Lookups", run in ChatGPT's own scope on its existing schedule. Roster: build the full list of Jim's connections with each person's profile address, name, current company and title, and today's date, from the connected LinkedIn app as far as it can list connections, otherwise from the connections Sheet in Drive; send it as one ROSTER batch on a schedule you set. Checks: for jobs in the Job Ledger that are new, active or applied, find the connections at those companies and verify their current company and title through the app, send them as CHECK batches, and send the stalest people next, at whatever pace the app allows. Mark the source of each row (`SHEET` or `LINKEDIN_APP`). If the app refuses or is unavailable, send nothing and stop for the day. Never message anyone, never write anywhere but the handoff, never guess a title, never infer closeness, influence or willingness to refer.

**NET-0 prompt for ChatGPT** (nothing written, no names printed): "List the tools and connectors you have in the LIFE OS Daily Runs automation. For each of these, answer with capability and numbers only. (1) The LinkedIn app: can it list ALL my connections with each person's current company and title? How many per call, and is there a daily or monthly cap? Can it look up one named person? Does it return tenure or history? (2) Can you read my LinkedIn Connections sheet in Drive, and can you copy it or attach it as a FILE (not by retyping rows) to a Notion page, to another Drive folder, or to a GitHub repo? What is the largest file you can attach? (3) Can you write to, or run SQL against, the Hostinger database or call any HTTPS endpoint? If yes, name the connector. (4) How many rows can you handle in one run without typing them out yourself? (5) Which Sheet column headings are there (headings only)?"

Rejected: any V7 access to Drive or a Sheets reader; any local import or download; an intake folder watched by a V7 job; a Notion row per person (5,000 rows through Notion is slow and fragile); ChatGPT writing arbitrary SQL against Hostinger; workflow inputs and artifacts (world-readable on a public repository); committing any export.

## O. Decisions Jim must make

Settled by Jim: the whole roster in Hostinger; everything in the cloud and automated; no local sheet, commands or hand-kept lists; ChatGPT keeps the data fresh through its connected app and Drive access; V7 does not touch Drive; no small caps; learned aliases; no correction command.

1. **The door** (section N): the one open question. Default B (a private file drop in Notion that V7 loads into Hostinger); A if ChatGPT can already write to Hostinger (tell me how) or you want a small endpoint built. NET-0's prompt answers the facts.
2. **Triggers.** The canon's four; the first slice covers a qualified job (`ADMIT`) and an applied job. `REVIEW` jobs are not included by default.
3. **Fields and email.** Email addresses are not stored. The Notion block shows display name, listed company and title, observation date, a profile link and a fixed reason.
4. **The canon clause** (section 0a): on hold, as you asked.
5. **Erasure and retention for people** (recommended: a `REMOVED` status that excludes a person from matching, set when Jim asks).
6. **Approve the tables**, slice by slice.

## P. First build slice

```text
FIRST BUILD SLICE:
NET-0 ChatGPT capability check (no V7 code), then NET-1 door, validator and chunked importer

WHY FIRST:
NET-0 settles what nobody here has verified: whether ChatGPT's LinkedIn app can list all 5,000 connections with employers, its limits, how a batch can reach Hostinger without retyping rows, and the Sheet's columns. NET-1 then builds the part V7 owns (validation, identity, chunked apply, history) against real facts.

EXPECTED FILES (NET-1):
lifeos/network/__init__.py, identity.py, inbox.py, validate.py, store.py, one line in lifeos/run.py, a step in the hourly finish job, tests/network/*, a docs/SETUP.md section

SCHEMA:
NONE for NET-0. NET-1: v7_network_people, v7_network_positions, v7_network_batches (and v7_network_inbox for door A), after Jim's approval.

ACCEPTANCE:
NET-0: the five answers in the prompt, by capability and number only. NET-1: validator and identity tests pass on synthetic batches (tracking-parameter and case variants of profile addresses, same-name collisions, blank fields, own-name companies, future dates, oversized and implausibly small batches, commas and emoji in names); a 5,000-row synthetic roster applies in chunks and resumes after a simulated crash; a dry run prints counts only; nothing is written anywhere; replay is a no-op; the full suite passes.

DO NOT BUILD YET:
The change-event digest, aliases, the Notion block, any Google or Drive client, any local importer, relationship evidence.
```
