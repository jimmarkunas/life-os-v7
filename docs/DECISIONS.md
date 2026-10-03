# LIFE OS v7 - Decisions (canon)

Numbered, dated, and owned by Jim. If this file and any other document disagree, **this file wins**.
Status: HARD = non-negotiable constraint. DECIDED = approved. PROPOSED = recommended default, awaiting Jim.

## D1 - No unresolved jobs (HARD, 2026-09-30)
Every job that reaches Notion has BOTH a resolved final apply link AND its full job description. A job is never
published, or left in limbo, because the ATS/employer link "could not be resolved". There is no retargeting /
retry-later layer. If resolution fails for an open job, that is a defect in the resolver (fix the resolver), not a
state to ship. A posting that is genuinely closed or expired is excluded as `CLOSED` - never published half-resolved.

## D2 - No tiered publishing (HARD, 2026-09-30)
Nothing is published "now, upgraded later". A job is published once, fully resolved.

## D3 - Final apply link ranking (DECIDED)
1. Employer website (preferred). 2. The employer's official third-party board (Greenhouse, Ashby, Lever, Workday, ...).
3. Easy Apply, only when it is the sole path (LinkedIn or Jobright). 4. Aggregator-only, flagged `aggregator`
(allowed only when the aggregator truly is the only apply path). All sources are recorded; one final link is published.

## D4 - Posting date / job age ranking (DECIDED, 2026-09-30)
Sources in order of trust; we record which source supplied the date (`posted_source`):
1. Employer/ATS structured data (JobPosting `datePosted`; ATS API published/created timestamp).
2. The aggregator's own job page (LinkedIn posted time, Jobright page data).
3. Newsletter text ("Posted N days ago") + the email's age.
4. Email age alone (job is at least as old as the email that mentioned it).
The earliest credible date is the original posting date; all signals are kept, none overwritten.

## D5 - Freshness window (DECIDED)
Jobs known to be older than 14 days are excluded before Notion (`EXCLUDED_STALE`); unknown age is allowed.

## D6 - Reposted jobs (PROPOSED)
A repost is NOT automatically excluded or labelled a ghost job. Reposts reset dates on many sites, so:
keep it, flag `reposted`, and age it by the ORIGINAL date when known (so D5 still excludes a repost of an old job).
"Ghost job" likelihood (long-open, repeatedly reposted, no hiring activity) is a separate Phase 2 scoring signal, not a filter.

## D7 - Privacy (HARD)
Public repository: no PII, secrets, mailbox content, or job data in code, logs, or PRs. See `docs/PRIVACY.md`.

## D8 - Notion (DECIDED)
Free-tier API only (<= 3 req/s). Hostinger is the dedupe brain; no Notion reads to dedupe. Job description goes in the
page body only, in the machine-readable `v7.jd.1` format (see PLAN.md); Hostinger keeps the authoritative copy.

## D9 - Provider match scores (DECIDED)
Aggregator match % is stored as evidence only and counts zero today. Phase 2 decides: 0% or 25% weight.

## D10 - Browser tooling (DECIDED, 2026-09-30; scope narrowed by D12)
Automated HTTP fetching is blocked or login-gated for Lensa, LinkedIn (employer link) and Jobright (employer link).
Resolution uses a real browser path: TinyFish (Fetch first because it is free; Agent only where a login or clicks are
unavoidable, because it is metered per step) and the existing Jobright login. Browser path is proven on a sample
BEFORE anything is published.

## D11 - TinyFish spending guard (HARD, 2026-09-30)
Jim's TinyFish wallet is **$35 total and must never be used up**. Only the free **Fetch API** is used (free limits:
150 URLs/min, 1,000 URLs/day). In code: daily cap 900 URLs (counted in Hostinger `v7_spend`, pessimistically, before
sending), pacing of at most 100 URLs/min, and **no code path to the metered Agent ($0.016/step) or Cloud Browser
($0.002/min)** - a test fails the build if one appears. Any paid use requires Jim to set an explicit dollar cap first;
the default is $0. Search API (free, 30/min, 500/hr) is allowed under the same counting rules.

## D12 - Browser is ONLY for the link chain (HARD, 2026-09-30)
The TinyFish browser is used **only** to follow the link chain from an aggregator job page (Lensa / LinkedIn /
Jobright) to the employer or official-ATS apply link. It is **not** used for descriptions, posting dates, parsing, or
anything else. Descriptions and dates come from the final employer/ATS page (plain HTTP or the ATS's public API) or
the aggregator's own free data. Code enforces this: the browser client is callable only from the link-chain resolver.

## D13 - Paid browser budget (RETIRED - code removed; the free Chromium in the runner and free Search/Fetch cover it)
Measured: the free Fetch API loads aggregator pages but does NOT expose the apply button's destination (it sits behind a
click). The metered **Browser** tool ($0.002/min, scripted click) is ~50x cheaper than the **Agent** ($0.016/step, AI
clicks). Proposal: use Browser only (never Agent), hard lifetime cap **$5** of the $35 wallet (~2,500 browser minutes),
minutes counted in Hostinger before each session, hard stop at the cap, no overage. Until Jim approves a dollar cap, paid spend stays $0 (D11).

## D14 - Logins (HARD, 2026-09-30)
Jobright: log in with the saved GitHub secrets (`JOBRIGHT_EMAIL`, `JOBRIGHT_PASSWORD`) inside the scripted browser session
(ported from V2's proven flow), once per session, for the link chain only. LinkedIn: **never log in.** LinkedIn apply
type and employer link come from V2's no-login method (public guest page: apply-control markers, JSON-style apply keys,
and `/safety/go` / `/redir/redirect` wrapped links), plain HTTP first; browser (no login) only if that fails.
Credentials are never logged or committed.

## D6 advice - reposts (still PROPOSED until Jim answers)
Do not auto-exclude reposts as "ghost jobs". A repost is usually the same real opening refreshed by the employer or ATS.
Policy: one job = one row (dedupe by final URL, then fuzzy key); keep the ORIGINAL first_seen; count sightings
(`seen_count`, `last_posted_date`). Posting date for freshness is the employer's datePosted when present, else first_seen.
Ghost risk is evidence, not a rule: flag "possible ghost" (never exclude) when the same vacancy was reposted 3+ times in
60 days or carries no real posting date for weeks; Phase 2 scoring may down-weight it.

## D15 - Provider limits are canon (DECIDED)
Every provider limit is a constant in `lifeos/platform/limits.py`, documented in `docs/LIMITS.md`, and enforced by tests. Stages
stop on the first 429/403.

## D16 - Private evidence stays out of the public repo (DECIDED)
Fit-model evidence and personal scoring corrections are never committed. `docs/FIT_MODEL.md` holds structure only.

## D17 - Ownership boundaries (DECIDED)
`lifeos/platform` is shared infrastructure (database connection, Gmail, HTTP, Notion transport, provider limits, usage
guards) and knows nothing about jobs. `lifeos/jobs` is Jobs OS: one job model, identity, intake, resolve, enrich, quality,
audit, publish, repost and retention, whatever produced the job. `lifeos/sources/<name>` are producers: they emit a
normalized job through `jobs.intake.add_job` and stop. Imports point downward only (sources -> jobs -> platform);
`tests/test_boundaries.py` enforces it. Lane and provider live on the job row, so a new producer needs no change in Jobs OS.
Package renamed from `pipeline` to `lifeos`; the `v7_` table prefix stays.

## D18 - Shared test layer (DECIDED)
Tests mirror the code: `tests/platform`, `tests/jobs`, `tests/sources/<name>`. Shared fakes for the platform boundaries
(database, mailbox) live in `tests/kit` and know nothing about any domain. Cross-cutting guards that every OS inherits
live in `tests/contracts` (privacy, import boundaries, provider limits, workflow validity); the boundary test discovers
OS packages from `lifeos/`, so a new OS needs no change there. Domain tests stay with their domain. Run:
`python -m unittest discover -s tests -t .`

## D19 - Fit is deterministic; no LLM in the chain (DECIDED)
The Claude project's fit method is ported as code (`lifeos/jobs/fit`, docs/FIT_MODEL.md). No Anthropic or other model call
is made to extract or classify requirements. The profile is a private secret (`FIT_PROFILE_JSON`), validated and hashed on
load; scores record the profile hash so a profile change re-scores. Shadow mode first (`V7_FIT_GATE` unset), gate after calibration. Professional Fit follows the live Candidate Profile (V3 five dimensions, Go at 72); exclusions are a separate gate and never change the Fit number.
Dice is bundled into the US Remote producer, not built as a newsletter source.

## D20 - One lane policy, data not code (DECIDED, 2026-10-01)
`lifeos/jobs/lanes.py` holds `LanePolicy` records and one `qualify()` (see docs/LANES.md). US Remote freshness stays 14 days;
Scale-Up has a 30-day age gate; Skilled Worker is a disabled record. Retention follows the canon (30-day stale purge, 90-day
tombstone) plus: Applied without reaching interview in 30 days is purged, an interview-stage job is kept. Scale-Up is built and tested
(promoted to the schedule by Jim on 2026-10-01, D32).

## D21 - Retention follows the canon; Applied is never retired by Jobs yet (DECIDED, 2026-10-01)
30-day retirement of unapplied, undecided jobs on the employer Posting Date (else First Surfaced), 90-day tombstone, no Lifecycle dependence. Applied jobs
resolve UNKNOWN in `progression.resolve` until INT-7.1A, so they are kept (fail closed). Jim's rule "Applied without an interview is purged after 30 days"
becomes active when INT-7.1A can prove the absence of progression.

## D22 — Newsletter closure, Ledger read-back, market detection, shape probes
- A PUBLISHED job is finished only after a read-back of the Notion page (key, Apply URL, title, Fit present, v7-jd body marker); `v7_jobs.verified_at` records it.
- Newsletter mail gets the `J Newsletters/Done` label only when no job from it is NEW/RESOLVED/READY and every PUBLISHED one is verified (`finalize`). `Processed` keeps its extract meaning.
- Market comes from the location text alone (US / UK / OTHER, silent or mixed = unknown, never guessed); a known market that is not the lane's market is EXCLUDE. Policy version l2 re-scores.
- `probe` (dispatch only, counts-only) reports the shape of Open Jobs, Teamtailor and Dice pages from Actions before any parser is written against them.

## D23 — Open Jobs as a tail consumer; Teamtailor via RSS; Dice
- Open Jobs (`lifeos/sources/openjobs.py`) follows the published change generations forward. It never bootstraps the multi-million-row corpus; it applies each generation atomically (admitted rows and the `v7_feed` checkpoint commit together), verifies every manifest hash, page size/sha256 and row count, and a gap or corrupt page is DEGRADED (nothing applied, checkpoint kept, never skipped). First run applies the newest delta only. Events are filtered by the cheap US Remote suppression before any Fit work; the company comes from the board slug (the feed carries none); removals close only jobs V7 holds that are still unpublished.
- Teamtailor boards (6 Scale-Up sponsors) are read from the `<jobs url>.rss` feed (probe run 83: all six answered 200, item counts equal the page's job links, no JSON-LD on the HTML list). An empty channel is a COMPLETE empty board; anything that is not a valid channel is FAILED.
- Dice: the public search page is script-rendered (probe run 83: 200, no job-detail links). No Dice discovery; a Dice job page is read only when a job already points at it.

## D24 — Skilled Worker enabled (Phase 2); INT-7.1A bounded handoff
- Skilled Worker is active (Jim, 2026-10-01). Route evidence = the actual employer on the sponsor register; recruiters and unloaded registers are Review, never NEGATIVE; 72 Fit floor, 14 days, GBP 65,000 explicit pay floor, London/UK-remote geography, unknown market = Review. Lane routing is `decide_all`; policy version l4.
- INT-7.1A is the Hiring Pipeline page read by `hiring_pipeline.py`: it protects pursuits with an ACTIVE opportunity (company and role must both match), never unprotects, and fails closed when configured but unreadable. Calendar/Gmail correlation is deliberately not built: it cannot be made deterministic enough to prove the ABSENCE of progression, which is what retiring an Applied job needs.

## D12 amendment (Jim, 2026-10-01; wording tightened after TL review): rendered-fetch fallback
Interactive browser/link navigation stays restricted to final-link resolution (D12). Free TinyFish Fetch may additionally render an **already-resolved final employer/ATS URL (or its public data endpoint)**
ONCE when the ATS API and plain HTTP cannot retrieve usable page content. It may extract JD, date and liveness evidence only. It may not click, authenticate, search, traverse
links, select a different vacancy, or change `final_apply_url`. It is counted against the daily Fetch cap BEFORE it is sent and is never repeated for the same job
(`description_empty_tf`). The metered Agent is never used outside the link chain. `resolve` decides WHERE a job is; `enrich` reads WHAT is at that decided location.

## D25 — One scheduler: GitHub Actions; the platform is AI-agnostic (Jim, 2026-10-01)
GitHub Actions cron (`hourly.yml`, driving `lifeos.run`) is the sole recurring scheduler for V7. No ChatGPT/LLM-hosted schedule, no second scheduler. Any wider LIFE OS
"Daily Runs" scheduling that conflicts is superseded for V7 (the canon page needs updating on the Notion side). The workflow stays a thin dispatcher over `lifeos.run`.

## D26 — Freshness has one authority; destructive Ledger operations ask the human-state guard
- `lifeos.jobs.lanes.POLICIES[...].max_age_days` is the only freshness number. Enrichment consults the job's lane policy (`enrich.finish(..., lane)`), so a Scale-Up
  job is no longer cut at 14 days. Newsletter ingest keeps one coarse mail-age pre-filter (to avoid spending link-resolution budget on stale mail) whose number is read from the
  US Remote policy. A test fails the build if any other module defines its own freshness constant.
- `lifeos.jobs.guard.protection()` runs before audit trashes a published page: Applied, Applied On, Saturn Decision, an active hiring-pipeline opportunity, or an unreadable page
  all protect it (fail closed); the row is left PUBLISHED and only counted. Any future destructive Ledger operation must call the guard first.

## D27 — What the first live-shaped dry runs taught (run 85, 2026-10-01)
- Open Jobs: one generation held 562,627 events (287,980 upserts, 274,647 removes); with title family, US and age filters alone 9,370 postings still passed, so a bulk feed now requires POSITIVE remote evidence (location, title or the description's explicit statement); a posting that never says remote is not a candidate here. The 3,000-per-generation valve stays and is always counted (`over_cap`).
- Sponsor register: 120,417 Skilled Worker, A-rated entries read from the gov.uk CSV (dry run).
- Hiring Pipeline page: readable; 5 active opportunities, 2 with interview rounds; one title does not follow "Company - Role" and cannot match (it protects nothing until renamed).
- Dice: a job detail page answers plain HTTP with JobPosting JSON-LD (description 5,588 chars) and an "Easy Apply" marker; enrichment's existing JSON-LD tier reads it, and an Easy Apply Dice page is now flagged `apply_kind=easy_apply` (Ledger Source Types "Easy Apply"). Dice search stays script-rendered: no discovery.

## D28 — Lifecycle removed, Job Ledger target verified, guardrails as contract tests
- Retention no longer reads the legacy Lifecycle field at all (the Applied checkbox and Saturn Decision are the human-state filters; the hiring-pipeline handoff adds protection).
- `lifeos.jobs.ledger.verify()` checks that the configured data source has the Job Ledger's properties with the right types (`REQUIRED`) before ANY Notion write: publish (live), audit trash, retention trash. Mismatch or unreadable = no write, reported as `target`. The `probe` stage reports it (and any missing/wrong-type property names) so it can be proven before the pipeline is turned on.
- `tests/contracts/test_guardrails.py` ports the V1-V5 invariants as assertions: every trash call is behind a protection check; retention selects only unapplied, undecided rows; unknown is protected; no Lifecycle/Liveness authority in code; every Notion writer verifies the target first; a failed board is never zero or a removal and every due source is accounted for; a disabled lane can never admit; exactly one recurring scheduler; the source universes are frozen.

## D29 — Finalization is an allowlist; ambiguous repost identity is not auto-linked
- A newsletter mail closes (`Done`) only when every job from it is in an explicit terminal state: verified PUBLISHED, CLOSED, DUPLICATE, PURGED or EXCLUDED_*. HOLD, NEW/RESOLVED/READY and any unknown status keep it open (counted as hold / in_flight / other).
- `repost.link` links a new row to an original only when exactly ONE candidate matches the fuzzy key; with more than one it links nothing and marks `unresolved_reason='repost_ambiguous'`.
- Deferred: merging a duplicate observation's stronger evidence (URL, provider, lane) into the original, until live runs show it is needed.

## D30 — Scheduled triggers are unreliable, so there are three per hour and a gate (2026-10-01)
GitHub fired 2 of ~11 scheduled runs in one stretch. Still one scheduler (D25): `hourly.yml` now has crons at :07, :27 and :47. `lifeos.platform.gate` (first step of `prep`) cancels a scheduled
run when another run started in the last 50 minutes, so the effective cadence stays about hourly and the TinyFish allowance (500/hour) is not doubled; a dropped slot is covered by the next.
The same step opens ONE GitHub issue (label `pipeline-stale`) when no run has succeeded for 150 minutes and closes it on recovery; alert failures never stop the pipeline. Dispatch runs are never gated.

## D31 — An outside timer may TICK the same workflow; GitHub Actions still does all the work (Jim, 2026-10-01)
After D30, GitHub fired no scheduled run for 5+ hours (it fired 2 of ~11 earlier). D25 is amended: the only thing allowed outside GitHub is a dumb timer that calls the workflow's
`workflow_dispatch` with `tick=true` (a free cron service, any provider; it holds a Actions-only token for this one repo and no logic or data). A tick behaves exactly like a scheduled run: same gate
(`lifeos.platform.gate` skips it if a run started in the last 50 minutes), `PIPELINE` from `V7_PIPELINE`, `LIVE` from `V7_LIVE`. All pipeline code, state and secrets stay in this repo and GitHub Actions.
Still no LLM-hosted scheduler. The timer can be replaced by any other HTTP cron without a code change.

## D32 — Scale-Up web acquisition is scheduled; the unused v7_runs table is retired (Jim, 2026-10-01)
- Step 5c (`lifeos.run web-scale-up --limit 12`) now runs in every scheduled run (`PIPELINE`), not only on dispatch. It uses no TinyFish budget (public ATS boards only); a failure is counted by the existing failed-stage check.
- `v7_runs` was created by the first schema and never read or written. It is removed from the schema and from `store.TABLES`. An existing empty `v7_runs` in Hostinger is left in place (nothing references it); drop it by hand when convenient.

## D33 — The 17 Scale-Up sponsors with their own careers page are readable (Jim, 2026-10-01)
Ported the proven first-party readers from the V2 Scale-Up acquirer into `lifeos/sources/web/html_readers.py` (static pages and JSON-LD, WTTJ/Stream/Popsa/JOIN/Rippling paths, Bluestonex, Six & Flow, Futuristic portal,
Doubleword bundle array, Revolut `__NEXT_DATA__` with an exhaustive count check, TG0, Veramed cards). A reader that cannot prove the inventory raises, which the lister reports as FAILED `bad_shape`
(never zero jobs, never a removal); an empty page is a COMPLETE zero only for the self-proving readers or when the registry row carries a `zero_marker` phrase (Intrepid). The 17 rows moved from `bespoke` to `ready`
(36 of 48 now listable). Not done: the 12 sponsors with no discoverable ATS (LinkedIn/Indeed company pages) stay `fallback`/DEGRADED. These readers have not yet seen the live pages from the runner:
the first runs may report some as FAILED `bad_shape`; the counts say which.

## D34 — The remaining 12 Scale-Up sponsors: discovery first (Jim, 2026-10-01)
Otto Car and Truvi publish their own job pages, so they move to `static_complete_html` (38 of 48 listable; a page that is not a provable list simply reports FAILED `bad_shape`). The other 10 have only LinkedIn/Indeed
company pages, a team page or a homepage: LinkedIn company jobs need a login (never used) and Indeed is bot-walled. `probe` now includes `discover_scale_up`: free TinyFish Search per sponsor, printing only `kind:slug`
for a board V7 can already list, `own:<host>` for the sponsor's own site, or None. Results decide which rows get a real board; nothing is added automatically.

## D35 — Lensa: one search per job and a 15-minute cap (Jim, 2026-10-01)
Run 92's Lensa log (old code): 250 rows in 32 minutes, 89 resolved. Each row ran TWO searches (ATS domains, then any site) while the run budget counted ONE, so the real use was up to 2x the stated 300 (a risk to the
500/hour allowance) and misses (143 of 250) cost the full two calls. Employer-site hits were 79 of the ~100 search hits. `search_match.find` now makes one unfiltered search per job and judges each result by
the rule for its own kind (ATS domain: title + company; employer page: also the company in its host). Lensa also stops starting batches after 15 minutes (`LENSA_DEADLINE_MINUTES`): a run held the one-run-at-a-time queue
for 30+ minutes; the unfinished rows stay NEW for the next run. Expected: about twice the rows per minute and a run of roughly 15-17 minutes.

## D36 — Discovery for the Scale-Up sponsors with no job board (Jim, 2026-10-01)
The 10 remaining sponsors have no readable board (probe: nothing found for 4, aggregator pages only for 4, unverified domains for 2). `lifeos.sources.web.discover_jobs` (stage `discover-jobs`, step 5f in the Lensa job so it shares the
search pace; dispatch input `discover`) runs one free web search per sponsor, keeps results that are a single-job URL naming the sponsor with a readable role, and adds them as NEW candidates (lane Scale-Up, route evidence
`Scale-up:POSITIVE`, source `discover`, provider Public Web). They then take the normal final-link chain (employer ATS board match, one employer-site search, 40 searches per run); nothing is published without a final employer/ATS link and a full
description, so an aggregator-only lead stays unresolved and is never shown. Aggregator pages are never fetched or scraped. myvisajobs (a general list, not company pages) is not used.

## D37 — Published pages whose lane decision is EXCLUDE are trashed (Jim, 2026-10-01)
The Fit gate already keeps new EXCLUDE jobs out of Notion (EXCLUDED_FIT, never published). 11 pages published before they were scored showed Admission Status Excluded. With V7_FIT_GATE=true the audit stage now trashes a published page whose stored
decision is EXCLUDE, under the same human-state guard (Applied, Applied On, Saturn Decision, active hiring-pipeline match, unreadable or wrong target = left alone), marks the job EXCLUDED_FIT and keeps its description and ledger hash so it is never published again.
Notion keeps a trashed page for 30 days. A page with no Hostinger row or no stored decision is not touched.

## D38 — Shared runtime and redaction primitives; Interview handoff (Jim, 2026-10-01)
`lifeos/platform/runtime.py` (RunContext: run id, deadline, bounded timeout; ExecutionStatus/ExecutionResult with scalar-only detail) and `lifeos/platform/redact.py` (structured and free-text redaction; meeting passcodes, meeting ids, join tokens, Notion ids, emails, URL secrets)
are ported and trimmed from V2. Counts-only logs remain the primary control; redaction guards exception text and any diagnostic. The resolve stage's batch deadline is runtime's first consumer; Interview OS is the next.
`docs/INTERVIEW_HANDOFF.md` is the shared contract for the Interview track: ownership, hard rules, build order, the one open decision (how Derived content is produced).

## D39 — Interview Derived starts deterministic only (Jim, 2026-10-01)
INT-7.1B1 introduces deterministic identity and protection contracts only: no local or API model,
prep generation, production page creation, adoption, or mutation. Derived content may later use
extraction/templates. Any future approved model remains behind an Interview-owned adapter and
never becomes a platform dependency. Interview owns D39–D49.

Protected Live Notes and Raw Notes are never normalized or modified. Future mutation order:
target check → ownership/human-state guard → protected snapshot → write → direct read-back →
protected comparison. Ordinary probes never fingerprint protected content. Existing human and
unknown pages remain NO WRITE. B2 consumes B1 creation eligibility; B1 never executes it.

## D40 — Interview B2 machine creation is bounded and parent-first (Jim, 2026-10-01)
Existing HUMAN/UNKNOWN opportunities and rounds remain NO WRITE. Accepted confirmed interview
evidence may create a MACHINE opportunity only with a valid Hiring Pipeline target, complete
enumeration, deterministic identity, zero ACTIVE matches, and a unique verified Active Opportunities
page insertion surface. Inserting a new child page is the only permitted mutation of that human
container: existing blocks are never edited, moved, normalized, or deleted.
Rounds are created only beneath MACHINE opportunities. Parent and round creation use separate
passes, at most one page mutation per evidence item per run; every creation is marker-first with
authoritative readback and refreshed unique identity verification. Replay creates zero duplicates.
Live Notes and Raw Notes start empty and their human content is never written. Ambiguous,
incomplete, unreadable, HUMAN, UNKNOWN, and malformed states fail closed with zero writes.
MACHINE round identity is a versioned canonical machine identity paragraph immediately after the
ownership marker, outside protected notes; it is authoritative for identity and replay. Titles are
presentation only. Human/legacy rounds are never automatically retrofitted.

B2's insertion surface is exactly one verified child page named Active Opportunities directly
beneath Hiring Pipeline. The existing HUMAN Active heading/column coexists untouched. The
container is infrastructure, not an opportunity; legacy HUMAN Active pages remain in the same
identity universe for duplicate/ambiguity protection. MACHINE opportunities are created only
beneath the dedicated container, never directly under Hiring Pipeline.
Pages outside recognized Active/Retired regions are preserved but excluded from Interview identity
resolution and do not make enumeration incomplete.

## D41 — Interview B3 Derived is deterministic, bounded, and machine-owned (Jim, 2026-10-02)
Derived contains machine-generated content only and never inferred facts. B3 accepts explicitly accepted private evidence in caller-supplied priority order; carry-forward follows current items and fills only remaining section capacity. B3 never uses or summarizes Live Notes or Raw Notes as derivation input; those regions may be read only by the protected snapshot/comparison guard surrounding an authorized live mutation, and B3 never writes them. Only MACHINE rounds may receive Derived content; HUMAN and UNKNOWN rounds remain NO WRITE. B3 appends once. A v2 Derived marker commits separately to the exact normalized current prep evidence and the complete rendered payload. Replay validates both commitments and returns before carry-forward, so later changes to earlier rounds do not alter an existing Derived payload. Any change to current accepted evidence fails closed. Unexpected content, including a v1 marker, fails closed without rewrite. Notion cannot page from a heading, so the Derived read streams the round's blocks, drops those before the Derived heading unread, and uses its own call budget. Any failure after the append is a readback failure. Future model use remains behind the Interview-owned adapter rule in D39.

## D42 — Interview Advisor is source-grounded, model-mediated, preview-first
The Interview Advisor is a private planning layer upstream of B3. It applies the accepted Straight Line and Game Theory doctrine to one specific opportunity/round using only explicitly accepted, versioned source material. The model has no write authority and remains behind the Interview-owned adapter required by D39. Model output is untrusted until deterministic validation and compilation.

Candidate accomplishments and metrics are authoritative only through immutable versioned evidence references; the model selects evidence references and never authors canonical candidate evidence. Live Notes and Raw Notes are never Advisor derivation input. Explicit Guidance and Accepted Signals are separate versioned inputs.

The Advisor is preview-first. Generation does not commit to the interview round. Existing B3 remains the append-once commitment boundary for compiled PrepEvidence. The four B3 fields contain bounded conclusions, not the full Advisor analysis.

Runtime private material will live outside the public repository. Raw private corpus material is never committed to this repository. Real model transmission requires Jim's explicit acceptance authorization.

## D43 — Advisor private store is bounded; previews are immutable and insert-only
Interview Advisor runtime doctrine, candidate profile, evidence, Guidance and Accepted Signals live under one explicitly configured private Notion root shared with the Interview integration. The runtime never searches the workspace globally and never treats arbitrary Notion content as Advisor input. The store is fail-closed: exact root identity, exact required child surfaces, source markers, version metadata and corpus hashes must validate before a current bundle may be assembled.

The root comes only from INTERVIEW_ADVISOR_ROOT_PAGE_ID. The text namespace starting `v7-interview-advisor` is reserved: only each page's exact first-block marker and its one exact metadata line may use it, in any block type, and an unexpected child database under a controlled parent fails closed.

The private root also reserves one dedicated Advisor Queue container for the ChatGPT-native Advisor handoff. INT-ADV-2 defines only the container identity; queue item semantics are deferred to INT-ADV-3.

Advisor Preview creation is deterministic, immutable and insert-only beneath the dedicated Advisor Previews container. A preview commits to the exact AdvisorInputBundle digest and to the complete rendered preview body. Preview creation requires the one-attempt Notion transport and refuses to run without it; only a response that proves rejection (HTTP 400, 401, 403, 404, 409 or 429) is reported as a write failure, and every other failure after the POST begins is reported as an uncertain read-back failure and never retried. Preview creation performs authoritative read-back and never writes Hiring Pipeline, B3 Derived, Live Notes or Raw Notes. Approval and B3 commit remain separate future actions.

The v1 Advisor does not require or authorize a paid model/API provider. ChatGPT-native reasoning is external to this deterministic store/preview boundary.

## D44 — Advisor Queue is an immutable request/response handoff; state is derived
The Advisor Queue is append-only. LIFE OS is the sole v1 writer of Advisor Request pages. A request freezes one exact validated AdvisorInputBundle; its request ID is the first 32 lowercase hexadecimal characters of that bundle's bundle_digest. The request payload is the complete deterministic bundle snapshot, not references to mutable current store state.

The future ChatGPT-native Advisor is the sole v1 writer of Advisor Response pages. A response is a child of exactly one request and contains only the structured AdvisorDraft handoff. ChatGPT copies the request ID and bundle hash supplied by the request; it is never asked to calculate a cryptographic hash. The response is untrusted until LIFE OS parses it, validates every source/evidence reference against the frozen request bundle, and successfully runs compile_prep().

Queue state is derived from immutable structure, never stored or updated: a valid request with no response is READY; exactly one valid response is RESPONDED; malformed, unexpected or multiple response children are AMBIGUOUS. PREVIEWED is derived only when an existing VerifiedPreview has generation_id equal to the request ID, the same bundle hash, and PrepEvidence equal to the validated response compilation.

INT-ADV-3 does not schedule ChatGPT, modify workflow/run.py, create live Notion content, write responses, commit B3, or authorize any paid model/API.

## D50 — One shared name normalizer; the Interview job is isolated in the workflow (2026-10-01)
`lifeos/platform/names.py` now holds the normalizer and the company/role/title rules (moved from `jobs/fit/profile.py`, `jobs/names.py` and `jobs/hiring_pipeline.py`, behavior unchanged, old names re-exported); Interview OS is the second consumer.
`hourly.yml` gains a dispatch-only `interview` job with only `NOTION_INTERVIEW_TOKEN` and `HIRING_PIPELINE_PAGE_ID` (every Jobs secret blanked, enforced by a contract test); its steps use `continue-on-error` and a warning so an Interview failure cannot fail the Jobs run
(the gate and the stale-pipeline alert are keyed on run success). Decision numbers: Interview D39 to D49, Jobs D50 and up.

## D51 — Revolut is fetched as Chrome (2026-10-01; verified on the runner: 363 jobs, was blocked)
Revolut's careers page refuses plain scripted requests from the runner. A registry source may set `impersonate` (plus `warm_url`, `alt_urls`): `lifeos/platform/impersonate.py` then fetches it with `curl_cffi` (Chrome TLS fingerprint), warming a session on the home page first, 3 tries, accepting only a 200 that carries `__NEXT_DATA__`. This is the V1 mechanic, scoped to one source; every other source keeps the plain fetcher.
A blocked or ambiguous page is still FAILED, never zero jobs (D33). `curl_cffi` is pinned in `requirements.txt`.
Tried and rejected on the runner (probe, same day): Futuristic still refuses a Chrome-style fetch (401; plain 403) and Otto Car still returns 404, so they need a real browser or another route; Truvi's page loads (200) but exposes no job links the generic reader recognises. All three stay FAILED (never zero jobs); the flag is set on Revolut only.

## D52 — A watchdog outside the pipeline raises the stale-pipeline alert (2026-10-02)
The gate only runs when an hourly run starts, so a total stall (no GitHub schedule and no timer tick) was silent: on 2026-10-01 the outside timer was set to once a day and nothing ran for about 3 hours with no alert.
`watchdog.yml` (twice an hour, no secrets, `issues: write` only) runs `python -m lifeos.platform.gate --watch`: it opens the `pipeline-stale` issue when no hourly run has succeeded for 150 minutes and closes it when one has. It is an alarm, not a scheduler: it never runs the pipeline (D25/D30 stand), and a contract test pins that.

## D53 — One alerts layer in the platform, with a phone push (2026-10-02)
`lifeos/platform/alerts.py` is the single place that decides what is worth telling a person: an `Alert` (stable key, severity PAGE or INFO, counts-only text, one next step), `reconcile` (open once, remind every 6 h for PAGE and 24 h for INFO, resolve once) and sinks (phone push through ntfy). Text is redacted before it leaves; nothing but counts ever reaches a public log or a push.
Detectors live in the OS that owns the facts (`lifeos/jobs/alerts.py`): nothing published in 24 h, sponsors failing 3+ runs, jobs stuck over a day, a credential expiring (`lifeos/jobs/expiries.json`). State is in `v7_alerts` so a reminder is not a repeat; a failed push is retried next run.
Two layers: the detectors run at the end of the `finish` job, and the secret-light watchdog (D52) stays as the independent "nothing ran at all" alarm and also pushes. The `report` job pushes when a lane fails. The push topic (`NTFY_TOPIC`) is the only new secret and is never in the repo. An alert failing to send never fails a run.
Not built yet (each is one detector or one sink): Notion alerts view, GitHub issue per alert, Interview OS alerts (the TL specifies which).

## D54 — Provider independence; Jira is read and rolled by V7 directly (2026-10-02)
V7 must run correctly with ChatGPT (and any chat connector) completely unavailable: external systems (Jira, Microsoft Graph, Gmail, Google Calendar) are reached by V7's own integrations, and ChatGPT is an optional client of capabilities V7 already owns, never a dependency. Dependency direction is provider API -> V7 integration -> core -> product modules -> persistence/presentation -> ChatGPT (optional).
Jira: `platform/jira.py` is a stdlib REST client (Basic auth with an API token; fixed error codes only, reads retry, a write retries only when idempotent). `lifeos/jira/` is the Jira OS: `snapshot` reads each configured board (`JIRA_BOARDS`, a secret) into one private database row per project (`v7_jira_snapshot`; ticket data never enters this public repo or its logs), and `rollover` ports V1's weekly rollover: dry run by default, one active sprint (or one just-ended future sprint for catch-up), Monday target week, create-or-reuse the next sprint, carry-forward verified before the old sprint closes, every state change read back, never before the sprint's end. Logs are counts and fixed codes. Both stages are manual-dispatch only until a dry run is accepted; `card` (Phase B) renders the saved snapshot into the one Daily Report block V7 owns (the "JIRA Execution" callout, named by a secret): V1's buckets over actionable leaves, a stale snapshot only flips the status line to STALE, add-then-remove writes with read-back, refusal on any block that does not open with the heading. Scheduled and tick runs now refresh the snapshot and the card (live only when V7_LIVE is true, like every stage); scheduled rollover (`jira-rollover-scheduled`) acts only once a project's sprint has ended and it is Monday 6 AM local (like V1), is a quiet no-op every other hour, and skips `:readonly` boards; the manual `rollover` option is unchanged. Epic parents are labelled in the card's group headers.

## D51 amendment — Futuristic needs the announced-bot header; Otto Car and Truvi move to fallback (2026-10-02)
Tried on the GitHub runner (probe, counts only): a real headless Chromium and a Chrome-fingerprint fetch were both refused by Futuristic (401/403), but V1's way works: a plain request that announces itself (`LIFE-OS-Scale-up/1.0`, registry flag `announced_ua`, `lifeos/platform/egress.py`) returns the page and the sponsor's own reader finds its vacancies. Lesson: some sites refuse a browser-looking agent that fails a real browser's fingerprint, but serve an announced bot.
Otto Car's old Teamtailor board is dead (404 for every agent; V1 recorded the same) and the free search only finds an unrelated company with a similar name. Truvi's careers page loads but is a marketing page with no job list (V1 same). Both are `fallback`: DEGRADED, never zero jobs; their jobs still arrive through the aggregator lanes and discovery. A source that can never succeed must not sit in `ready`: it would trip the "sponsors failing 3+ runs" alert (D53) daily.
Also tried: Jina Reader (403) and Wayback (rate limited, and a snapshot is not live). Not tried, by decision: fetching through the Hostinger server with the database SSH key (that key is for the database tunnel only).
Ready sponsors 36, fallback 12. The headless-browser fetch was reverted (no consumer).

## D55 — Outlook is read by V7 directly through Microsoft Graph (2026-10-02)
Phase C of D54. `platform/outlook.py` is a stdlib Graph client: delegated OAuth (device-code sign-in once, then silent refresh), `Prefer: IdType="ImmutableId"` on every call, bounded full enumeration that raises rather than return a partial census, throttling retry, fixed error codes only (no URL, token, address or body). Read-only scopes (`Mail.Read`, `Calendars.Read`, `offline_access`); cleanup and any write need their own, later consent. Refresh tokens are stored in the private database (`v7_outlook_token`, one row per account label) and replaced whenever Microsoft rotates them; they are never a GitHub secret or a log line. One app registration serves both mailboxes (personal Microsoft account and the work tenant). Sign-in and health check live in their own manual workflow (`outlook.yml`): the hourly workflow is at the 25-input limit, and mailbox credentials never share a job with job-search secrets. Classification, newsletter ingestion and the calendar bridge are consumers added in later changes; the adapter only fetches.

## D56 — Outlook invites are added to the person's own Google calendar, one way (2026-10-03)
Phase D of D54. `calendar_bridge` reads the Outlook calendar through the Phase C adapter (`Calendars.Read`, occurrences expanded, UTC) and adds each event to the Google calendar the person already uses. Auth is a Google service account that has been given edit access to that one calendar (no consent screen, no expiring refresh token; signing uses the system `openssl`, the key touches disk only in a private temp file). Because it writes into a real calendar, ownership is strict: every event V7 writes has a deterministic id (hash of account label, iCalUId and occurrence start) plus a private marker and content hash, so re-runs never duplicate and edits update in place; V7 only ever edits or deletes events carrying its marker. An invite that already reached Google another way (matched by iCalUID and start) is left alone, so nothing is added twice. A cancelled or declined Outlook event removes only V7's own copy, and a guard refuses to delete more than half of V7's copies at once (minimum 5). Output is counts only. The workflow (`calendar.yml`) is manual with a dry-run default; scheduling is a later change. The Outlook token table moved to `platform/outlook_tokens.py` because several operating systems read it.

## D58 — The ChatGPT hourly task is a router of independent modules; Daily Report regions have exactly one owner (2026-10-03)
Two independent schedulers exist and stay independent: V7's own hourly workflow (V7-owned execution, no ChatGPT dependency, unchanged) and the ChatGPT-native `LIFE OS Daily Runs` task (hourly, outside GitHub, for reasoning and synthesis; it orchestrates only ChatGPT-owned modules such as Interview Advisor and the Daily Command Center, and is not a replacement for V7 runtime work). `lifeos/platform/router.py` is the whole contract, with no scheduler, queue, datastore, framework or new workflow.
- **Modules:** each returns `PASS`, `NO_ACTION` or `DEGRADED`; one that raises or returns anything else is `DEGRADED`; none ever stops another; the router returns one compact per-module summary line and an overall outcome (`DEGRADED` if any, else `PASS` if any, else `NO_ACTION`). Duplicate module names are refused.
- **Daily Report regions:** a region is a callout whose first child heading names its owner. `JIRA Execution` is owned exclusively by V7 (it is `lifeos/jira/card.py`'s `CARD_TITLE`, kept in step by a test). `ChatGPT · Daily Command Center` is the single region the Daily Command Center module may write. Anything else, including any unmarked region, is owned by no module, so a write that would touch it fails closed.
- **Daily Command Center rules:** it works only from accepted, current evidence (stale or failed source evidence is `DEGRADED`, never fresh); it resolves its one region by heading and refuses a missing or ambiguous target (it never creates one, and never recreates a missing V7 block); it prefers block-local updates over page replacement; it never mutates, dispatches or triggers Jira; and after a write, every region owned by someone else must be exactly as before (digest compared) or the write is treated as failed.
- Interview OS, Jobs OS and the workflows are untouched.

## D57 — Outlook job newsletters feed Jobs OS and are filed into a J Newsletters folder (2026-10-03)
Part of D54, modelled on V7's own Gmail pipeline (sweep, then ingest). `sources/newsletters/outlook.py` reads the Outlook inbox through the Phase C adapter and reuses the Gmail senders, parsers and Jobs OS intake unchanged. Like Gmail's sweep, mail from a known job-alert sender is moved from the Inbox into a top-level `J Newsletters` folder; Dice alerts (`dice@connect.dice.com`) and Reed alerts are filed the same way but wait for a parser. Dice Private Email (`user.dice.com`, relayed staffing recruiters) and every other sender are human mail and are never touched. Nothing is ever deleted (V1 deleted accepted Outlook alerts; the person chose a folder), every move is read back, and a per-run cap bounds the blast radius. Moving needs a separate, explicit write sign-in (`auth-write`: `Mail.ReadWrite`); without it the stage still reads and ingests from the Inbox and reports `write_denied`. A message is recorded as seen (table `v7_outlook_mail_seen`, keyed by a short hash of the immutable id, which survives the move; the source table's id column holds 40 characters) only after its cards are saved; a parser gap stays unseen and is retried; stale or non-job mail is closed without being opened. Counts only. It runs on every scheduled and tick run as a step of the `outlook` job (renamed from `calendar`: it also holds the calendar sync), isolated like Jira and Interview; manual runs use the outlook workflow's `newsletters` action. Refresh requests no longer name a scope, so a sign-in keeps exactly what it was granted. Recruiter mail is deliberately out of scope for now.

## D61 — Dice and Reed Outlook alerts fail closed on parser misses (2026-10-03)
Outlook Dice alerts use only non-decoy text links on `elinks.dice.com/a/sc/`; image links are ignored. Company, location and posting date are read from the flat paragraph stream following the title link; repeated tracking links collapse on normalized company/title identity while each source URL remains recorded. Alert wording without cards is a parser miss and remains unseen. Reed job sender mail with no cards also remains unseen. Reed course mail is filed only; Dice Private Email hosts (`user.dice.com`, `recruiter.dice.com`) remain untouched. The Dice fixture mirrors the supplied synthetic HTML shape. Reed has no real sample and its parser remains unverified. Follow-up: resolve Dice `elinks.dice.com/a/sc/` tracking URLs to their Dice job pages.

## D62 — Dice tracking redirects use anonymous bounded resolution and fail closed
The Dice resolver attempts read-only HTTPS GETs through `platform.http.fetch`, with no login or cookies. Only a final `dice.com` or `www.dice.com` job-detail path is accepted and normalized; errors stay pending, while a 429 stops further requests for the run. Pacing is one second per request, capped at 40 per run, using the shared resolver deadline. The scheduled/tick workflow runs it without adding a dispatch input; manual runs use `python -m lifeos.run resolve-dice`. GitHub runner access to a real tracking link remains unverified and needs a dry run.

## D63 — Bills are read as a complete private snapshot
The Bills OS reads every Bill Tracker data-source page through the official Notion API, including evaluated formula values, and saves a single complete snapshot to the private database only when live. A failed or incomplete Notion read cannot replace the previous snapshot. Due-state is calculated from the saved snapshot using the America/Chicago date; the hourly Bills job is isolated and non-blocking, and manual runs use the separate `bills` workflow. Notion access and real Bill Tracker property shapes remain unverified until the read-only workflow is configured and run.

## D64 — V7 owns the Calendar Daily Report callout
The Agenda OS reads the main Google Calendar through the read-only service account, saves the complete Chicago today/tomorrow window privately, and renders only from that snapshot. The Calendar callout uses the existing Daily Report integration, is guarded by router ownership and protected-region read-back, and is refreshed on scheduled/tick runs; manual runs use the separate `agenda` workflow. A stale snapshot changes only the status line. ChatGPT must not write the V7-owned Calendar callout.

## D59 — Manual runs never block a tick (2026-10-03)
The hourly workflow now sets `run-name` to `tick` for scheduled and timer runs and `manual` for everything else; the gate counts only `tick` runs when deciding to skip. A manual run used to cancel the next hourly tick (it started within 50 minutes). Manual and tick runs may now overlap in time; the workflow's concurrency group queues them. The first tick after this change may not be skipped by an older run, which was titled `hourly`.

## D60 — One HTTP retry helper, one stage registry, shared test fakes, one Python setup (2026-10-03)
Consolidation with no behaviour change. `platform/rest.py` is the single retry/backoff/fixed-error-code loop for the Jira, Google Calendar and Outlook clients (Notion and Gmail keep their own: Notion paces and retries only 429, Gmail has form and auth variants). `lifeos/run.py` declares each stage as one `lazy("module", "function")` line (214 to 112 lines) and a test proves every stage resolves to a real function. `tests/kit` now holds the shared network fakes (`Response`, `http_error`) and a block-store fake for region writers; new fakes go there. The Python and requirements setup is a local composite action, declared once. A contract test fails if a new workflow-level secret is not blanked in the Interview, Jira and Outlook jobs. Not done: a reusable workflow for the isolation blocks (cannot be exercised outside Actions; the contract test guards the drift instead). The router (D58) is for Daily Report regions, not pipeline stages, and stays separate.

## D65 — Amazon order mail is filed only after canonical Notion read-back (2026-10-03)
V7 accepts only the three allowlisted Amazon lifecycle senders with exactly one order ID in the message body, reconciles their events monotonically into the existing Amazon Orders data source, and files each accepted Gmail message only after the persisted row reads back equal. Conflicts update only Status and Needs Review; unsupported or ambiguous messages remain in the Inbox. Amazon Orders uses the existing Gmail `gmail.modify` grant and a separate database-scoped Notion integration; scheduled/tick and manual runs report counts only.

## D68 — A rejected link costs an attempt, and the rejection is counted by reason (2026-10-03)
Enrich takes a RESOLVED job back to NEW when the page at its link is not that job (a listing, a template, a different title). That rejection did not count as an attempt, so a resolver that kept returning the same wrong link made the job bounce NEW, RESOLVED, NEW every hour without end (the 07:00 run rejected 43 of 48). A rejection now adds one resolve attempt, and at the resolver cap the job parks on HOLD (visible, not retried). The enrich line also reports `mismatch`: counts by reason, by producer (lensa, linkedin, jobright, dice), by site family, and how many were rejected for the same reason last time. Counts only: no title, company or URL.
## D67 — One snapshot store; Bills "stale due" uses the contract's supported cycles (2026-10-03)
`platform/snapshot_store.py` owns the private snapshot table (schema, replace, read-back); Jira, Bills and Agenda use it instead of three copies, and the Bills/Agenda test fakes became one shape. Bills' bucket is renamed from "overdue" to `stale_due`, V1's name for an Active, unpaid, recurring bill whose Due Date has passed, because most of those rows are bills that were paid but never rolled forward, not late bills. Recurring means exactly the contract's nine cycles (Weekly, Bi-Weekly, Monthly, 45 Days, 60 Days, 90 Days, Quarterly, 180 Days, Yearly); "4 Years", Lifetime, One Time, any cycle added later and a missing cycle are never processed or counted automatically.
## D66 — Calendar callout uses its configured ID and protects JIRA (2026-10-03)
The configured `CALENDAR_CARD_BLOCK_ID` is the single authority for the Calendar region, following D58's one-owner-per-region rule; V7 does not scan the Daily Report tree. The target may be nested and must be a callout whose first child is an exact `Calendar` heading_3 or heading_4. V7 keeps that heading and replaces only subsequent children. Before writing, and after each append/delete and at completion, it reads the full configured JIRA callout tree by `JIRA_CARD_BLOCK_ID` and requires it unchanged. The ID is an Actions secret value, not a credential.

## D69 — Domain jobs move to their own workflow, in two stages (2026-10-03)
Jira, Outlook, Bills, Agenda and Amazon each touch one provider and none touches the Jobs pipeline, but they lived in `hourly.yml`, which declares every Jobs secret at the top and so had to blank about 25 of them in each isolated job. `domains.yml` declares no secrets at the top, so a job can only see what its own steps pass it (isolation by construction), and a contract test pins each job's allowed secrets. Stage 1 (this change): `domains.yml` is manual-only (`all` runs every domain job like a tick, `jira` picks one Jira mode, `live` writes; the jobs still also run from `hourly.yml`, so nothing is duplicated automatically). Stage 2, after a manual run proves it: add the `workflow_run` trigger (after `hourly`, skipped when the gate cancelled the tick), delete the five jobs and their blanking from `hourly.yml`, and retarget the isolation test. Domain jobs then run a few minutes after the Jobs lanes instead of beside them, and a failed Jobs run no longer blocks them.

## D70 — HOLD jobs are excluded after seven days (2026-10-03, Jim)
A newsletter mail closes only when every job from it has an outcome, and HOLD is not one: nothing retries a HOLD job, so each held job kept its mail open forever (82 mails at the 07:00 run, none closed). After seven days on HOLD (measured by `updated_at`, which a repeat sighting does not touch) a job becomes `EXCLUDED_UNRESOLVED` with reason `hold_expired`: terminal, never published, never retried, and the finalize stage then closes its mail. Stage `expire-holds` runs before finalize on every tick, dry run unless live, counts only (`on_hold`, `due`, `excluded`). A rejected link now costs an attempt (D68), so a link that keeps failing reaches HOLD and then this exit instead of looping.

## D71 — A link that cannot be one vacancy is never RESOLVED; holds and board failures say where (2026-10-03)
Enrich opens every RESOLVED job with a deterministic link test (`quality.link_problem`: a root or listing URL, a missing job id, a Workable link without its account) and rejects the job if the link fails it. The resolve stage now applies the same test before it writes RESOLVED, so such a link is `bad_link_<code>` and pending, counted as an attempt, instead of resolving and being rejected an hour later. What stays in Enrich is the part that needs the page: the title and description actually belong to this job. Diagnostics: `expire-holds` also reports the HOLD jobs by reason code and by producer, and a Scale-Up run lists the failing boards by ATS family and by public registry id (the source list is committed, so an id is public; no job data). The precision guards in Enrich and Audit stay as strict as before.

## D72 — Stage 2: the domain jobs run from domains.yml after each tick (2026-10-03)
The five domain jobs (Jira, Outlook, Bills, Agenda, Amazon) are deleted from `hourly.yml` (800 to 411 lines, and the `jira` dispatch input is freed: 24 of 25 used). `domains.yml` now also runs on `workflow_run` of `hourly`: only when that run was a TICK (run-name `tick`, never a manual hourly run) and was not cancelled by the gate. They start a few minutes after the Jobs lanes finish, whatever the lanes' result: a failing Jobs lane no longer blocks Jira, the Calendar card or Amazon, but a tick whose unit tests failed still triggers them (the code under test is the same code). If the trigger ever stops firing the domain jobs go quiet without an error: a manual `domains` run (input `all`) is the fallback, and the first tick after this change is checked by hand. The Interview job stays in `hourly.yml` (dispatch-only; it still blanks the Jobs secrets there, guarded by its own tests). The isolation tests moved with the jobs: `domains.yml` declares no secrets at the top and a contract test pins each job's allowed secrets.

## D73 — The Calendar card waits for the Jira card (2026-10-03)
The first live tick after stage 2 showed `AGENDA_PROTECTED_REGION_UNAVAILABLE`: the `agenda` and `jira` jobs ran in parallel, and the Calendar card read the Jira region while the Jira job was replacing its blocks. The write was refused (the guard worked), but it would have recurred every tick. `agenda` now has `needs: jira` with `always()`, so it still runs when Jira is skipped or failed, and the two never write the same page at once.

## D74 — The Jobright resolve line says why jobs stay pending (2026-10-03)
After PR 91 the 18:00 tick resolved 6 of 40 Jobright jobs and left 34 pending, with no way to tell a correct refusal from a resolver that never reaches the apply link. The resolve stage's live path now counts every result under a fixed reason (`why`): the outcome code without its detail (`apply_unavailable`, `target_timeout`, `auth_unavailable`, ...), `bad_link_<code>` when the landing failed the link test, or `landed:<kind>`. Counts only; no urls or titles. Resolution rules are unchanged. The dry run uses the same test and reports it, and the `resolve-jobright:` line also gives `refused`: the hosts of refused landings and the NAMES of their query parameters (never paths or values). Suspected cause under test: `quality.url_problem` reads an id only from the path, so an employer page such as `/careers?gh_jid=123` (a real single vacancy that carries its id in the query) is refused as a listing.

## D75 — A job id in the query string counts as a job id (2026-10-03, Jim)
`quality.url_problem` read a job id only from the URL path, so a real vacancy such as an employer's `/careers?gh_jid=123` (Greenhouse embedded on the company's own page) was refused as a listing, in the resolve stage, in Enrich and in Audit alike. A query parameter that names a job (`gh_jid`, `jid`, `job_id`, `req`, `id` and the like) now counts when its value has a digit and at least three characters. A search or paging parameter (`q`, `page`, ...) still makes the URL a listing, whatever else it carries, and a bare `?utm=123456` is not an id. One function feeds every caller, so the stages agree. Whether a still-ambiguous link should be fetched and judged by its page title instead of refused by shape is a separate question, sized from the diagnostics. Dry run 193 (40 Jobright landings, 36 refused) showed the real shapes: Greenhouse embeds (`boards.greenhouse.io/embed/job_app?for=<board>&token=<job id>`, 11 of the refused, a form Enrich already reads through the Greenhouse API), ADP and similar (`reqId`, `jobId`), Eightfold (`pid`). `token` and `pid` are too common as names to trust anywhere, so they count only on `greenhouse.io` and `eightfold.ai` hosts (`HOST_ID_PARAMS`).

## D76 — An ambiguous link shape is proven by the page's title, not refused by its URL (2026-10-03, Jim)
After D75, 18 of 40 Jobright landings were still refused: staffing and enterprise portals (iCIMS, JobDiva, TCS iBegin, RippleHire, Njoyn) whose job id is not visible in the URL. Jobright reads that link off the job's own page ("Original Job Post" or the page data), so its provenance is direct, and the Jobright title is the identity to prove. Three coordinated changes, one concept: (1) the resolve stage accepts a landing whose shape is ambiguous (`quality.AMBIGUOUS`: `listing_url`, `no_job_id`) only when `via` is `original_post` or `page_data`, marks the job `link_proof='unproven'` and RESOLVED; a bare root, a Workable link without its account, and any landing reached by clicking stay refused. (2) Enrich fetches an `unproven` job and applies a STRICT title check: the page's own title (JSON-LD title, ATS API title or `<title>`) must carry at least 80% of the Jobright title words, and the body does not count (a portal lists many titles); the usual description, form and listing checks still apply. Pass sets `link_proof='title'`; failure returns the job to NEW as a counted attempt, clearing the mark. The rendered fallback keeps the strict check. (3) Audit excuses an ambiguous shape only for `link_proof='title'` jobs; a root is never excused. Other sources are untouched: only Jobright produces direct provenance. The migration adds `v7_jobs.link_proof`.
