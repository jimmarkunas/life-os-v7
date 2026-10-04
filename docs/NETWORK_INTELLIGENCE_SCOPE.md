# Network Intelligence: scope and roadmap (scoping only, no code)

Status: scoped for Jim's review. Nothing here is built, and this document changes no schema, secret, workflow or `docs/DECISIONS.md`.
Settled by Jim: **a periodic re-export of the connections file is the accepted refresh mechanism**, replacing the brief's per-person live lookups. Still open: the private path for the seed file (section 12 recommends one) and the decisions in section 17.

## 0. The shape of it in one paragraph

Network Intelligence is a small temporal people graph in Hostinger that makes Jobs OS smarter: when a job is admitted, it lists the few people in Jim's network who currently or formerly list that employer, with the evidence and its age. The graph is seeded from a LinkedIn connections export and kept alive by importing a fresh export every so often and diffing it against what is already stored. That diff is free, bulk, deterministic and permitted, and it produces exactly the change events the brief wants (company changed, title changed). It does not need any live lookup, any scraping, any paid service, or any chat connector, none of which V7's runtime can use. Every claim carries its source and the date it was observed; nothing is inferred about relationship strength.

Why the refresh changed: the brief's targeted lookups run through a connected chat app. V7 must run with chat unavailable (D54) and GitHub Actions cannot reach a chat connector, so V7 has no people-evidence source at runtime at all. The browser paths are closed by D12 and D14, and TinyFish Fetch is reserved for job pages (D11, D12 amendment). A re-export is the one permitted bulk source that needs no new decision.

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
Connections export (Jim, private)
        |  local run, counts only
        v
parse + validate (no writes in dry run)
        v
identity: profile-URL key (never name alone)  +  company key (core tokens)
        v
reconcile against stored positions  ->  same | title changed | company changed | new person | ambiguous
        v
Hostinger: v7_network_people, v7_network_positions, v7_network_events     (one transaction per person)
        |
        +----> change events (counts now, digest later)
        |
        +----> match(job)  <---- admitted, published jobs (read through a sources adapter)
                    v
             NetworkLead list (at most 5, each with reasons and freshness)
                    v
        marker-owned "Network Leads" block at the end of the Ledger page body
```

Placement: `lifeos/network/` (identity, store, import, reconcile, match, render) imports `platform` only. The stage that reads admitted jobs and calls `network.match` lives in `lifeos/sources/` (it may import `jobs` and `network`), so neither OS imports the other and the D17 boundary stays as it is.

## D. Source feasibility

| Source | Viable | Gives | Bulk or targeted | Auth and cost | MVP |
|---|---|---|---|---|---|
| LinkedIn connections export (member data download) | Yes, member-initiated, permitted | Name, profile URL, current listed company and position, connection date, sometimes email | Bulk | None; free; Jim downloads it | **MVP, the seed** |
| Fresh re-export, diffed | Yes | Change in the listed company or title since the last export | Bulk | None; free | **MVP, the refresh** |
| LinkedIn through the connected chat app | Not at V7 runtime | Targeted current lookup | Targeted | Chat connector | Reject for V7. A human could relay an observation later through a private channel (decision 3). |
| V7's LinkedIn job resolver | No | Jobs only | n/a | n/a | Reject: job-only, must not become a people crawler |
| Employer team or bio pages, personal sites, speaker pages | Possible per site | Current role evidence | Targeted | Free | Later, one adapter per site with its own authority rules |
| Browser automation or logged-in scraping | No | | | | Reject (D12, D14) |
| TinyFish Fetch or Search | No | | Shared job quota | Free quota, reserved | Reject: job pages only (D11, D12 amendment) |
| Email signatures and domains from Jim's mail | Possible | Employer change evidence | Targeted | Existing mail access | Later (NET-6, relationship evidence) |
| Address-book contacts | Possible | Company and title fields | Bulk | New OAuth scope | Reject for MVP: a new grant |
| Paid enrichment vendors | Yes | | | Recurring cost | Reject; optional and out of MVP |

What the export can and cannot say: it carries each connection's single currently listed position at the moment of export. It gives no tenure dates, no history and no second concurrent role, and some fields are blank. So history accumulates from launch forward, one export at a time, and each stored fact says "listed as of the export of date X", never "employed from, to". A blank field never demotes a stored position. Whether the real file has these columns and how many are blank is proven by the dry-run inspector in NET-1a, by count, before any write.

## E. Hostinger schema (minimum, added by slice)

Added only after Jim approves each slice's tables. Table creation happens only on a live run.

`v7_network_people` (NET-1b)
- `id` (key), `person_key` unique (hash of the identity key), `url_key` unique and nullable (normalized profile address: scheme, host, case, query string and trailing slash removed), `display_name`, `connected_on` date, `first_seen`, `last_observed`, `status` (`ACTIVE`, `REMOVED`), `history_coverage` (`SEED_ONLY`, `PARTIAL`, `UNKNOWN`), timestamps.
- No email column unless Jim decides to keep one (decision 6). The brief's `refresh_state` and `refresh_due_at` are dropped: with export diffing there is no per-person refresh schedule to track until a live source exists.
- Identity rule: the URL key is the identity. With no URL, fall back to normalized name plus company key, and treat any collision as ambiguous and unmerged. Two people with one name never merge; a changed employer never creates a new person.

`v7_network_positions` (NET-1b)
- `id`, `person_id`, `company_key` (joined `core()` tokens), `company_name`, `title` nullable, `position_state` (`CURRENT`, `SUPERSEDED`), `first_observed`, `last_observed`, `source_kind` (`LINKEDIN_EXPORT`, later `USER_CONFIRMED`), `source_ref` (an export batch label, never a URL), `source_observed_at`, `confidence`, `material_hash` (unique per person, company, title and source), timestamps.
- `SUPERSEDED` means "no longer the listed position as of a later observation". It does not assert that employment ended, so no end date is invented. Start and end date columns from the brief are left out until a source supplies tenure.
- Index on (`company_key`, `position_state`) for matching.

`v7_network_events` (NET-2)
- `id`, `person_id`, `event_type` (`PERSON_IMPORTED`, `CURRENT_POSITION_CONFIRMED`, `COMPANY_CHANGED`, `TITLE_CHANGED`, `PROFILE_UNRESOLVED`, `CONFLICT_DETECTED`), `old_position_id`, `new_position_id`, `observed_at`, `source_kind`, `event_hash` unique, `created_at`. Append-only and idempotent.

Not added: a raw-observation table (positions already carry source and observed time), a recommendations table (matching is computed on demand), a refresh queue.

One platform addition: a small transaction context in `platform/db.py`. The connection is autocommit, and a position change must commit together with its event, otherwise a crash between them would lose the event for good on replay.

## F. Contracts (architecture level)

```python
@dataclass(frozen=True)
class PersonObservation:
    url_key: str | None; display_name: str; connected_on: date | None
    company_name: str | None; title: str | None
    source_kind: SourceKind; source_ref: str; observed_on: date

@dataclass(frozen=True)
class ImportReport:    # counts only
    rows_read, people_created, people_matched, ambiguous, positions_created,
    positions_unchanged, positions_superseded, malformed, events_planned

def parse_export(path, observed_on) -> Iterator[PersonObservation | Malformed]
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

Source adapters return observations and never write; `apply` is the only writer.

## G. Refresh algorithm (re-export diff)

1. Jim supplies a new export and its date. The date is explicit because the file carries none. Cadence is his choice; about every four to six weeks and before an active search is a sensible default.
2. Parse the whole file first. Any structural error fails the run before a write (a full result or an error, never partial).
3. For each row, derive the identity key and company key, then compare with the stored current position:
   - same company key and same title: confirm (`last_observed` and `source_observed_at` advance); no event.
   - same company key, different title: previous position `SUPERSEDED`, new position `CURRENT`, one `TITLE_CHANGED` event. No promotion is inferred.
   - different company key: previous `SUPERSEDED`, new `CURRENT`, one `COMPANY_CHANGED` event.
   - blank company or title where a value was stored: no change and no event; the stored value simply ages.
   - person not seen before: create the person and the position, one `PERSON_IMPORTED` event.
   - two rows that resolve to one identity with conflicting positions, or a collision with no URL: counted as ambiguous, no write for that person.
4. People absent from a newer file are left untouched. Absence from one export proves nothing, so nothing is demoted or removed for it.
5. Replay of the same file is a no-op: unique hashes on positions and events, so a second run creates zero people, zero positions and zero events.
6. Freshness is computed from `source_observed_at` and always shown with the date. Proposed defaults, held as named constants: `FRESH` up to 45 days, `AGING` to 120 days, `STALE` beyond that, and `CONFLICTED` while an ambiguity flag is set. The first batch is labelled seed until a later export confirms it.
7. There is no retry or rate logic, because the only I/O is the file and the database. Database failures use the existing fixed `StoreError` codes and roll the person back.

## H. Job-to-network recommendation algorithm

Trigger: a published job with a page id and a verified time whose stored `admission` is `ADMIT` (decision 1 asks whether `REVIEW` jobs, which also publish, count). Matching is stateless and reads current Hostinger state.

- Company match is by equal company key only. There is no fuzzy or prefix match in the MVP, so a missed alias ("Meta" against "Meta Platforms") shows up as a miss rather than a wrong person. A small deterministic alias table can come later.
- Tier 1: a `CURRENT` position at the company. Tier 2: a `SUPERSEDED` position at the company ("previously listed there"). Within a tier, order by function overlap between the stored title and the job's role (shared role tokens), then by freshness, then by name for a stable order. No numeric score is stored; every ordering factor is visible.
- A person with no company match is never shown, so weak signals cannot fill slots. At most five leads.
- Reasons are fixed templates built from stored facts, for example "Listed at Acme as Director of Product, as of the September export (45 days)" or "Previously listed at Acme; later listed elsewhere". Nothing about closeness, referral likelihood or willingness is ever generated.
- A stale lead is shown with its age and a verify-first note, never as current.
- Objectives are fixed templates by basis (for example, learn how the team is organised for a current employee). Drafting message text is not part of V7; that stays a human or ChatGPT-side step.

Examples (synthetic):

1. Admitted job at Acme. Network holds Jane (current at Acme, fresh), Raj (superseded at Acme, aging) and Tom (current at WidgetCo). Result: Jane then Raj; Tom is omitted.
2. Admitted job at WidgetCo. The only match is Sam, current at WidgetCo but 200 days old. Result: Sam, labelled STALE with its date and a verify-first note.
3. Admitted job at a company nobody in the network lists. Result: nothing is written; no empty block is created.

## I. Roadmap (each slice has a product boundary of its own)

| Slice | Outcome | Likely files | Schema | Tests and UAT | Depends on | Risks | Size | PRs |
|---|---|---|---|---|---|---|---|---|
| **NET-1a Export inspector** | Jim runs a dry run on his own export locally and sees counts only: rows, blank URL, blank company, duplicate URL keys, distinct company keys, ambiguous identities. Proves the file's real shape before anything is stored. | `lifeos/network/{identity,parse}.py`, `run.py` line, `.gitignore` entry for export files, `docs/SETUP.md` | none | table-driven parser and identity tests (tracking parameters, case, trailing slash, same-name collision, blank fields), counts-only output test | nothing | The real export's columns may differ from the documented ones; that is exactly what this finds | S to M | 1 |
| **NET-1b Private import** | The seed lands in Hostinger without duplicates; replay creates nothing. | `lifeos/network/{store,import_export}.py`, `platform/db.py` transaction helper, tests | `v7_network_people`, `v7_network_positions` | replay twice equals once; ambiguous people unmerged; dry run creates no tables; read-back equals written | NET-1a, Jim's table approval, seed path (section 12) | PII handling: logs counts only | M | 1 |
| **NET-2 Re-export diff** | A second export yields exactly the right change events and freshness. | `lifeos/network/{reconcile,freshness}.py`, `network-changes` counts report | `v7_network_events` | title change, company change, blank field, new person, absent person, conflicting rows; each yields the specified events exactly once | NET-1b | Export granularity: changes are only as fresh as the export cadence | M | 1 |
| **NET-3 Job match (no surface yet)** | A dry-run stage reports, by count, how many admitted jobs have leads and how many leads each. | `lifeos/network/match.py`, `lifeos/sources/network_leads.py`, `run.py` line | none | the three examples above, tier order, five-lead cap, no weak fill | NET-2, decision 1 | A missing alias gives a miss, not a wrong match | M | 1 |
| **NET-4 Surface** | Leads appear on the job page in one machine-owned block; unchanged leads cause no write. | marker-block writer generalized from `report_region`, one step in the `finish` job after publish (continue-on-error, not in the failure list) | none | owned block replaced, nothing else touched, read-back, hash skip, page-gone and Ledger-target guards | NET-3, decision 5 | A human editing inside the owned block loses the edit; documented | M | 1 |
| **NET-5 History and change digest** | "Who moved recently" and "who has listed company X" answered from stored events, as counts first and a private page or region later. | `lifeos/network/queries.py`, a stage | none | query tests; region ownership decided before any report region | NET-2 | Region ownership is a product decision (roadmap decision 3) | M to L | 1 |
| **NET-6 Relationship evidence (later)** | Accepted mail and calendar evidence of real contact. | later | later | later | core value proven | Easy to overreach into relationship scoring, which is out of scope | L | later |

NET-0 from the brief is absorbed: with no live source to prove, its work is the NET-1a inspector plus the decisions in section 17.

## J. Surfacing mechanics (NET-4 in detail)

- One top-level toggle block at the end of the Ledger page body, titled "Network Leads (n)". Its first child paragraph begins with a marker (`v7-net:1`) followed by a short hash of the lead set. The marker, not position, is how ownership is decided.
- An unchanged hash means no write. A changed hash deletes the owned block and appends the new one, then reads the page back. Nothing outside the owned block is read for change or written.
- The existing description marker at the top of the body (`v7-jd:1`) and the properties the publisher and fit sync own are never touched, so `readback.py`'s first-three-blocks check keeps passing.
- Before any write: `ledger.verify` on the data source, page not archived or in trash, description marker still present. A failed guard writes nothing.
- The step runs in the hourly `finish` job after publish because the Jobs Notion secrets exist only there. It is continue-on-error and absent from the lane-failure list, so a network failure cannot fail the Jobs run.

## K. Non-goals and rejected architecture

No second scheduler, workflow family or workflow input. No graph database. No profile scraping, crawler, logged-in automation or browser. No TinyFish. No dependency on a chat connector at runtime. No automatic outreach, no connection requests, no drafted message text from V7. No relationship strength, referral likelihood or closeness scoring. No generic CRM framework. No paid enrichment vendor. No email addresses stored by default. No Jobs-to-Network import, and no new edit to the Jobs tables.

## L. Decision-log proposals (draft; `docs/DECISIONS.md` is not edited)

- Network Intelligence is its own OS package; the Jobs-to-Network glue lives in `lifeos/sources/`.
- The refresh mechanism is a periodic re-export diff; no live people source is used.
- A position is "listed as of an export"; a superseded position does not assert that employment ended, and no tenure dates are invented.
- Person identity is the normalized profile address; a name alone never merges two people.
- Matching is stateless, by equal company key, and shows at most five leads with visible reasons and freshness.
- Leads are written as one marker-owned block on the Ledger page body, with no new Notion property.
- Export files never enter the repository, workflow inputs or artifacts.

## M. The brief's twenty questions, answered

| # | Answer |
|---|---|
| 1 | New package `lifeos/network/`; cross-OS glue in `lifeos/sources/`. |
| 2 | `platform/db.connect`, `StoreError`, `names.core`, `notion_client`, `redact`, `alerts`, `limits`; plus one new transaction helper. |
| 3 | A dedicated `lifeos/network/store.py` with its own `ensure_schema`, created only on live runs. |
| 4 | Reuse `names.core`; the key is its joined tokens, with no platform change. Do not use `same_company` as a key. |
| 5 | Published job with a page id and verified time, `admission = ADMIT` (REVIEW per decision 1). The brief's status chain with ENRICHED and FIT does not exist. |
| 6 | One marker-owned toggle block at the end of the Ledger page body, from a `finish`-job step after publish. |
| 7 | Matching is database-only and at most about ten Notion calls; no network refresh runs on the tick at all. |
| 8 | `python -m lifeos.run network-*` stages, dry run by default, run locally or from `domains.yml`; no new `hourly.yml` input. |
| 9 | None permitted at runtime. The re-export diff replaces it. |
| 10 | Only forward accumulation, one export at a time. Retroactive history is not promised. |
| 11 | MVP adapter: the export importer. Later: user-confirmed corrections and per-site public pages. |
| 12 | Local import on Jim's machine (section 12). |
| 13 | Profile-address identity; name-only collisions stay unmerged and are counted. |
| 14 | The schema allows several current positions per person; the export can only show one, so concurrent roles wait for a richer source. |
| 15 | Fresh to 45 days, aging to 120, stale beyond, always with the date. Defaults, tunable. |
| 16 | People and positions in NET-1b; events in NET-2. |
| 17 | Yes, stateless on demand; the page's marker hash is the only stored idempotency. |
| 18 | All of it deterministic in V7. Message drafting stays human or ChatGPT-side and outside V7. |
| 19 | Person rows and addresses stay in Hostinger. Only the lead's display name, listed company and title, the observation date and a fixed reason appear in Jim's private Notion block (decision 6 covers whether to show more or less). |
| 20 | First as counts from a stage, then as a private page or a region once ownership is decided. |

## N. Seed path

**Recommended: a local import on Jim's machine.** The file never leaves it.

- Jim requests the connections data export from LinkedIn (member-initiated, free) and keeps it outside the repository. A `.gitignore` entry for export-shaped files is added in NET-1a as a safety net.
- He runs the import locally: the stage takes a file path and the export's date, dry run by default, then `--live`. It reaches Hostinger through the same SSH-tunnel module the pipeline uses, with the eight existing database settings supplied from his password manager as environment variables on his machine, never written to a file in the repository.
- Output is counts only. The file is read, parsed and discarded.

Fallback if running locally is not workable: split the compressed file across several repository secrets (each is capped at 48 KB) and run the import from a `domains.yml` job. This puts personal data in GitHub's secret store and the runner, needs a `DOMAIN_SECRETS` change and a manual cleanup afterward, so it is the second choice.

Rejected: workflow inputs and artifacts (world-readable on a public repository), committing the file, and a manual SQL upload into a staging table.

## O. Decisions Jim must make

1. Trigger on `ADMIT` only, or `ADMIT` and `REVIEW` (both publish).
2. Approve the seed path in section N (local import), or choose the split-secret fallback.
3. Whether an observation relayed by hand from a chat-side lookup may ever be ingested (needs a private channel and a `USER_CONFIRMED` source). Recommended: not in the MVP.
4. Confirm the glue lives in `lifeos/sources/` (recommended) rather than amending the D17 layering rule.
5. Confirm Network Leads as an appended, machine-owned block on the job page, rather than a property or a separate page.
6. Whether to store emails at all (recommended: no) and which fields may appear in the Notion block (recommended: display name, listed company and title, observation date, reason).
7. Erasure and retention for people (recommended: a `REMOVED` status that excludes a person from matching, plus a hard-delete command).
8. Approve the tables in section E, slice by slice.

## P. First build slice

```text
FIRST BUILD SLICE:
NET-1a export inspector (dry-run parser and identity check, local, counts only)

WHY FIRST:
It needs no schema and no secret, runs on Jim's own export, and proves the file's real columns, how many fields are blank and how many identities are ambiguous before anything is stored. Every later slice depends on those answers, and it settles the seed path in practice.

EXPECTED FILES:
lifeos/network/__init__.py, lifeos/network/identity.py, lifeos/network/parse.py, one line in lifeos/run.py, tests/network/*, a .gitignore entry, a docs/SETUP.md section

SCHEMA:
NONE

ACCEPTANCE:
Table-driven tests pass for profile-address normalization, same-name collisions, blank fields and malformed rows; a dry run over a synthetic file prints counts only; nothing is written anywhere; the full suite passes.

DO NOT BUILD YET:
Tables, the live import, the diff and events, matching, the Notion block, the change digest, any live or targeted lookup, relationship evidence.
```
