# Network Intelligence: scope and roadmap (scoping only, no code)

Status: scoped for Jim's review. Nothing here is built, and this document changes no schema, secret, workflow or `docs/DECISIONS.md`.
**Settled by Jim:** the feature is driven from a **separate table in the Hostinger database already used for Jobs OS**, nothing in matching reads a Google Sheet, and **everything runs in the cloud and is automated: no local files, no commands, no lists Jim maintains by hand.** Revised after re-reading the live canon page (section 0a), which turned out to be compatible with this. Assumed defaults, change on request: the first slice's triggers are a qualified job (`ADMIT`) and an applied job. Still open: the decisions in section O.

## 0. The shape of it in one paragraph

Network Intelligence is a small temporal people graph in Hostinger (tables beside the Jobs tables) that makes Jobs OS smarter: when a job is admitted, it lists the few people in Jim's network who currently or formerly list that employer, with the evidence and its age. Jim's LinkedIn connections export already lives in his Google Drive as a Sheet. A small cloud job, run on V7's own tick, notices when a new export appears in a Drive intake folder, reads it, and brings the Hostinger tables up to date, recording who changed jobs. Matching and the Notion surface read only the Hostinger tables; the Sheet is read only by that sync job, and only to refresh the tables. Nobody downloads a file, runs a command or edits a list. The one thing no cloud job can do is get a fresh export out of LinkedIn, which hands it over only when Jim asks; V7 reminds him when the newest export gets old. Every claim carries its source and the date it was observed; nothing is inferred about relationship strength.

Why the refresh is an export and not a live lookup: the brief's targeted lookups run through a connected chat app. V7 must run with chat unavailable (D54) and GitHub Actions cannot reach a chat connector, so V7 has no people-evidence source at runtime. The browser paths are closed by D12 and D14, LinkedIn automation is a canon non-goal, and TinyFish Fetch is reserved for job pages (D11, D12 amendment). A periodic export is the one permitted bulk source that needs no new decision.

## 0a. The live Notion canon, re-read, and what I got wrong

I first said the Notion page "LI Connection Database & Integration" forbids a separate connection database and needs editing. **That overstated it.** Re-read in full, it says:

- **Canonical dataset:** Jim's existing Google Sheet of connections (a dated export, in his Drive). Still true: the Sheet stays where Jim's data lives, and he keeps adding exports to Drive as he does today.
- **"LIFE OS may read and normalize the Sheet for matching/search"**, and **must not create "a second CRM or independently editable connection database merely for this feature."** Tables that a job fills automatically from the Sheet, that nobody edits by hand, and that can be rebuilt from the exports are a normalization, not an independently editable database. This design keeps to that: Jim's only inputs are decisions (a dismissal, a "same company" confirmation), never data.
- **Four triggers:** a newly qualified open job in the US Remote or Scale-Up experience; Jim moves an opportunity into active pursuit or applied; an interview is scheduled or confirmed; Jim explicitly asks. Section H.
- **Matching:** normalized employer name and known aliases, ranked only by context actually in the data. Never infer closeness, influence or willingness to refer. Section H.
- **Output:** a compact shortlist: person, current role and company, why relevant, which opportunity, a recommended networking objective, a suggested next action (draft outreach, open LinkedIn, dismiss), and the data's freshness. Section J.
- **Lifecycle:** a recommendation is derived career context, not a new durable person record; dismissing one suppresses the same unchanged recommendation for the same opportunity; new evidence brings it back. Sections E and J.
- **Stale or unavailable data** is shown as a limitation. **Non-goals:** a second CRM, scraping beyond Jim's supplied data, an inferred social graph, automatic messaging, any automatic claim that a connection can refer him.

**What is still worth adding to that page.** The Platform Canon allows a "bounded noncanonical machine-state store" only when Jim explicitly approves it and **the owning domain canon defines its scope.** Jim has approved it in this session; the LI page does not yet define the scope. Suggested clause, for Jim's go: "Network matching and change history run from derived Hostinger tables that a V7 job fills automatically from the Sheet exports in the Drive intake folder. The Sheet stays canonical; the tables are rebuildable from the exports except the change history, hold no hand-edited data, and store only dismissals and company-name confirmations as Jim's own decisions." That is an addition, not a contradiction, so nothing in the page needs reversing.

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
| Export parser and importer | MISSING | |
| Person identity (profile-URL key, collision handling) | MISSING | |
| Company key | PARTIAL | `core()` is reusable; a key built as the joined core tokens needs no platform change. |
| Transactional write | MISSING | A tiny platform helper is needed so a position change and its event commit together. |
| Change detection and events | MISSING | |
| Freshness policy | MISSING | |
| Live current-state source | MISSING and none permitted | Replaced by re-export diff. |
| Job-to-network match | MISSING | |
| Surface on the job page | MISSING | No body-append writer exists; `report_region` is the nearest. |
| Trigger signal | EXISTS | Published job with a page id, a verified time and `admission = ADMIT`. |
| Non-fatal scheduling slot | EXISTS | The `finish` job after `publish`. |
| Alerts | PARTIAL | Layer exists; coupling with Jobs alerts needs the shared fix in `docs/BIG_FEATURES_ROADMAP.md`. |

## C. Final architecture

```text
Google Drive intake folder (Jim's LinkedIn exports, already in the cloud)
        |  `network` job on V7's tick: lists the folder, reads the newest export only when it is new (counts only)
        v
parse + validate (a full result or an error, never partial)
        v
identity: profile-URL key (never name alone)  +  company key (core tokens)
        v
reconcile against stored positions  ->  same | title changed | company changed | new person | ambiguous
        v
Hostinger: v7_network_people, v7_network_positions, v7_network_events, v7_network_imports   (one transaction per person)
        |
        +----> change events -> push on import ("N changes, M at companies in your pipeline") + private digest
        |
        +----> match(job)  <---- admitted, published jobs (read through a sources adapter; reads Hostinger only)
                    v
             NetworkLead list (at most 5, each with reasons and freshness)
                    v
        marker-owned "Network Leads" block at the end of the Ledger page body (Dismiss and Same-company ticks)
```

Placement: `lifeos/network/` (identity, store, sync, reconcile, match, render) imports `platform` only. The Drive read goes through a new read-only `lifeos/platform/gdrive.py` built on the same service-account pattern as `gcal.py` (a token signed with stdlib and the system openssl; no new dependency). The stage that reads admitted jobs and calls `network.match` lives in `lifeos/sources/` (it may import `jobs` and `network`), so neither OS imports the other and the D17 boundary stays as it is.

## D. Source feasibility

| Source | Viable | Gives | Bulk or targeted | Auth and cost | MVP |
|---|---|---|---|---|---|
| Google Drive intake folder holding Jim's LinkedIn connection exports (the Sheet he already has), read by a cloud job | Yes; his own data, in the cloud, read-only through the Google service account V7 already uses for Calendar | The export's columns (LinkedIn's seven; the inspector reports blanks and oddities by count) | Bulk | Drive API enabled once, one folder shared with the service account; free | **MVP: the seed and every refresh, fully automatic** |
| The LinkedIn export itself | Yes, member-initiated and permitted; **the one step no cloud job can do** (no API, and automation is closed by D12, D14 and the canon's non-goals) | Name, profile URL, current listed company and position, connection date, sometimes email | Bulk | Free; Jim requests it from LinkedIn and drops it in the folder; V7 reminds him when the newest is old | The only human act in the loop |
| LinkedIn through the connected chat app | Not at V7 runtime | Targeted current lookup | Targeted | Chat connector | Reject for V7: no hand-relayed observations either, since nothing in this feature is typed in by hand. |
| V7's LinkedIn job resolver | No | Jobs only | n/a | n/a | Reject: job-only, must not become a people crawler |
| Employer team or bio pages, personal sites, speaker pages | Possible per site | Current role evidence | Targeted | Free | Later, one adapter per site with its own authority rules |
| Browser automation or logged-in scraping | No | | | | Reject (D12, D14; also the canon's non-goal) |
| TinyFish Fetch or Search | No | | Shared job quota | Free quota, reserved | Reject: job pages only (D11, D12 amendment) |
| Email signatures and domains from Jim's mail | Possible | Employer change evidence | Targeted | Existing mail access | Later (NET-6, relationship evidence) |
| Address-book contacts | Possible | Company and title fields | Bulk | New OAuth scope | Reject for MVP: a new grant (People Identity may link a row to Apple Contacts later; the canon allows it) |
| Paid enrichment vendors | Yes | | | Recurring cost | Reject; optional and out of MVP |

What the export can and cannot say: it carries each connection's single currently listed position at the moment of export. It gives no tenure dates, no history and no second concurrent role, and some fields are blank. So history accumulates from launch forward, one export at a time, and each stored fact says "listed as of the export of date X", never "employed from, to". A blank field never demotes a stored position. Whether the real file has these columns and how many are blank is proven by the dry-run inspector in NET-1a, by count, before any write.

## E. Hostinger schema (minimum, added by slice)

Added only after Jim approves each slice's tables. Table creation happens only on a live run. **No table is edited by hand.** Everything in them is written by the sync job or copied from a tick Jim makes in Notion.

`v7_network_people` (NET-1b)
- `id` (key), `person_key` unique (hash of the identity key), `url_key` unique and nullable (normalized profile address: scheme, host, case, query string and trailing slash removed), `display_name`, `connected_on` date, `first_seen`, `last_observed`, `status` (`ACTIVE`, `REMOVED`), `history_coverage` (`SEED_ONLY`, `PARTIAL`, `UNKNOWN`), timestamps.
- No email column; if the export has an email column the sync ignores it (decision 4). The brief's `refresh_state` and `refresh_due_at` are dropped: with export diffing there is no per-person refresh schedule.
- Identity rule: the URL key is the identity. With no URL, fall back to normalized name plus company key, and treat any collision as ambiguous and unmerged. Two people with one name never merge; a changed employer never creates a new person.

`v7_network_positions` (NET-1b)
- `id`, `person_id`, `company_key` (joined `core()` tokens), `company_name`, `title` nullable, `position_state` (`CURRENT`, `SUPERSEDED`), `first_observed`, `last_observed`, `source_ref` (the export's date, never a URL or file id), `source_observed_at`, `material_hash` (unique per person, company, title and export), timestamps.
- `SUPERSEDED` means "no longer the listed position as of a later observation". It does not assert that employment ended, so no end date is invented.
- Index on (`company_key`, `position_state`) for matching.

`v7_network_imports` (NET-1b)
- `id`, `file_key_hash` unique (a hash of the Drive file id; the id itself stays out of the table), `export_date` (from the file's name, else its modified time), `modified_at`, row and outcome counts, `status` (`COMPLETE`, `FAILED`), `imported_at`. This is the idempotency record ("have I already imported this file at this version?") and the source of the freshness date.

`v7_network_events` (NET-2)
- `id`, `person_id`, `event_type` (`PERSON_IMPORTED`, `CURRENT_POSITION_CONFIRMED`, `COMPANY_CHANGED`, `TITLE_CHANGED`, `PROFILE_UNRESOLVED`, `CONFLICT_DETECTED`), `old_position_id`, `new_position_id`, `observed_at`, `event_hash` unique, `created_at`. Append-only and idempotent. This is the history a Sheet cannot keep, and the reason the tables exist.

`v7_network_dismissals` (NET-4)
- `id`, `job_page_id`, `person_id`, `evidence_hash` (the lead's reason text and observation date), `dismissed_at`. Copied from the Dismiss tick in the owned Notion block so that rewriting or deleting the block cannot resurrect a dismissed lead. A lead returns only when its `evidence_hash` changes (new evidence).

`v7_network_aliases` (NET-4, learned, never typed)
- `id`, `alias_key` unique, `company_key` (the key it resolves to), `confirmed_at`. A row appears only when Jim ticks "Same company" on a possible match (section H). The canon asks for "known aliases"; this is how they are learned without anyone maintaining a list.

**How the tables stay up to date: automatically.** The `network` job (section C) checks the intake folder on each tick, imports a new export once, and writes events. Nothing writes to the tables from a person's keyboard. If a position is wrong, it is wrong in LinkedIn's export, and the next export fixes it.

Not added: a raw-observation table (positions already carry source and observed time), a recommendations table (matching is computed on demand), a refresh queue, a hand-correction path.

One platform addition: a small transaction context in `platform/db.py`. The connection is autocommit, and a position change must commit together with its event, otherwise a crash between them would lose the event for good on replay.

## F. Contracts (architecture level)

```python
@dataclass(frozen=True)
class PersonObservation:
    url_key: str | None; display_name: str; connected_on: date | None
    company_name: str | None; title: str | None
    source_ref: str; observed_on: date      # columns found by header name, reported by the inspector

@dataclass(frozen=True)
class ImportReport:    # counts only
    rows_read, people_created, people_matched, ambiguous, positions_created,
    positions_unchanged, positions_superseded, malformed, events_planned

def newest_export(listing) -> ExportFile | None         # by the date in the name, else modified time; ignores everything else in the folder
def parse_export(rows, observed_on) -> Iterator[PersonObservation | Malformed]
def identity_key(obs) -> IdentityKey | Ambiguous
def company_key(name) -> str                       # " ".join(core(name))
def plan(existing: PersonState | None, obs) -> Plan   # pure: writes + events, no I/O
def apply(connection, plan, live) -> Applied        # one transaction, read back

@dataclass(frozen=True)
class NetworkLead:
    person_id: int; display_name: str; basis: Basis        # CURRENT_AT_COMPANY | FORMER_AT_COMPANY
    company: str; title: str | None
    reasons: tuple[str, ...]; observed_on: date; freshness: Freshness
    objective: str                                           # fixed template, not generated

def match(job, connection, limit=5) -> list[NetworkLead]   # stateless
def render_block(leads) -> list[NotionBlock]               # pure
```

The Drive reader returns file listings and rows and never writes; `apply` is the only writer to the database.

## G. Refresh algorithm (automatic export diff)

1. On each tick the `network` job lists the intake folder (one Drive request) and picks the newest export. If `v7_network_imports` already holds that file at that version, it stops: most ticks cost one request and write nothing.
2. Otherwise it reads the whole export first. Any structural error fails the run before a write (a full result or an error, never partial) and raises one alert; the previous data stays in use.
3. For each row, derive the identity key and company key, then compare with the stored current position:
   - same company key and same title: confirm (`last_observed` and `source_observed_at` advance); no event.
   - same company key, different title: previous position `SUPERSEDED`, new position `CURRENT`, one `TITLE_CHANGED` event. No promotion is inferred.
   - different company key: previous `SUPERSEDED`, new `CURRENT`, one `COMPANY_CHANGED` event.
   - blank company or title where a value was stored: no change and no event; the stored value simply ages.
   - a company value that is the person's own name or freelance, independent or self-employed wording is recorded as given but flagged `NOT_AN_EMPLOYER` and never matches a job.
   - person not seen before: create the person and the position, one `PERSON_IMPORTED` event.
   - two rows that resolve to one identity with conflicting positions, or a collision with no URL: counted as ambiguous, no write for that person.
4. People absent from a newer export are left untouched. Absence from one export proves nothing, so nothing is demoted or removed for it.
5. Replay of the same file is a no-op: unique hashes on positions and events, so a second run creates zero people, zero positions and zero events.
6. **Freshness and the reminder.** Freshness is the export's date and is always shown with it (the canon's stale-data rule: a stale lead says so). Proposed defaults, held as named constants: `FRESH` up to 45 days, `AGING` to 120 days, `STALE` beyond that, and `CONFLICTED` while an ambiguity flag is set. When the newest export passes 45 days the job raises one alert, once, saying a fresh export is due. That is the only thing V7 ever asks Jim to do here.
7. After a successful import the job pushes one line (counts only: how many changes, how many at companies currently in the Job Ledger) through the existing alerts layer, and the change events feed a private digest (NET-5).
8. I/O is the Drive API and the database. Drive calls use the shared retry helper with fixed error codes; an unreachable folder is `DEGRADED`, never "no new export". Database failures use the existing fixed `StoreError` codes and roll the person back.

## H. Job-to-network recommendation algorithm

Triggers (from the canon): (1) a newly qualified job, meaning a published job with a page id and a verified time whose stored `admission` is `ADMIT` (`REVIEW` jobs also publish but are not included by default); (2) Jim moves a job to active pursuit (the Ledger's Applied checkbox or Applied-on date); (3) an interview scheduled or confirmed; (4) Jim explicitly asks (a manual stage that takes a company). **The first slice covers 1 and 2**, because both surface on the Ledger page; 3 and 4 need a surface decision, since Hiring Pipeline pages are human-owned and never written. Matching is stateless and reads current Hostinger state.

- Company match is by equal company key, after alias resolution. A person flagged `NOT_AN_EMPLOYER` never matches.
- **Possible matches, automatic, never silent.** When no key is equal but one company key is a whole-word prefix of the other ("meta" and "meta platforms"), the person is shown last, labelled "Name differs: same company?", with a Same company tick. Ticking it records a learned alias (`v7_network_aliases`) that applies to every later job and import. Leaving it alone shows nothing more. No other fuzzy matching exists, so a missed alias is a miss, never a wrong person, and nobody types an alias.
- Tier 1: a `CURRENT` position at the company. Tier 2: a `SUPERSEDED` position at the company ("previously listed there"). Tier 3: the possible matches above. Within a tier, order by function overlap between the stored title and the job's role (shared role tokens), then by freshness, then by name for a stable order. No numeric score is stored; every ordering factor is visible.
- A person with no company match is never shown, so weak signals cannot fill slots. At most five leads.
- Reasons are fixed templates built from stored facts, for example "Listed at Acme as Director of Product, as of the September export (45 days)" or "Previously listed at Acme; later listed elsewhere". Nothing about closeness, referral likelihood or willingness is ever generated.
- A stale lead is shown with its age and a verify-first note, never as current.
- A lead whose `evidence_hash` is in the dismissals table is not shown for that job; new evidence changes the hash and it returns.
- Objectives are fixed templates by basis (for example, learn how the team is organised for a current employee). Drafting message text is not part of V7; that stays a human or ChatGPT-side step.

Examples (synthetic):

1. Admitted job at Acme. Network holds Jane (current at Acme, fresh), Raj (superseded at Acme, aging) and Tom (current at WidgetCo). Result: Jane then Raj; Tom is omitted.
2. Admitted job at WidgetCo. The only match is Sam, current at WidgetCo but 200 days old. Result: Sam, labelled STALE with its date and a verify-first note.
3. Admitted job at a company nobody in the network lists. Result: nothing is written; no empty block is created.

## I. Roadmap (each slice has a product boundary of its own)

| Slice | Outcome | Likely files | Schema | Tests and UAT | Depends on | Risks | Size | PRs |
|---|---|---|---|---|---|---|---|---|
| **NET-1a Drive inspector (dry run)** | A cloud dry run reads the newest export from the Drive intake folder and prints counts only: files seen, export date and age, columns found, rows, blank URL, blank company, own-name or freelance companies, duplicate URL keys, distinct company keys, ambiguous identities. Proves V7 can reach the folder and the file's real shape before anything is stored. | `lifeos/platform/gdrive.py` (read-only, on the `gcal.py` pattern), `lifeos/network/{identity,parse}.py`, `run.py` line, a `network` job in `domains.yml` with its own `DOMAIN_SECRETS` entry, `docs/SETUP.md` | none | table-driven parser and identity tests (tracking parameters, case, trailing slash, same-name collision, blank fields, own-name company), a fake Drive listing, counts-only output test | Jim's one-time setup (section N) | The real export's oddities (commas, emoji, credentials in names) may break a naive parse; that is exactly what this finds | M | 1 |
| **NET-1b Sync and first import** | The first export lands in Hostinger without duplicates and replay creates nothing; the job then runs on every tick and writes only when a new export appears. | `lifeos/network/{store,sync}.py`, `platform/db.py` transaction helper, tests | `v7_network_people`, `v7_network_positions`, `v7_network_imports` | replay twice equals once; ambiguous people unmerged; dry run creates no tables; read-back equals written; an unreachable folder is DEGRADED and keeps the old data | NET-1a, Jim's table approval | PII handling: logs counts only | M | 1 |
| **NET-2 Change events and reminder** | A second export yields exactly the right change events; the push line and the 45-day reminder fire once. | `lifeos/network/{reconcile,freshness,alerts}.py` | `v7_network_events` | title change, company change, blank field, new person, absent person, conflicting rows; each yields the specified events exactly once; reminder raises once | NET-1b, and the alert fix (P2) | Changes are only as fresh as the export cadence | M | 1 |
| **NET-3 Job match (no surface yet)** | A dry-run stage reports, by count, how many admitted jobs have leads and how many leads each. | `lifeos/network/match.py`, `lifeos/sources/network_leads.py`, `run.py` line | none | the three examples below, tier order, five-lead cap, no weak fill, possible-match tier, own-name company never matches | NET-2 | A missed alias gives a miss, not a wrong match | M | 1 |
| **NET-4 Surface** | Leads appear on the job page in one machine-owned block with Dismiss and Same-company ticks; unchanged leads cause no write; both ticks are remembered. | marker-block writer generalized from `report_region`, one step in the `finish` job after publish (continue-on-error, not in the failure list) | `v7_network_dismissals`, `v7_network_aliases` | owned block replaced, nothing else touched, read-back, hash skip, page-gone and Ledger-target guards, tick read-back and persistence | NET-3, decision 3 | A human editing inside the owned block loses the edit; documented | M | 1 |
| **NET-5 Change digest** | "Who moved recently" and "who has listed company X", written automatically after each import as a private page or region. | `lifeos/network/queries.py`, a stage | none | query tests; region ownership decided before any report region | NET-2 | Region ownership is a product decision (roadmap decision 3) | M to L | 1 |
| **NET-6 Relationship evidence (later)** | Accepted mail and calendar evidence of real contact. | later | later | later | core value proven | Easy to overreach into relationship scoring, which is out of scope | L | later |

NET-0 from the brief is absorbed: its work is the NET-1a inspector plus the decisions in section O. Triggers 3 and 4 from the canon (an interview, an explicit ask) are later slices and wait on a surface decision.

## J. Surfacing mechanics (NET-4 in detail)

- One top-level toggle block at the end of the Ledger page body, titled "Network Leads (n)". Its first child paragraph begins with a marker (`v7-net:1`) followed by a short hash of the lead set. The marker, not position, is how ownership is decided.
- An unchanged hash means no write. A changed hash deletes the owned block and appends the new one, then reads the page back. Nothing outside the owned block is read for change or written.
- The existing description marker at the top of the body (`v7-jd:1`) and the properties the publisher and fit sync own are never touched, so `readback.py`'s first-three-blocks check keeps passing.
- Before any write: `ledger.verify` on the data source, page not archived or in trash, description marker still present. A failed guard writes nothing.
- Each lead has a Dismiss tick, and a possible match also has a Same company tick. V7 reads both before any rewrite and copies them to the dismissals and aliases tables. An unchanged dismissed lead stays hidden; it comes back only when new evidence changes its `evidence_hash` (the canon's rule).
- The block shows the data's freshness (the export's date) on every lead, per the canon, and each lead carries a link to the profile page for "open LinkedIn". "Draft outreach" stays a human or ChatGPT-side step.
- Matching reads only Hostinger in the `finish` job, using the database settings that job already holds for the Jobs publish; no Google secret is involved there (the Drive read happens in the separate `network` job).
- The step runs in the hourly `finish` job after publish because the Jobs Notion secrets exist only there. It is continue-on-error and absent from the lane-failure list, so a network failure cannot fail the Jobs run.

## K. Non-goals and rejected architecture

No second scheduler, workflow family or workflow input. No local files, commands or hand-maintained lists. No graph database. No profile scraping, crawler, logged-in automation or browser. No TinyFish. No dependency on a chat connector at runtime. No automatic outreach, no connection requests, no drafted message text from V7. No relationship strength, referral likelihood or closeness scoring. No generic CRM framework. No paid enrichment vendor. No email addresses stored by default. No Jobs-to-Network import, and no new edit to the Jobs tables.

## L. Decision-log proposals (draft; `docs/DECISIONS.md` is not edited)

- Network Intelligence is its own OS package; the Jobs-to-Network glue lives in `lifeos/sources/`.
- The data lives in derived, automatically maintained Hostinger tables beside the Jobs tables (a bounded noncanonical machine-state store under the Platform Canon, approved by Jim); the Sheet exports in Drive stay canonical, and matching never reads them.
- A `network` job on V7's tick syncs the newest export from a Drive intake folder; nothing is downloaded, run or edited by hand. The LinkedIn export itself is the only human act, and V7 reminds Jim when it is old.
- Company aliases are learned from Jim's Same company ticks; there is no typed alias list and no other fuzzy matching.
- Dismissals and confirmations are copied from ticks in the owned block into tables, so rewriting the block cannot resurrect a dismissed lead.
- A position is "listed as of an export"; a superseded position does not assert that employment ended, and no tenure dates are invented.
- Person identity is the normalized profile address; a name alone never merges two people.
- Matching is stateless, by equal company key after alias resolution, and shows at most five leads with visible reasons and freshness.
- Leads are written as one marker-owned block on the Ledger page body, with no new Notion property.
- Export files never enter the repository, workflow inputs or artifacts.

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
| 8 | `python -m lifeos.run network-*` stages, dry run by default; the sync runs from `domains.yml` and the surface step from `finish`; no new `hourly.yml` input. |
| 9 | None permitted at runtime. The automatic export diff replaces it. |
| 10 | Only forward accumulation, one export at a time. Retroactive history is not promised. |
| 11 | MVP adapter: the Drive export reader. Later: per-site public pages. No hand-correction path. |
| 12 | A cloud job reads the Drive intake folder (section N); nothing is local. |
| 13 | Profile-address identity; name-only collisions stay unmerged and are counted. |
| 14 | The schema allows several current positions per person; the export can only show one, so concurrent roles wait for a richer source. |
| 15 | Fresh to 45 days, aging to 120, stale beyond, always with the date. Defaults, tunable. |
| 16 | People, positions and the import ledger in NET-1b; events in NET-2; dismissals and aliases in NET-4. |
| 17 | Yes, stateless on demand; the page's marker hash is the only stored idempotency. |
| 18 | All of it deterministic in V7. Message drafting stays human or ChatGPT-side and outside V7. |
| 19 | Person rows and addresses stay in Hostinger. Only the lead's display name, listed company and title, the observation date and a fixed reason appear in Jim's private Notion block (decision 5 covers whether to show more or less). |
| 20 | First as counts from a stage and a push line after each import, then as a private page or a region once ownership is decided. |

## N. Intake path (cloud, automatic)

**There is no seed step and no local file.** The first export V7 finds in the intake folder is the seed, and every later one is a refresh, all read by the `network` job.

One-time setup, done once in a browser (none of it repeats):
1. **Enable the Google Drive API** in the Google Cloud project that owns the service account V7 already uses for Calendar (one console switch).
2. **Make an intake folder** in Drive (suggested name "LinkedIn Exports", a sub-folder so the service account sees nothing else in the LIFE OS folder), move the existing 2026-09-07 Sheet into it, and **share the folder with the service account's email as Viewer.** From then on, a new export is just a new file or Sheet dropped in that folder, named with its date as the existing one is.
3. **Add one repository secret** holding the folder's identifier (it is an identifier, and this repository is public), and approve the `network` job's isolated entry in `DOMAIN_SECRETS` (the Google credential, the folder identifier and the database settings, nothing else).

Reading a Sheet needs no Sheets API: the Drive API exports its first tab as CSV in memory. Output is counts only; the file is read, parsed and discarded, never written to the repository, an artifact, a log or a workflow input. If the export has an email column the sync ignores it.

Rejected: any local import or download (Jim's instruction: everything in the cloud); a Sheets reader that matches straight from the Sheet at runtime (matching reads Hostinger only); workflow inputs and artifacts (world-readable on a public repository); committing the file; split-secret transport.

## O. Decisions Jim must make

Settled by Jim: a separate table in the Hostinger database; everything in the cloud and automated; no local sheet, no commands, no hand-kept lists.

1. **The canon page.** Re-read, it does not forbid this (section 0a); it only needs a short clause defining the derived store's scope, which the Platform Canon requires. Suggested wording is in section 0a. Say "add the clause" and I will; nothing else on that page changes.
2. **One-time setup** (section N): enable the Drive API, make the intake folder and share it with the service account, add the folder-identifier secret. Needed before NET-1a, not before.
3. **Triggers.** The canon's four; the first slice covers a qualified job (`ADMIT`) and an applied job. `REVIEW` jobs are not included by default; say so if they should be. Where the block and the glue live: the glue in `lifeos/sources/` (recommended), and leads as an appended, machine-owned block with Dismiss and Same-company ticks on the job page.
4. **Fields and email.** Email addresses are not stored (recommended). The Notion block shows display name, listed company and title, export date, a profile link and a fixed reason.
5. **Erasure and retention for people** (recommended: a `REMOVED` status that excludes a person from matching, set when Jim asks; removal from the export alone changes nothing).
6. **Approve the tables** in section E, slice by slice.

## P. First build slice

```text
FIRST BUILD SLICE:
NET-1a Drive inspector (dry run, cloud, counts only)

WHY FIRST:
It needs no table, proves V7 can reach the intake folder through the existing service-account pattern, and shows the real export's columns, blank fields, own-name companies and ambiguous identities by count before anything is stored. Every later slice depends on those answers.

EXPECTED FILES:
lifeos/platform/gdrive.py, lifeos/network/__init__.py, identity.py, parse.py, one line in lifeos/run.py, a network job in .github/workflows/domains.yml plus its DOMAIN_SECRETS entry in tests/contracts/test_workflow.py, tests/network/* and a reader test in tests/platform, a docs/SETUP.md section

SCHEMA:
NONE

ACCEPTANCE:
Reader and parser tests pass on synthetic exports (tracking-parameter and case variants of profile addresses, same-name collisions, blank fields, own-name companies, commas and emoji in names, malformed rows, an incomplete read failing closed); a dry run prints counts only; nothing is written anywhere; the full suite passes.

DO NOT BUILD YET:
Tables, the import, the diff and events, aliases, the Notion block, the digest, any live or targeted lookup, relationship evidence.
```
