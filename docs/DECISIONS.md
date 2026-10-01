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
Every provider limit is a constant in `pipeline/limits.py`, documented in `docs/LIMITS.md`, and enforced by tests. Stages
stop on the first 429/403.

## D16 - Private evidence stays out of the public repo (DECIDED)
Fit-model evidence and personal scoring corrections are never committed. `docs/FIT_MODEL.md` holds structure only.
